"""
Unit tests for combo + compound bet settle logic.

We don't go through Flask routes here — just exercise _BET_OUTCOME_RESOLVERS
(pure functions) and _settle_one_bet directly. Goal is to catch off-by-one /
operator-precedence bugs in the predicates without spinning up the full app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _match(**kwargs):
    """Build a fake Match-like object with the attributes the resolvers touch.

    All optional Match columns default to None so resolvers can detect missing
    data the same way the SQLAlchemy model would. Override what each test
    actually needs.
    """
    defaults = dict(
        status='FINISHED',
        home_score=0, away_score=0, winner=None,
        # Halftime data
        home_ht_score=None, away_ht_score=None,
        # Cards
        home_yellow_cards=None, home_red_cards=None,
        away_yellow_cards=None, away_red_cards=None,
        # Corners
        home_corners=None, away_corners=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Compound resolvers — direct tests on the lambdas
# ---------------------------------------------------------------------------

def _resolvers():
    """Import lazily so test_persistence's monkeypatched engine doesn't conflict."""
    from app import _BET_OUTCOME_RESOLVERS
    return _BET_OUTCOME_RESOLVERS


class TestCompoundResolvers:
    def test_h_btts_yes_wins_when_home_wins_and_both_score(self):
        r = _resolvers()['compound']['h_btts_yes']
        assert r(_match(winner='HOME_TEAM', home_score=2, away_score=1)) is True

    def test_h_btts_yes_loses_when_home_wins_clean_sheet(self):
        r = _resolvers()['compound']['h_btts_yes']
        # Home wins 2-0 — Home Win is true, BTTS is false → compound is false.
        assert r(_match(winner='HOME_TEAM', home_score=2, away_score=0)) is False

    def test_h_btts_yes_loses_when_draw_with_both_scoring(self):
        r = _resolvers()['compound']['h_btts_yes']
        # Both score but home didn't win → false.
        assert r(_match(winner='DRAW', home_score=1, away_score=1)) is False

    def test_h_btts_no_wins_when_home_wins_with_clean_sheet(self):
        r = _resolvers()['compound']['h_btts_no']
        assert r(_match(winner='HOME_TEAM', home_score=2, away_score=0)) is True

    def test_h_btts_no_loses_when_home_wins_but_both_score(self):
        r = _resolvers()['compound']['h_btts_no']
        # Home wins 2-1 — Home Win is true, BTTS is true → btts_no is false.
        assert r(_match(winner='HOME_TEAM', home_score=2, away_score=1)) is False

    def test_a_btts_yes_wins_when_away_wins_and_both_score(self):
        r = _resolvers()['compound']['a_btts_yes']
        assert r(_match(winner='AWAY_TEAM', home_score=1, away_score=2)) is True

    def test_d_btts_yes_wins_on_score_draw(self):
        r = _resolvers()['compound']['d_btts_yes']
        assert r(_match(winner='DRAW', home_score=1, away_score=1)) is True

    def test_d_btts_yes_loses_on_goalless_draw(self):
        r = _resolvers()['compound']['d_btts_yes']
        # 0-0 is a draw but BTTS is false → compound is false.
        assert r(_match(winner='DRAW', home_score=0, away_score=0)) is False


# ---------------------------------------------------------------------------
# Sanity: market='compound' is registered with all six BTTS+Win permutations
# ---------------------------------------------------------------------------

class TestCompoundMarketCoverage:
    def test_all_six_btts_win_permutations_present(self):
        r = _resolvers()['compound']
        for k in ('h_btts_yes', 'd_btts_yes', 'a_btts_yes',
                  'h_btts_no', 'd_btts_no', 'a_btts_no'):
            assert k in r, f"missing compound resolver: {k}"


# ---------------------------------------------------------------------------
# Expanded market resolvers — totals at every line, double chance, HT, cards,
# corners. Each is a simple > / < check on a derived total, so we test
# representative lines + edge cases (exact line = lost, missing data = void).
# ---------------------------------------------------------------------------

class TestTotalsAtVariousLines:
    @pytest.mark.parametrize('home,away,line,expected_over', [
        (0, 0, 0.5, False),  # 0 goals, under 0.5
        (1, 0, 0.5, True),   # 1 goal, over 0.5
        (1, 1, 1.5, True),   # 2 goals, over 1.5
        (1, 0, 1.5, False),  # 1 goal, under 1.5
        (3, 0, 2.5, True),
        (1, 1, 2.5, False),  # 2 goals exactly = under (line is half so unambiguous)
        (5, 0, 4.5, True),
        (2, 2, 4.5, False),
        (3, 3, 5.5, True),
        (6, 0, 5.5, True),
    ])
    def test_totals_over_under(self, home, away, line, expected_over):
        line_str = str(line).replace('.', '_')
        market = f'totals_{line_str}'
        r = _resolvers()[market]
        m = _match(home_score=home, away_score=away)
        assert r['over'](m) == expected_over
        assert r['under'](m) == (not expected_over)


class TestDoubleChance:
    @pytest.mark.parametrize('winner,expected', [
        ('HOME_TEAM', {'1x': True, 'x2': False, '12': True}),
        ('DRAW',      {'1x': True, 'x2': True,  '12': False}),
        ('AWAY_TEAM', {'1x': False, 'x2': True, '12': True}),
    ])
    def test_double_chance_outcomes(self, winner, expected):
        r = _resolvers()['double_chance']
        m = _match(winner=winner)
        for outcome, want in expected.items():
            assert r[outcome](m) == want, f"{outcome} for {winner}"


class TestHalftimeMarkets:
    def test_ht_result_home_wins(self):
        r = _resolvers()['ht_result']
        m = _match(home_ht_score=2, away_ht_score=0)
        assert r['home'](m) is True
        assert r['draw'](m) is False
        assert r['away'](m) is False

    def test_ht_result_draw(self):
        r = _resolvers()['ht_result']
        m = _match(home_ht_score=1, away_ht_score=1)
        assert r['draw'](m) is True
        assert r['home'](m) is False
        assert r['away'](m) is False

    def test_ht_result_voids_when_ht_data_missing(self):
        """Match without HT data → all HT picks resolve to None (settle path voids)."""
        r = _resolvers()['ht_result']
        m = _match(home_ht_score=None, away_ht_score=None)
        assert r['home'](m) is None
        assert r['draw'](m) is None
        assert r['away'](m) is None

    def test_ht_totals_under_with_goalless_first_half(self):
        r = _resolvers()['ht_totals_0_5']
        m = _match(home_ht_score=0, away_ht_score=0)
        assert r['under'](m) is True
        assert r['over'](m) is False


class TestCardsMarket:
    def test_cards_over_with_busy_match(self):
        r = _resolvers()['cards_4_5']
        m = _match(
            home_yellow_cards=3, home_red_cards=0,
            away_yellow_cards=2, away_red_cards=0,
        )
        # Total = 5 cards, over 4.5 ✓
        assert r['over'](m) is True

    def test_cards_under_with_clean_match(self):
        r = _resolvers()['cards_3_5']
        m = _match(
            home_yellow_cards=1, home_red_cards=0,
            away_yellow_cards=2, away_red_cards=0,
        )
        # Total = 3 cards, under 3.5 ✓
        assert r['under'](m) is True

    def test_cards_voids_when_data_missing(self):
        r = _resolvers()['cards_3_5']
        m = _match(home_yellow_cards=None)  # other fields default to None too
        # Missing data → None (settle path voids the bet)
        assert r['over'](m) is None
        assert r['under'](m) is None


class TestCornersMarket:
    def test_corners_over(self):
        r = _resolvers()['corners_9_5']
        m = _match(home_corners=7, away_corners=4)
        # Total = 11, over 9.5
        assert r['over'](m) is True
        assert r['under'](m) is False

    def test_corners_under(self):
        r = _resolvers()['corners_8_5']
        m = _match(home_corners=3, away_corners=4)
        # Total = 7, under 8.5
        assert r['under'](m) is True
        assert r['over'](m) is False

    def test_corners_voids_when_data_missing(self):
        r = _resolvers()['corners_8_5']
        m = _match(home_corners=None, away_corners=None)
        assert r['over'](m) is None
        assert r['under'](m) is None


class TestMarketsCoverage:
    """Snapshot: catches accidental removal of supported markets."""

    def test_all_supported_markets_have_at_least_one_outcome(self):
        r = _resolvers()
        for market, outcomes in r.items():
            assert outcomes, f"market {market} has no outcomes"

    def test_totals_present_at_every_common_line(self):
        for line in ('0_5', '1_5', '2_5', '3_5', '4_5', '5_5'):
            assert f'totals_{line}' in _resolvers(), f"missing totals_{line}"

    def test_corners_present_at_every_common_line(self):
        for line in ('7_5', '8_5', '9_5', '10_5', '11_5'):
            assert f'corners_{line}' in _resolvers(), f"missing corners_{line}"

    def test_cards_present_at_every_common_line(self):
        for line in ('2_5', '3_5', '4_5', '5_5', '6_5'):
            assert f'cards_{line}' in _resolvers(), f"missing cards_{line}"


# ---------------------------------------------------------------------------
# Combo early-settle — a combo is dead the moment ANY leg loses, even if
# other legs are still pending. Without this fix the combo would stay
# "pending" until the last leg plays, which is misleading on multi-day combos.
# ---------------------------------------------------------------------------

class TestComboEarlySettle:
    """
    These tests exercise _settle_one_bet's combo branch using a fake bet object
    and a stubbed _resolve_leg. Going through the real DB would force test_combo_settle
    to take on the persistence fixture; we don't need full integration to cover
    the branching logic.
    """

    def _make_bet(self, *, legs, stake=100.0, odds=10.0):
        from datetime import datetime
        return SimpleNamespace(
            id=1,
            market='combo',
            outcome_key='multi',
            stake=stake,
            odds_at_bet=odds,
            combo_legs=legs,
            status='pending',
            settled_at=None,
            profit_loss=None,
            placed_at=datetime(2026, 5, 1),
        )

    def _patch_resolver(self, monkeypatch, results):
        """Patch _resolve_leg to return the given results in sequence."""
        import app
        calls = iter(results)
        monkeypatch.setattr(app, '_resolve_leg', lambda leg: next(calls))

    def test_combo_lost_immediately_when_first_leg_loses(self, monkeypatch):
        from app import _settle_one_bet
        legs = [{'match_id': 1}, {'match_id': 2}, {'match_id': 3}]
        # First leg lost, others still pending — should settle as LOST now.
        self._patch_resolver(monkeypatch, ['lost', None, None])
        bet = self._make_bet(legs=legs)
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is True
        assert bet.status == 'lost'
        assert bet.profit_loss == -100.0

    def test_combo_stays_pending_when_legs_unfinished_no_loss(self, monkeypatch):
        from app import _settle_one_bet
        legs = [{'match_id': 1}, {'match_id': 2}]
        self._patch_resolver(monkeypatch, ['won', None])
        bet = self._make_bet(legs=legs)
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is False
        assert bet.status == 'pending'

    def test_combo_won_when_all_legs_won(self, monkeypatch):
        from app import _settle_one_bet
        legs = [{'match_id': 1}, {'match_id': 2}, {'match_id': 3}]
        self._patch_resolver(monkeypatch, ['won', 'won', 'won'])
        bet = self._make_bet(legs=legs, stake=100.0, odds=11.65)
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is True
        assert bet.status == 'won'
        # P/L = stake * (odds - 1) = 100 * 10.65 = 1065
        assert bet.profit_loss == round(100 * (11.65 - 1.0), 6)

    def test_combo_void_when_any_leg_voids_and_none_lost(self, monkeypatch):
        from app import _settle_one_bet
        legs = [{'match_id': 1}, {'match_id': 2}]
        self._patch_resolver(monkeypatch, ['won', 'void'])
        bet = self._make_bet(legs=legs)
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_empty_combo_voids_immediately(self):
        """Malformed combo (no legs) shouldn't loop forever as pending."""
        from app import _settle_one_bet
        bet = self._make_bet(legs=[])
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is True
        assert bet.status == 'void'


# ---------------------------------------------------------------------------
# CANCELLED-match handling — without this, cancelled matches leave bets pending
# forever even though we know the bet will never settle to won/lost.
# ---------------------------------------------------------------------------

class TestCancelledMatch:
    def test_single_bet_voids_on_cancelled_match(self):
        from app import _settle_one_bet
        from datetime import datetime
        bet = SimpleNamespace(
            id=1, market='h2h', outcome_key='home', stake=100.0,
            odds_at_bet=2.0, status='pending', settled_at=None,
            profit_loss=None, placed_at=datetime(2026, 5, 1),
            combo_legs=None,
        )
        cancelled_match = SimpleNamespace(
            status='CANCELLED', home_score=None, away_score=None, winner=None
        )
        changed = _settle_one_bet(bet, cancelled_match)
        assert changed is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    @pytest.mark.parametrize('non_played_status', ['POSTPONED', 'SUSPENDED'])
    def test_single_bet_voids_on_postponed_or_suspended(self, non_played_status):
        """
        POSTPONED and SUSPENDED matches must void the bet — otherwise a bet on
        a postponed fixture stays pending forever even after the rescheduled
        match plays under a NEW match_id. Matches typical bookie rules.
        """
        from app import _settle_one_bet
        from datetime import datetime
        bet = SimpleNamespace(
            id=1, market='h2h', outcome_key='home', stake=100.0,
            odds_at_bet=2.0, status='pending', settled_at=None,
            profit_loss=None, placed_at=datetime(2026, 5, 1),
            combo_legs=None,
        )
        match = SimpleNamespace(
            status=non_played_status, home_score=None, away_score=None, winner=None
        )
        changed = _settle_one_bet(bet, match)
        assert changed is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_combo_voids_when_leg_postponed(self, monkeypatch):
        """A combo with one POSTPONED leg should void rather than hang."""
        from app import _settle_one_bet
        from datetime import datetime
        legs = [{'match_id': 1}, {'match_id': 2}]
        # Patch _resolve_leg to mimic: leg 0 won, leg 1 returns 'void' (postponed).
        import app
        results = iter(['won', 'void'])
        monkeypatch.setattr(app, '_resolve_leg', lambda L: next(results))
        bet = SimpleNamespace(
            id=1, market='combo', outcome_key='multi', stake=100.0,
            odds_at_bet=5.0, combo_legs=legs, status='pending',
            settled_at=None, profit_loss=None, placed_at=datetime(2026, 5, 1),
        )
        changed = _settle_one_bet(bet, SimpleNamespace())
        assert changed is True
        assert bet.status == 'void'


# ---------------------------------------------------------------------------
# Tri-state resolver contract — None means underlying stat (HT, cards, corners)
# is missing on the match record. Settle path must void, not auto-lose.
# Without this, every HT/cards/corners bet on a free-tier-API match (which
# never ships those columns) would silently settle to 'lost'.
# ---------------------------------------------------------------------------

class TestMissingDataVoids:
    def _make_bet(self, market, outcome_key, *, stake=100.0, odds=2.0):
        from datetime import datetime
        return SimpleNamespace(
            id=1, market=market, outcome_key=outcome_key, stake=stake,
            odds_at_bet=odds, status='pending', settled_at=None,
            profit_loss=None, placed_at=datetime(2026, 5, 1),
            combo_legs=None,
        )

    def test_single_ht_bet_voids_when_ht_data_missing(self):
        """FINISHED match with no HT scores → ht_result bet voids, not lost."""
        from app import _settle_one_bet
        bet = self._make_bet('ht_result', 'home')
        match = _match(winner='HOME_TEAM', home_score=2, away_score=0,
                       home_ht_score=None, away_ht_score=None)
        changed = _settle_one_bet(bet, match)
        assert changed is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_single_cards_bet_voids_when_cards_missing(self):
        from app import _settle_one_bet
        bet = self._make_bet('cards_3_5', 'over')
        match = _match(winner='HOME_TEAM', home_score=1, away_score=0)
        # All card columns default to None
        changed = _settle_one_bet(bet, match)
        assert changed is True
        assert bet.status == 'void'

    def test_single_corners_bet_voids_when_corners_missing(self):
        from app import _settle_one_bet
        bet = self._make_bet('corners_9_5', 'under')
        match = _match(winner='DRAW', home_score=1, away_score=1)
        changed = _settle_one_bet(bet, match)
        assert changed is True
        assert bet.status == 'void'

    def test_single_ht_bet_resolves_normally_with_data(self):
        """Sanity: tri-state path still settles won/lost correctly with data."""
        from app import _settle_one_bet
        bet = self._make_bet('ht_result', 'home', odds=2.0, stake=100.0)
        match = _match(winner='HOME_TEAM', home_score=3, away_score=1,
                       home_ht_score=2, away_ht_score=0)
        changed = _settle_one_bet(bet, match)
        assert changed is True
        assert bet.status == 'won'
        assert bet.profit_loss == 100.0  # stake * (odds - 1)


# ---------------------------------------------------------------------------
# Auth gate for bet writes — without BET_WRITE_TOKEN env, allows all (backward
# compat). With it set, requires matching X-Bet-Token header.
# ---------------------------------------------------------------------------

class TestBetWriteAuth:
    def test_no_token_env_allows_unauthenticated(self, monkeypatch):
        from app import app as flask_app, _require_bet_write_token
        monkeypatch.delenv('BET_WRITE_TOKEN', raising=False)
        with flask_app.test_request_context('/api/bets', method='POST'):
            assert _require_bet_write_token() is True

    def test_token_env_set_rejects_missing_header(self, monkeypatch):
        from app import app as flask_app, _require_bet_write_token
        monkeypatch.setenv('BET_WRITE_TOKEN', 'expected-value')
        with flask_app.test_request_context('/api/bets', method='POST'):
            assert _require_bet_write_token() is False

    def test_token_env_set_rejects_wrong_header(self, monkeypatch):
        from app import app as flask_app, _require_bet_write_token
        monkeypatch.setenv('BET_WRITE_TOKEN', 'expected-value')
        with flask_app.test_request_context('/api/bets', method='POST',
                                            headers={'X-Bet-Token': 'wrong'}):
            assert _require_bet_write_token() is False

    def test_token_env_set_accepts_matching_header(self, monkeypatch):
        from app import app as flask_app, _require_bet_write_token
        monkeypatch.setenv('BET_WRITE_TOKEN', 'expected-value')
        with flask_app.test_request_context('/api/bets', method='POST',
                                            headers={'X-Bet-Token': 'expected-value'}):
            assert _require_bet_write_token() is True


# ---------------------------------------------------------------------------
# Fixture score extraction — verifies the data_collection bug where home_score
# was hardcoded to None is actually fixed.
# ---------------------------------------------------------------------------

class TestFixtureScoreExtraction:
    """
    get_upcoming_fixtures hardcoded home_score/away_score/winner to None for
    every match, even FINISHED ones with score data inline. That silently
    dropped results and prevented bet settlement.
    """

    def _fake_api_match(self, home_score, away_score, winner, status='FINISHED'):
        return {
            'id': 12345,
            'competition': {'id': 2021, 'name': 'Premier League'},
            'season': {'startDate': '2025-08-15'},
            'matchday': 30,
            'stage': 'REGULAR_SEASON',
            'utcDate': '2026-05-16T14:00:00Z',
            'status': status,
            'homeTeam': {'id': 1, 'name': 'Arsenal', 'shortName': 'Arsenal'},
            'awayTeam': {'id': 2, 'name': 'Spurs', 'shortName': 'Spurs'},
            'score': {
                'fullTime': {'home': home_score, 'away': away_score},
                'winner': winner,
            },
        }

    def test_finished_match_carries_scores_through(self, monkeypatch):
        import data_collection
        # COMPETITIONS lookup must include 2021 (it's the EPL ID in our
        # SUPPORTED_COMPETITIONS map). If the test env's COMPETITIONS dict
        # doesn't have it, this test is a no-op — skip with a clear reason.
        if 2021 not in data_collection.COMPETITIONS:
            import pytest
            pytest.skip(f"Competition 2021 not in COMPETITIONS: {data_collection.COMPETITIONS}")

        # Stub the HTTP call to return one finished match.
        fake_match = self._fake_api_match(2, 1, 'HOME_TEAM')
        monkeypatch.setattr(
            data_collection.FootballDataCollector,
            '_make_requests',
            lambda self, path, params=None: {'matches': [fake_match]},
        )

        collector = data_collection.FootballDataCollector.__new__(data_collection.FootballDataCollector)
        collector.api_key = 'fake'
        fixtures = collector.get_upcoming_fixtures(days=7)

        assert len(fixtures) == 1
        f = fixtures[0]
        assert f['home_score'] == 2, "home_score must propagate from API response"
        assert f['away_score'] == 1
        assert f['winner'] == 'HOME_TEAM'

    def test_unfinished_match_has_none_scores(self, monkeypatch):
        import data_collection
        if 2021 not in data_collection.COMPETITIONS:
            import pytest
            pytest.skip(f"Competition 2021 not in COMPETITIONS")

        # Future match: API returns score block with all-None values.
        future_match = self._fake_api_match(None, None, None, status='SCHEDULED')
        monkeypatch.setattr(
            data_collection.FootballDataCollector,
            '_make_requests',
            lambda self, path, params=None: {'matches': [future_match]},
        )

        collector = data_collection.FootballDataCollector.__new__(data_collection.FootballDataCollector)
        collector.api_key = 'fake'
        fixtures = collector.get_upcoming_fixtures(days=7)

        assert fixtures[0]['home_score'] is None
        assert fixtures[0]['winner'] is None
