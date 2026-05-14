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
