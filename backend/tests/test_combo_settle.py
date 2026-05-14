"""
Unit tests for combo + compound bet settle logic.

We don't go through Flask routes here — just exercise _BET_OUTCOME_RESOLVERS
(pure functions) and _settle_one_bet directly. Goal is to catch off-by-one /
operator-precedence bugs in the predicates without spinning up the full app.
"""

from __future__ import annotations

from types import SimpleNamespace


def _match(**kwargs):
    """Build a fake Match-like object with the attributes the resolvers touch."""
    defaults = dict(
        status='FINISHED',
        home_score=0, away_score=0,
        winner=None,
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
