"""
Regression cover for the two feature-pipeline defects that silently degraded
production predictions: end-of-season league tables used as point-in-time
features, and rest days imputed differently at train and serve time.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _match(mid, home, away, date, hs=None, a_s=None, winner=None,
           comp='Test League', season=2024):
    return SimpleNamespace(
        id=mid, home_team_id=home, away_team_id=away, date=date,
        home_score=hs, away_score=a_s, winner=winner,
        competition=comp, season=season, stage='REGULAR_SEASON',
    )


@pytest.fixture
def engineer():
    """A FeatureEngineer with its caches populated by hand — no database."""
    from collections import defaultdict

    from feature_engineering import FeatureEngineer

    fe = FeatureEngineer.__new__(FeatureEngineer)
    # The DB-backed table lookups are the serving path; these tests drive the
    # in-memory one, so hand it a session that finds nothing. Without a stub the
    # class would reach for a real connection the moment an in-memory lookup
    # misses.
    class _EmptyResult:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    fe.db = SimpleNamespace(session=SimpleNamespace(execute=lambda *a, **k: _EmptyResult()))
    fe._db_table_cache = {}
    fe._domestic_cache = {}
    fe._standings_cache = {}
    fe._date_index = {}
    fe._pit_standings = {}
    fe._table_cache = {}
    fe._group_matches = defaultdict(list)
    fe._group_teams = defaultdict(set)
    fe._team_all_matches = defaultdict(list)
    fe._team_home_matches = defaultdict(list)
    fe._team_away_matches = defaultdict(list)
    fe._h2h_matches = defaultdict(list)
    return fe


# A three-team league. Team 1 wins everything, team 3 loses everything, so the
# final table is unambiguous and completely unlike the table on matchday 1.
FIXTURES = [
    _match(1, 1, 2, datetime(2024, 8, 1), 3, 0, 'HOME_TEAM'),
    _match(2, 3, 1, datetime(2024, 8, 8), 0, 2, 'AWAY_TEAM'),
    _match(3, 2, 3, datetime(2024, 8, 15), 1, 1, 'DRAW'),
    _match(4, 1, 3, datetime(2024, 8, 22), 4, 0, 'HOME_TEAM'),
    _match(5, 2, 1, datetime(2024, 8, 29), 0, 1, 'AWAY_TEAM'),
]


class TestPointInTimeStandings:
    def _load(self, fe):
        for m in FIXTURES:
            group = (m.competition, m.season)
            fe._group_matches[group].append(m)
            fe._group_teams[group].update({m.home_team_id, m.away_team_id})
        fe._build_pit_standings()

    def test_first_match_sees_an_empty_table(self, engineer):
        self._load(engineer)
        snap = engineer._pit_standings[1]
        assert snap['home']['points'] == 0
        assert snap['away']['points'] == 0
        assert snap['home']['goal_difference'] == 0

    def test_table_reflects_only_earlier_results(self, engineer):
        self._load(engineer)
        # Match 4 (22 Aug): team 1 has won matches 1 and 2 → 6 points, GD +5.
        snap = engineer._pit_standings[4]
        assert snap['home']['points'] == 6
        assert snap['home']['goal_difference'] == 5
        # Team 3 has lost one and drawn one → 1 point.
        assert snap['away']['points'] == 1

    def test_never_leaks_the_final_table(self, engineer):
        """The whole point: an early match must not see end-of-season totals."""
        self._load(engineer)
        final = engineer._table_as_of('Test League', 2024, datetime(2025, 6, 1))
        assert final[1]['points'] == 12          # team 1 won all four
        for match_id in (1, 2, 3):
            assert engineer._pit_standings[match_id]['home']['points'] < final[1]['points']

    def test_lookup_prefers_the_snapshot_over_the_stored_row(self, engineer):
        """A stored Standing row holds the FINAL table and must not win."""
        self._load(engineer)
        engineer._standings_cache[(1, 2024, 'Test League')] = SimpleNamespace(
            position=1, points=12, goal_difference=10)
        got = engineer._get_standing_from_cache(1, 2024, FIXTURES[0])
        assert got['points'] == 0, "end-of-season Standing row leaked into match 1"

    def test_stored_row_is_still_the_last_resort(self, engineer):
        """Competitions we can't reconstruct keep working off the stored table."""
        engineer._standings_cache[(7, 2024, 'Unknown League')] = SimpleNamespace(
            position=3, points=44, goal_difference=8)
        unknown = _match(99, 7, 8, datetime(2024, 9, 1), comp='Unknown League')
        got = engineer._get_standing_from_cache(7, 2024, unknown)
        # Asserting the three feature-bearing keys rather than the whole dict:
        # entries also carry `played`, which is bookkeeping for the early-season
        # position seeding and not a feature.
        assert got['league_position'] == 3
        assert got['points'] == 44
        assert got['goal_difference'] == 8

    def test_table_as_of_is_exclusive_of_the_date(self, engineer):
        self._load(engineer)
        before = engineer._table_as_of('Test League', 2024, datetime(2024, 8, 8))
        assert before[1]['points'] == 3   # only match 1 counted
        after = engineer._table_as_of('Test League', 2024, datetime(2024, 8, 9))
        assert after[1]['points'] == 6


class TestRestDayImputation:
    """
    Training fills a missing rest-day gap with 7 (_build_xy_from_csv). Serving
    zero-filled every None first, so the model was handed 0 for season openers,
    promoted sides and any coverage gap — a value it never trained on.
    """

    def _features(self, raw):
        from prediction_service import compute_features
        fe = MagicMock()
        fe.create_match_features.return_value = dict(raw)
        model_data = {
            'feature_names': ['days_since_home_last_match', 'days_since_away_last_match',
                              'rest_diff'],
            'elo_ratings': None,
        }
        home = SimpleNamespace(id=1, name='Home FC')
        away = SimpleNamespace(id=2, name='Away FC')
        return compute_features(home, away, fe, model_data)

    def test_missing_rest_days_impute_to_seven(self):
        out = self._features({
            'days_since_home_last_match': None,
            'days_since_away_last_match': None,
        })
        assert out['days_since_home_last_match'] == 7
        assert out['days_since_away_last_match'] == 7
        assert out['rest_diff'] == 0

    def test_present_rest_days_are_untouched(self):
        out = self._features({
            'days_since_home_last_match': 3,
            'days_since_away_last_match': 10,
        })
        assert out['days_since_home_last_match'] == 3
        assert out['days_since_away_last_match'] == 10
        assert out['rest_diff'] == -7

    def test_one_missing_side_still_uses_the_training_default(self):
        out = self._features({
            'days_since_home_last_match': None,
            'days_since_away_last_match': 4,
        })
        assert out['days_since_home_last_match'] == 7
        assert out['rest_diff'] == 3


class TestComboHonesty:
    def test_combos_declare_the_independence_assumption(self):
        from prediction_service import _build_combos
        markets = {
            'btts': {'probabilities': {'Yes': 0.55, 'No': 0.45}},
            'over_2_5': {'probabilities': {'Over': 0.5, 'Under': 0.5}},
        }
        combos = _build_combos(0.5, 0.3, 0.2, markets)
        assert combos, "expected combos to be built"
        assert all(c.get('independence_assumed') is True for c in combos.values())
        # And the arithmetic is still the plain product it claims to be.
        assert combos['H_btts_yes']['probability'] == pytest.approx(0.5 * 0.55, abs=1e-4)


class TestFeatureStaleness:
    """
    The incremental recompute used to compare stored features against
    match.updated_at only, which notices a score arriving but never notices that
    the FEATURE PIPELINE changed. Rows built with end-of-season league tables
    therefore looked permanently current, and the weekly retrain — which calls
    create_features_for_all_matches without force=True — would have kept training
    on them indefinitely. Version stamping is what closes that.
    """

    def _match(self, updated):
        return SimpleNamespace(id=1, updated_at=updated, created_at=updated)

    def test_no_stored_row_is_not_current(self):
        from feature_engineering import feature_row_is_current
        assert feature_row_is_current(None, self._match(datetime(2026, 1, 1))) is False

    def test_current_version_and_newer_than_the_match_is_reusable(self):
        from feature_engineering import FEATURE_PIPELINE_VERSION, feature_row_is_current
        entry = (datetime(2026, 2, 1), FEATURE_PIPELINE_VERSION)
        assert feature_row_is_current(entry, self._match(datetime(2026, 1, 1))) is True

    def test_match_updated_after_the_features_forces_a_rebuild(self):
        from feature_engineering import FEATURE_PIPELINE_VERSION, feature_row_is_current
        entry = (datetime(2026, 1, 1), FEATURE_PIPELINE_VERSION)
        assert feature_row_is_current(entry, self._match(datetime(2026, 2, 1))) is False

    def test_older_pipeline_rebuilds_however_fresh_the_row_is(self):
        """The case the old logic missed."""
        from feature_engineering import feature_row_is_current
        entry = (datetime(2099, 1, 1), '1')
        assert feature_row_is_current(entry, self._match(datetime(2026, 1, 1))) is False

    def test_unstamped_legacy_rows_rebuild(self):
        """Rows written before the column existed carry NULL."""
        from feature_engineering import feature_row_is_current
        entry = (datetime(2099, 1, 1), None)
        assert feature_row_is_current(entry, self._match(datetime(2026, 1, 1))) is False

    def test_match_with_no_timestamps_trusts_the_version_stamp(self):
        from feature_engineering import FEATURE_PIPELINE_VERSION, feature_row_is_current
        entry = (datetime(2026, 1, 1), FEATURE_PIPELINE_VERSION)
        assert feature_row_is_current(entry, self._match(None)) is True


class TestSimultaneousKickoffs:
    """
    Matches kicking off at the same moment must be snapshotted against the same
    table. Walking them one at a time ordered them by primary key, so a 15:00
    Saturday fixture saw the results of the other 15:00 fixtures — a small leak
    of the same kind the point-in-time work exists to remove.
    """

    SLATE = [
        _match(10, 1, 2, datetime(2024, 9, 7, 15, 0), 3, 0, 'HOME_TEAM'),
        _match(11, 3, 4, datetime(2024, 9, 7, 15, 0), 0, 2, 'AWAY_TEAM'),
        _match(12, 1, 3, datetime(2024, 9, 14, 15, 0), 1, 1, 'DRAW'),
    ]

    def _load(self, fe):
        for m in self.SLATE:
            group = (m.competition, m.season)
            fe._group_matches[group].append(m)
            fe._group_teams[group].update({m.home_team_id, m.away_team_id})
        fe._build_pit_standings()

    def test_same_kickoff_sees_the_same_table(self, engineer):
        self._load(engineer)
        a, b = engineer._pit_standings[10], engineer._pit_standings[11]
        for snap in (a, b):
            assert snap['home']['points'] == 0
            assert snap['away']['points'] == 0
            assert snap['home']['played'] == 0

    def test_a_later_kickoff_does_see_the_earlier_slate(self, engineer):
        self._load(engineer)
        later = engineer._pit_standings[12]
        assert later['home']['points'] == 3     # team 1 won on the 7th
        assert later['home']['played'] == 1
        assert later['away']['points'] == 0     # team 3 lost


class TestEarlySeasonPositionSeeding:
    """
    On matchday 1 every club is level, and _rank breaks the tie on team id — so
    the league position handed to the model is an artefact of primary keys. Last
    season's final table is a real ordering that was known before kickoff.
    """

    LAST_SEASON = [
        _match(20, 1, 2, datetime(2023, 8, 1), 5, 0, 'HOME_TEAM', season=2023),
        _match(21, 2, 3, datetime(2023, 8, 8), 0, 3, 'AWAY_TEAM', season=2023),
        _match(22, 3, 1, datetime(2023, 8, 15), 0, 1, 'AWAY_TEAM', season=2023),
    ]

    def _load(self, fe):
        for m in self.LAST_SEASON:
            group = (m.competition, m.season)
            fe._group_matches[group].append(m)
            fe._group_teams[group].update({m.home_team_id, m.away_team_id})
        fe._build_pit_standings()

    def test_previous_season_position_is_used_before_matches_are_played(self, engineer):
        from feature_engineering import FEATURE_PIPELINE_VERSION  # noqa: F401
        self._load(engineer)
        # Team 1 won both of its matches last season -> finished top.
        assert engineer._previous_season_position(1, 'Test League', 2024) == 1
        entry = {'league_position': 17, 'points': 0, 'goal_difference': 0, 'played': 0}
        seeded = engineer._seed_early_position(entry, 1, 'Test League', 2024)
        assert seeded['league_position'] == 1
        # Points and goal difference are NOT seeded: substituting an
        # end-of-season total is the train/serve skew this work removed.
        assert seeded['points'] == 0
        assert seeded['goal_difference'] == 0

    def test_a_club_new_to_the_division_starts_below_the_bottom(self, engineer):
        self._load(engineer)
        assert engineer._previous_season_position(99, 'Test League', 2024) == 4

    def test_seeding_stops_once_the_table_means_something(self, engineer):
        self._load(engineer)
        from feature_engineering import EARLY_SEASON_MATCHES
        entry = {'league_position': 17, 'points': 12,
                 'goal_difference': 4, 'played': EARLY_SEASON_MATCHES}
        assert engineer._seed_early_position(entry, 1, 'Test League', 2024) is entry

    def test_no_previous_season_leaves_the_entry_alone(self, engineer):
        entry = {'league_position': 17, 'points': 0, 'goal_difference': 0, 'played': 0}
        assert engineer._seed_early_position(entry, 1, 'Unknown League', 2024) is entry
