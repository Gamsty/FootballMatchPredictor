"""
Cover the closing-odds snapshot job.

This module had no tests at all, which is the wrong risk profile for what it is:
it runs unattended every 30 minutes against production, writes to two tables, and
is the sole source of the closing prices that /api/bets/performance turns into
CLV. A regression here shows up as a quietly wrong performance number, not as an
exception anybody sees.

`run_snapshot` already takes its database and odds client as arguments, so the
tests bind the real models to a private in-memory engine and hand it a stub
client — no global state, no network, no monkeypatching of module internals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _StubClient:
    """Stands in for OddsAPIClient. Records what it was asked for."""

    def __init__(self, odds_by_match=None, *, enabled=True, raises=False):
        self.enabled = enabled
        self._odds = odds_by_match or {}
        self._raises = raises
        self.calls = []

    def odds_for_match(self, competition, home, away, **kwargs):
        self.calls.append((competition, home, away, kwargs))
        if self._raises:
            raise RuntimeError("upstream exploded")
        return self._odds.get((home, away))

    def quota_status(self):
        return {'remaining': 400, 'used': 100, 'low': False}


def _bucket(best, median, bookmaker='pinnacle', count=7):
    return {'best': {'price': best, 'bookmaker': bookmaker},
            'median': median, 'count': count}


H2H_ODDS = {
    'h2h': {
        'home': _bucket(2.20, 1.90),
        'draw': _bucket(3.90, 3.60),
        'away': _bucket(4.60, 4.20),
    },
    'totals': {
        'over': _bucket(1.95, 1.85),
        'under': _bucket(2.05, 1.95),
    },
}


@pytest.fixture
def db():
    """A DatabaseManager-shaped object over a private in-memory database."""
    import database as d

    engine = create_engine('sqlite:///:memory:')
    d.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    teams = [d.Team(id=i, api_id=i, name=f'Team {i}') for i in (1, 2, 3, 4)]
    session.add_all(teams)
    session.commit()
    return SimpleNamespace(session=session)


def _match(db, mid, *, status='SCHEDULED', hours_ahead=1.0, home=1, away=2):
    import database as d
    m = d.Match(id=mid, api_id=mid, home_team_id=home, away_team_id=away,
                season=2026, competition='Premier League', status=status,
                date=_now() + timedelta(hours=hours_ahead))
    db.session.add(m)
    db.session.commit()
    return m


# ---------------------------------------------------------------------------


class TestSnapshotWindow:
    def test_writes_one_row_per_priced_outcome(self, db):
        from database import OddsSnapshot
        from odds_snapshot import run_snapshot

        _match(db, 1)
        client = _StubClient({('Team 1', 'Team 2'): H2H_ODDS})
        summary = run_snapshot(db=db, client=client, closing=True,
                               markets=('h2h', 'totals'))
        db.session.commit()

        rows = db.session.query(OddsSnapshot).all()
        assert summary['snapshots_written'] == 5      # 3 h2h + 2 totals
        assert len(rows) == 5
        assert {r.market for r in rows} == {'h2h', 'totals_2_5'}
        home = next(r for r in rows if r.outcome_key == 'home')
        assert home.best_odds == 2.20
        assert home.median_odds == 1.90
        assert home.snapshot_type == 'closing'

    def test_closing_run_ignores_matches_outside_the_window(self, db):
        from odds_snapshot import run_snapshot

        _match(db, 1, hours_ahead=1.0)      # inside a 2h closing window
        _match(db, 2, hours_ahead=9.0, home=3, away=4)   # tomorrow
        client = _StubClient({('Team 1', 'Team 2'): H2H_ODDS,
                              ('Team 3', 'Team 4'): H2H_ODDS})
        summary = run_snapshot(db=db, client=client, closing=True,
                               closing_window_hours=2.0)
        assert summary['matches_scanned'] == 1
        assert [c[1] for c in client.calls] == ['Team 1']

    def test_finished_and_cancelled_matches_never_cost_quota(self, db):
        """Dead fixtures used to be fetched anyway — pure waste on a 500/mo tier."""
        from odds_snapshot import run_snapshot

        _match(db, 1, status='FINISHED')
        _match(db, 2, status='CANCELLED', home=3, away=4)
        client = _StubClient({('Team 1', 'Team 2'): H2H_ODDS})
        summary = run_snapshot(db=db, client=client, closing=True)
        assert summary['matches_scanned'] == 0
        assert client.calls == []

    def test_realtime_run_uses_the_wide_window(self, db):
        from odds_snapshot import run_snapshot

        _match(db, 1, hours_ahead=9.0)
        client = _StubClient({('Team 1', 'Team 2'): H2H_ODDS})
        summary = run_snapshot(db=db, client=client, closing=False, hours=24)
        assert summary['snapshot_type'] == 'realtime'
        assert summary['matches_scanned'] == 1

    def test_disabled_client_is_a_no_op(self, db):
        from odds_snapshot import run_snapshot
        summary = run_snapshot(db=db, client=_StubClient(enabled=False))
        assert summary == {'enabled': False, 'message': 'ODDS_API_KEY not set'}

    def test_a_failed_fetch_does_not_abandon_the_rest_of_the_slate(self, db):
        """One bad league must not cost the whole run — the cron only fires twice
        an hour, so a raised exception would drop that window's closing prices."""
        from odds_snapshot import run_snapshot

        _match(db, 1)
        client = _StubClient(raises=True)
        summary = run_snapshot(db=db, client=client, closing=True)
        assert summary['fetch_errors'] == 1
        assert summary['snapshots_written'] == 0

    def test_unsupported_markets_are_dropped_before_the_request(self, db):
        """btts isn't on the bulk endpoint; asking for it would burn one credit
        per fixture instead of one per league."""
        from odds_snapshot import run_snapshot

        _match(db, 1)
        client = _StubClient({('Team 1', 'Team 2'): H2H_ODDS})
        run_snapshot(db=db, client=client, closing=True, markets=('btts', 'spreads'))
        assert client.calls[0][3]['markets'] == ('h2h',)


class TestApplyToBets:
    def test_pending_bet_gets_its_closing_price(self, db):
        from database import Bet
        from odds_snapshot import run_snapshot

        _match(db, 1)
        db.session.add(Bet(match_id=1, market='h2h', outcome_key='home',
                           odds_at_bet=2.05, stake=100.0, status='pending',
                           placed_at=_now(), bookmaker='Norsk Tipping'))
        db.session.commit()

        summary = run_snapshot(db=db, client=_StubClient({('Team 1', 'Team 2'): H2H_ODDS}),
                               closing=True, apply_to_bets=True)
        db.session.commit()

        bet = db.session.query(Bet).one()
        assert summary['bets_updated'] == 1
        assert bet.closing_odds == 2.20
        assert bet.closing_snapshot_at is not None

    def test_only_the_matching_outcome_is_touched(self, db):
        from database import Bet
        from odds_snapshot import run_snapshot

        _match(db, 1)
        db.session.add_all([
            Bet(match_id=1, market='h2h', outcome_key='home', odds_at_bet=2.05,
                stake=100.0, status='pending', placed_at=_now()),
            Bet(match_id=1, market='h2h', outcome_key='away', odds_at_bet=4.10,
                stake=50.0, status='pending', placed_at=_now()),
        ])
        db.session.commit()

        run_snapshot(db=db, client=_StubClient({('Team 1', 'Team 2'): H2H_ODDS}),
                     closing=True, apply_to_bets=True)
        db.session.commit()

        by_outcome = {b.outcome_key: b for b in db.session.query(Bet).all()}
        assert by_outcome['home'].closing_odds == 2.20
        assert by_outcome['away'].closing_odds == 4.60

    def test_settled_bets_are_left_alone(self, db):
        """Rewriting a settled bet's closing price would silently restate CLV
        for a period that has already been reported."""
        from database import Bet
        from odds_snapshot import run_snapshot

        _match(db, 1)
        db.session.add(Bet(match_id=1, market='h2h', outcome_key='home',
                           odds_at_bet=2.05, stake=100.0, status='won',
                           closing_odds=1.99, placed_at=_now()))
        db.session.commit()

        summary = run_snapshot(db=db, client=_StubClient({('Team 1', 'Team 2'): H2H_ODDS}),
                               closing=True, apply_to_bets=True)
        db.session.commit()

        assert summary['bets_updated'] == 0
        assert db.session.query(Bet).one().closing_odds == 1.99

    def test_apply_to_bets_forces_the_closing_window(self, db):
        """A caller asking to write closing_odds must not get a 24h-old price
        stamped on the bet as if it were the closing line."""
        from odds_snapshot import run_snapshot

        _match(db, 1, hours_ahead=9.0)
        summary = run_snapshot(db=db, client=_StubClient({('Team 1', 'Team 2'): H2H_ODDS}),
                               closing=False, apply_to_bets=True, hours=24)
        assert summary['snapshot_type'] == 'closing'
        assert summary['matches_scanned'] == 0

    def test_no_write_when_apply_to_bets_is_off(self, db):
        from database import Bet
        from odds_snapshot import run_snapshot

        _match(db, 1)
        db.session.add(Bet(match_id=1, market='h2h', outcome_key='home',
                           odds_at_bet=2.05, stake=100.0, status='pending',
                           placed_at=_now()))
        db.session.commit()

        run_snapshot(db=db, client=_StubClient({('Team 1', 'Team 2'): H2H_ODDS}),
                     closing=True, apply_to_bets=False)
        db.session.commit()
        assert db.session.query(Bet).one().closing_odds is None


class TestBucketWalk:
    def test_outcomes_without_a_price_are_skipped(self):
        from odds_snapshot import _walk_odds_buckets
        odds = {'h2h': {'home': _bucket(2.0, 1.9), 'draw': None, 'away': {}}}
        assert [(m, k) for m, k, _b in _walk_odds_buckets(odds)] == [('h2h', 'home')]

    def test_totals_are_labelled_with_our_internal_market_key(self):
        """The odds dict uses the Odds API's 'totals'; bets and resolvers use
        'totals_2_5'. Getting this wrong would file snapshots under a market no
        bet ever matches, and CLV would silently stay empty."""
        from odds_snapshot import _walk_odds_buckets
        odds = {'totals': {'over': _bucket(1.9, 1.85), 'under': _bucket(2.0, 1.95)}}
        assert {m for m, _k, _b in _walk_odds_buckets(odds)} == {'totals_2_5'}
