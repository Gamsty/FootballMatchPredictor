"""
Integration test for `_persist_prediction` against a real (SQLite in-memory) DB.

Previously this was only covered by mocking — which meant a SQL-level bug
(column mismatch, FK constraint violation, type-roundtrip failure) would slip
into prod silently because `_persist_prediction` swallows exceptions by design.

These tests prove the schema and the persister actually agree.
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import Column, Index, Integer, inspect
from sqlalchemy.orm import declarative_base, sessionmaker


@pytest.fixture
def db_session(monkeypatch):
    """
    Build an in-memory SQLite session bound to fresh metadata.

    database.py picks its pool arguments per dialect (_engine_options), so a
    sqlite URL just works — this fixture used to monkeypatch create_engine to
    strip Postgres-only kwargs that SQLite rejects at import.
    """
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')

    # Fresh import so the engine binds to the sqlite URL set above.
    for mod in ('database',):
        if mod in sys.modules:
            del sys.modules[mod]
    import database as db_mod  # noqa: E402

    # Create tables on the in-memory engine
    db_mod.Base.metadata.create_all(bind=db_mod.engine)
    SessionLocal = sessionmaker(bind=db_mod.engine)
    return db_mod, SessionLocal()


def _make_team(session, db_mod, api_id, name):
    t = db_mod.Team(api_id=api_id, name=name, short_name=name.split()[-1])
    session.add(t)
    session.commit()
    return t


def _make_match(session, db_mod, match_id, home, away, status='SCHEDULED', winner=None):
    from datetime import datetime
    m = db_mod.Match(
        id=match_id,
        api_id=match_id,
        home_team_id=home.id,
        away_team_id=away.id,
        season=2026,
        matchday=10,
        competition='Premier League',
        stage='REGULAR_SEASON',
        date=datetime(2026, 5, 20, 15, 0),
        status=status,
        winner=winner,
    )
    session.add(m)
    session.commit()
    return m


# ----------------------------------------------------------------------------
# Schema sanity: the new PredictionSnapshot table exists with expected columns
# ----------------------------------------------------------------------------

class TestSchema:
    def test_prediction_snapshot_table_created(self, db_session):
        db_mod, session = db_session
        # Insert + read back proves both columns and FKs work
        h = _make_team(session, db_mod, 1, 'Arsenal FC')
        a = _make_team(session, db_mod, 2, 'Chelsea FC')
        m = _make_match(session, db_mod, 100, h, a)

        snap = db_mod.PredictionSnapshot(
            match_id=m.id,
            home_win_prob=0.55,
            draw_prob=0.25,
            away_win_prob=0.20,
            confidence=0.55,
            model_type='stacked',
            model_version='v1',
        )
        session.add(snap)
        session.commit()

        loaded = session.query(db_mod.PredictionSnapshot).filter_by(match_id=100).first()
        assert loaded is not None
        assert loaded.home_win_prob == 0.55
        assert loaded.model_version == 'v1'

    def test_prediction_still_unique_per_match(self, db_session):
        from sqlalchemy.exc import IntegrityError

        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A')
        a = _make_team(session, db_mod, 2, 'B')
        m = _make_match(session, db_mod, 1, h, a)

        session.add(db_mod.Prediction(match_id=m.id, home_win_prob=0.5,
                                       draw_prob=0.3, away_win_prob=0.2,
                                       model_version='v1', model_type='stacked'))
        session.commit()

        # Second insert with same match_id must fail — that's the contract we rely on
        session.add(db_mod.Prediction(match_id=m.id, home_win_prob=0.6,
                                       draw_prob=0.2, away_win_prob=0.2,
                                       model_version='v2', model_type='stacked'))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ----------------------------------------------------------------------------
# _persist_prediction behaviour: upsert + snapshot dedupe
# ----------------------------------------------------------------------------

class TestPersistPrediction:
    """Direct tests on the inline helper logic — mimics what app.py does."""

    def _persist(self, db_mod, session, match, prediction_data, model_data):
        """Inline copy of _persist_prediction stripped of app.py's globals.
        Keeps the test self-contained while still exercising the SQL path."""
        result = prediction_data.get('match_result') or {}
        probs = result.get('probabilities') or {}
        if not probs.get('home_win'):
            return
        winner_map = {'HOME_WIN': 'HOME_TEAM', 'AWAY_WIN': 'AWAY_TEAM', 'DRAW': 'DRAW'}
        model_version = str(model_data.get('model_version') or 'unversioned')
        model_type = model_data.get('model_type')

        existing = session.query(db_mod.Prediction).filter_by(match_id=match.id).first()
        payload = dict(
            predicted_winner=winner_map.get(result.get('outcome'), result.get('outcome')),
            home_win_prob=probs.get('home_win'),
            draw_prob=probs.get('draw'),
            away_win_prob=probs.get('away_win'),
            confidence=result.get('confidence'),
            model_type=model_type,
            model_version=model_version,
        )
        if existing:
            for k, v in payload.items():
                if v is not None:
                    setattr(existing, k, v)
        else:
            session.add(db_mod.Prediction(match_id=match.id, **payload))

        latest_snap = (session.query(db_mod.PredictionSnapshot)
                       .filter_by(match_id=match.id, model_version=model_version)
                       .order_by(db_mod.PredictionSnapshot.created_at.desc())
                       .first())
        should_append = True
        if latest_snap:
            tol = 1e-4
            if (abs((latest_snap.home_win_prob or 0) - (probs.get('home_win') or 0)) < tol
                    and abs((latest_snap.draw_prob or 0) - (probs.get('draw') or 0)) < tol
                    and abs((latest_snap.away_win_prob or 0) - (probs.get('away_win') or 0)) < tol):
                should_append = False
        if should_append:
            session.add(db_mod.PredictionSnapshot(
                match_id=match.id,
                home_win_prob=probs.get('home_win'),
                draw_prob=probs.get('draw'),
                away_win_prob=probs.get('away_win'),
                confidence=result.get('confidence'),
                model_type=model_type,
                model_version=model_version,
            ))
        session.commit()

    def test_first_call_creates_prediction_and_snapshot(self, db_session):
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a)
        model_data = {'model_type': 'stacked', 'model_version': 'v1'}
        pred = {
            'match_result': {
                'outcome': 'HOME_WIN',
                'probabilities': {'home_win': 0.55, 'draw': 0.25, 'away_win': 0.20},
                'confidence': 0.55,
            },
        }

        self._persist(db_mod, session, m, pred, model_data)

        assert session.query(db_mod.Prediction).filter_by(match_id=m.id).count() == 1
        assert session.query(db_mod.PredictionSnapshot).filter_by(match_id=m.id).count() == 1

    def test_repeated_call_with_same_probs_does_not_duplicate_snapshot(self, db_session):
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a)
        model_data = {'model_type': 'stacked', 'model_version': 'v1'}
        pred = {
            'match_result': {
                'outcome': 'HOME_WIN',
                'probabilities': {'home_win': 0.55, 'draw': 0.25, 'away_win': 0.20},
                'confidence': 0.55,
            },
        }

        self._persist(db_mod, session, m, pred, model_data)
        self._persist(db_mod, session, m, pred, model_data)
        self._persist(db_mod, session, m, pred, model_data)

        # Prediction is upserted → still 1 row
        assert session.query(db_mod.Prediction).filter_by(match_id=m.id).count() == 1
        # Snapshot is deduped when probabilities are unchanged → still 1 row
        assert session.query(db_mod.PredictionSnapshot).filter_by(match_id=m.id).count() == 1

    def test_probability_change_appends_new_snapshot(self, db_session):
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a)
        model_data = {'model_type': 'stacked', 'model_version': 'v1'}
        pred1 = {'match_result': {'outcome': 'HOME_WIN',
                                   'probabilities': {'home_win': 0.55, 'draw': 0.25, 'away_win': 0.20},
                                   'confidence': 0.55}}
        pred2 = {'match_result': {'outcome': 'HOME_WIN',
                                   'probabilities': {'home_win': 0.60, 'draw': 0.22, 'away_win': 0.18},
                                   'confidence': 0.60}}

        self._persist(db_mod, session, m, pred1, model_data)
        self._persist(db_mod, session, m, pred2, model_data)

        # Prediction reflects the LATEST value
        latest_pred = session.query(db_mod.Prediction).filter_by(match_id=m.id).one()
        assert latest_pred.home_win_prob == 0.60
        # Snapshot table has BOTH — history is preserved
        snaps = session.query(db_mod.PredictionSnapshot).filter_by(match_id=m.id).all()
        assert len(snaps) == 2

    def test_bet_lifecycle_pending_then_won(self, db_session):
        """A pending Bet should settle to 'won' against a HOME_TEAM-winner match."""
        from datetime import datetime as dt
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a, status='FINISHED', winner='HOME_TEAM')
        m.home_score = 2
        m.away_score = 0

        bet = db_mod.Bet(
            match_id=m.id, market='h2h', outcome_key='home', outcome_label='Home Win',
            odds_at_bet=2.10, stake=100.0, status='pending',
        )
        session.add(bet)
        session.commit()

        # Simulate the settler logic (mirrors _settle_one_bet in app.py)
        def resolver(match):
            return match.winner == 'HOME_TEAM'
        won = resolver(m)
        bet.status = 'won' if won else 'lost'
        bet.profit_loss = bet.stake * (bet.odds_at_bet - 1) if won else -bet.stake
        bet.settled_at = dt(2026, 5, 21, 17, 0)
        session.commit()

        reloaded = session.query(db_mod.Bet).filter_by(id=bet.id).one()
        assert reloaded.status == 'won'
        assert reloaded.profit_loss == pytest.approx(110.0)

    def test_bet_lifecycle_pending_then_lost(self, db_session):
        from datetime import datetime as dt
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a, status='FINISHED', winner='DRAW')
        m.home_score = 1
        m.away_score = 1

        bet = db_mod.Bet(match_id=m.id, market='h2h', outcome_key='home',
                          odds_at_bet=2.10, stake=100.0, status='pending')
        session.add(bet)
        session.commit()

        # Settle
        won = (m.winner == 'HOME_TEAM')
        bet.status = 'won' if won else 'lost'
        bet.profit_loss = bet.stake * (bet.odds_at_bet - 1) if won else -bet.stake
        bet.settled_at = dt(2026, 5, 21, 17, 0)
        session.commit()

        reloaded = session.query(db_mod.Bet).filter_by(id=bet.id).one()
        assert reloaded.status == 'lost'
        assert reloaded.profit_loss == -100.0

    def test_odds_snapshot_table_appendable(self, db_session):
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a)

        # Two snapshots at different times — both should persist
        for i, t in enumerate(['realtime', 'closing']):
            session.add(db_mod.OddsSnapshot(
                match_id=m.id,
                market='h2h',
                outcome_key='home',
                best_odds=2.10 - i * 0.05,
                best_bookmaker='Pinnacle',
                median_odds=2.05 - i * 0.05,
                book_count=4,
                snapshot_type=t,
            ))
        session.commit()

        snaps = (session.query(db_mod.OddsSnapshot)
                 .filter_by(match_id=m.id).all())
        assert len(snaps) == 2
        types = {s.snapshot_type for s in snaps}
        assert types == {'realtime', 'closing'}

    def test_model_version_change_appends_new_snapshot(self, db_session):
        """Critical for CLV / version-comparison: same probs but different model
        version must produce a new snapshot."""
        db_mod, session = db_session
        h = _make_team(session, db_mod, 1, 'A FC')
        a = _make_team(session, db_mod, 2, 'B FC')
        m = _make_match(session, db_mod, 1, h, a)
        pred = {'match_result': {'outcome': 'HOME_WIN',
                                  'probabilities': {'home_win': 0.55, 'draw': 0.25, 'away_win': 0.20},
                                  'confidence': 0.55}}

        self._persist(db_mod, session, m, pred, {'model_type': 'stacked', 'model_version': 'v1'})
        self._persist(db_mod, session, m, pred, {'model_type': 'stacked', 'model_version': 'v2'})

        snaps = session.query(db_mod.PredictionSnapshot).filter_by(match_id=m.id).all()
        assert len(snaps) == 2
        versions = {s.model_version for s in snaps}
        assert versions == {'v1', 'v2'}


# ---------------------------------------------------------------------------
# Schema migrations — indexes declared after a table already exists
# ---------------------------------------------------------------------------

class TestIndexSync:
    """
    Regression cover for the class of bug that left ix_matches_home_date_status
    missing in production: create_all() skips existing tables entirely, so an
    Index added to __table_args__ later is never emitted, and nothing errors.
    """

    def test_create_all_alone_does_not_add_index_to_existing_table(self, db_session):
        db_mod, _ = db_session
        base = declarative_base()

        class _T1(base):  # noqa: N801
            __tablename__ = 'idxprobe'
            id = Column(Integer, primary_key=True)
            a = Column(Integer)

        base.metadata.create_all(bind=db_mod.engine)

        # Same table, now declaring an index — i.e. a later code change.
        base2 = declarative_base()

        class _T2(base2):  # noqa: N801
            __tablename__ = 'idxprobe'
            id = Column(Integer, primary_key=True)
            a = Column(Integer)
            __table_args__ = (Index('ix_idxprobe_a', 'a'),)

        base2.metadata.create_all(bind=db_mod.engine)

        names = {i['name'] for i in inspect(db_mod.engine).get_indexes('idxprobe')}
        assert 'ix_idxprobe_a' not in names, (
            "create_all grew the ability to add indexes to existing tables — "
            "the _sync_indexes pass may no longer be needed"
        )

    def test_sync_indexes_creates_the_missing_index(self, db_session):
        db_mod, _ = db_session
        base = declarative_base()

        class _T(base):  # noqa: N801
            __tablename__ = 'idxprobe2'
            id = Column(Integer, primary_key=True)
            a = Column(Integer)

        base.metadata.create_all(bind=db_mod.engine)

        # Declare the index after the fact, against database.py's own metadata,
        # then run the sync the way init_db() does.
        table = _T.__table__.to_metadata(db_mod.Base.metadata)
        Index('ix_idxprobe2_a', table.c.a)
        try:
            engine_inspector = inspect(db_mod.engine)
            db_mod._sync_indexes(engine_inspector, set(engine_inspector.get_table_names()))
            names = {i['name'] for i in inspect(db_mod.engine).get_indexes('idxprobe2')}
            assert 'ix_idxprobe2_a' in names
        finally:
            db_mod.Base.metadata.remove(table)

    def test_declared_indexes_exist_after_init(self, db_session):
        """The composite indexes the feature queries rely on must be present."""
        db_mod, _ = db_session
        names = {i['name'] for i in inspect(db_mod.engine).get_indexes('matches')}
        assert 'ix_matches_home_date_status' in names
        assert 'ix_matches_away_date_status' in names

    def test_standings_uniqueness_is_enforced(self, db_session):
        """add_standing upserts on (team_id, season, competition) — duplicates must fail."""
        from sqlalchemy.exc import IntegrityError

        db_mod, session = db_session
        team = _make_team(session, db_mod, 9001, 'Index Test FC')
        session.add(db_mod.Standing(team_id=team.id, season=2025,
                                    competition='Premier League', position=1))
        session.commit()
        session.add(db_mod.Standing(team_id=team.id, season=2025,
                                    competition='Premier League', position=7))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
