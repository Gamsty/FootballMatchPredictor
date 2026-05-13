"""
Unit tests for value-bet math.

The math itself is small (one-line formulas), so these are mostly there to catch
sign errors and threshold off-by-ones if the implementation changes.
"""
from value_bets import compute_edge, compute_kelly, value_picks
import pytest


# ----------------------------------------------------------------------------
# compute_edge
# ----------------------------------------------------------------------------

def test_edge_positive_when_model_beats_book():
    # Model says 55%, book prices it at 2.10 (implied ~47.6%).
    # Edge = 0.55 * 2.10 - 1 = 0.155
    assert compute_edge(0.55, 2.10) == pytest.approx(0.155)


def test_edge_zero_at_fair_odds():
    # Fair odds for 50% = 2.00; edge must be exactly zero.
    assert compute_edge(0.50, 2.00) == pytest.approx(0.0)


def test_edge_negative_when_book_overprices():
    # Model 40%, book 2.00 (50% implied) — book is too short.
    assert compute_edge(0.40, 2.00) == pytest.approx(-0.2)


# ----------------------------------------------------------------------------
# compute_kelly
# ----------------------------------------------------------------------------

def test_kelly_zero_when_no_edge():
    # At fair odds Kelly fraction is 0 — never bet a 0-edge wager.
    assert compute_kelly(0.50, 2.00) == 0.0


def test_kelly_zero_when_negative_edge():
    # Negative-edge bet: Kelly clamps to 0 (you don't bet negatives).
    assert compute_kelly(0.40, 2.00) == 0.0


def test_kelly_positive_when_edge_positive():
    # f* = (p*b - q) / b where b = odds - 1 = 1.10, p = 0.55, q = 0.45
    # f* = (0.55 * 1.10 - 0.45) / 1.10 = 0.155 / 1.10 ≈ 0.1409
    assert compute_kelly(0.55, 2.10) == pytest.approx(0.14090909, abs=1e-6)


def test_kelly_degenerate_odds():
    # Odds <= 1 means you can't actually lose anything → guard returns 0.
    assert compute_kelly(0.9, 1.0) == 0.0


# ----------------------------------------------------------------------------
# value_picks (integration of the above + filtering)
# ----------------------------------------------------------------------------

def _prediction(home, draw, away):
    return {'probabilities': {'home_win': home, 'draw': draw, 'away_win': away}}


def _odds(home=None, draw=None, away=None, bm='Pinnacle'):
    out = {}
    for k, p in [('home', home), ('draw', draw), ('away', away)]:
        out[k] = {'price': p, 'bookmaker': bm} if p is not None else None
    return out


def test_value_picks_returns_only_positive_edges():
    # Home is +EV (0.55 * 2.10 = 1.155), draw and away are −EV.
    picks = value_picks(
        _prediction(0.55, 0.25, 0.20),
        _odds(home=2.10, draw=3.00, away=4.00),
        min_edge=0.02,
    )
    assert [p['outcome'] for p in picks] == ['Home Win']


def test_value_picks_threshold_filters_marginal_bets():
    # All three outcomes deliberately price near fair: home 0.50*2.03 - 1 = 0.015,
    # draw 0.20*5.00 - 1 = 0.0, away 0.30*3.30 - 1 = -0.01. None clears 3%.
    picks = value_picks(
        _prediction(0.50, 0.20, 0.30),
        _odds(home=2.03, draw=5.00, away=3.30),
        min_edge=0.03,
    )
    assert picks == []


def test_value_picks_sorted_by_edge_desc():
    # Both home and away have positive edges; home edge > away edge → home first.
    picks = value_picks(
        _prediction(0.55, 0.15, 0.30),
        _odds(home=2.10, draw=10.0, away=3.50),  # home edge 0.155, away edge 0.05
        min_edge=0.02,
    )
    assert len(picks) >= 2
    edges = [p['edge'] for p in picks]
    assert edges == sorted(edges, reverse=True)


def test_value_picks_skips_missing_odds():
    # Home has no odds → home is silently dropped, not crashed on.
    picks = value_picks(
        _prediction(0.55, 0.25, 0.20),
        _odds(home=None, draw=3.00, away=4.00),
        min_edge=0.00,
    )
    assert all(p['outcome'] != 'Home Win' for p in picks)


def test_value_picks_handles_empty_inputs():
    assert value_picks(None, _odds(home=2.0)) == []
    assert value_picks(_prediction(0.5, 0.3, 0.2), None) == []
    assert value_picks({}, {}) == []


def test_value_picks_includes_kelly_and_bookmaker():
    picks = value_picks(
        _prediction(0.55, 0.25, 0.20),
        _odds(home=2.10, bm='Bet365'),
        min_edge=0.0,
        # Legacy odds shape collapses median == best, so either flavour works;
        # pin to best so the test stays deterministic on either.
        use_median=False,
    )
    assert len(picks) == 1
    p = picks[0]
    assert p['bookmaker'] == 'Bet365'
    assert p['kelly'] > 0
    assert p['outcome_key'] == 'home'
    # New fields added in the multi-market refactor
    assert p['market'] == 'h2h'
    assert 'edge_best' in p and 'edge_median' in p


# ----------------------------------------------------------------------------
# Multi-market shape (h2h + totals + btts) and edge_median vs edge_best
# ----------------------------------------------------------------------------

def _full_prediction(home, draw, away, over_2_5=None, under_2_5=None, btts_yes=None, btts_no=None):
    """Build a prediction matching predict_all_markets() shape."""
    pred = {'match_result': {'probabilities': {'home_win': home, 'draw': draw, 'away_win': away}}}
    markets = {}
    if over_2_5 is not None:
        markets['over_2_5'] = {'probabilities': {'Over': over_2_5, 'Under': under_2_5 or (1 - over_2_5)}}
    if btts_yes is not None:
        markets['btts'] = {'probabilities': {'Yes': btts_yes, 'No': btts_no or (1 - btts_yes)}}
    if markets:
        pred['markets'] = markets
    return pred


def _bucket(best_price, median, bm='Pinnacle', count=3):
    return {'best': {'price': best_price, 'bookmaker': bm}, 'median': median, 'count': count}


def _full_odds(h2h=None, totals=None, btts=None):
    out = {}
    if h2h:
        out['h2h'] = h2h
    if totals:
        out['totals'] = totals
    if btts:
        out['btts'] = btts
    return out


def test_picks_across_h2h_totals_btts():
    pred = _full_prediction(
        home=0.55, draw=0.25, away=0.20,
        over_2_5=0.60, under_2_5=0.40,
        btts_yes=0.55, btts_no=0.45,
    )
    odds = _full_odds(
        h2h={'home': _bucket(2.10, 2.05), 'draw': _bucket(3.50, 3.40), 'away': _bucket(4.00, 3.80)},
        totals={'over': _bucket(2.00, 1.95), 'under': _bucket(2.10, 2.05), 'point': 2.5},
        btts={'yes': _bucket(2.00, 1.95), 'no': _bucket(2.00, 1.95)},
    )
    picks = value_picks(pred, odds, min_edge=0.02, use_median=True)
    markets_seen = {p['market'] for p in picks}
    # All three market families produced at least one pick
    assert markets_seen >= {'h2h', 'totals_2_5', 'btts'}


def test_median_edge_gates_out_outlier_only_picks():
    # Pretend ONE book has a stale 30.0 line; median across trusted set is 6.0.
    # edge_best = 0.20 * 30 - 1 = 5.0; edge_median = 0.20 * 6.0 - 1 = 0.20.
    # With use_median=True and min_edge=1.0, this still passes (0.2 < 1.0 → filtered).
    pred = _full_prediction(home=0.20, draw=0.30, away=0.50)
    odds = _full_odds(h2h={
        'home': _bucket(30.0, 6.0, bm='Pinnacle', count=8),
        'draw': _bucket(3.50, 3.40), 'away': _bucket(2.00, 1.95),
    })
    picks = value_picks(pred, odds, min_edge=1.0, use_median=True)
    assert all(p['outcome_key'] != 'home' for p in picks)

    # Same data, gate by edge_best — the outlier survives
    picks2 = value_picks(pred, odds, min_edge=1.0, use_median=False)
    assert any(p['outcome_key'] == 'home' for p in picks2)


def test_pick_includes_both_edge_flavours():
    pred = _full_prediction(home=0.55, draw=0.25, away=0.20)
    odds = _full_odds(h2h={
        'home': _bucket(2.20, 2.00, count=5),
        'draw': _bucket(3.50, 3.40), 'away': _bucket(4.00, 3.80),
    })
    picks = value_picks(pred, odds, min_edge=0.0, use_median=True)
    home_pick = next(p for p in picks if p['outcome_key'] == 'home')
    # edge_best = 0.55 * 2.20 - 1 = 0.21; edge_median = 0.55 * 2.00 - 1 = 0.10
    assert home_pick['edge_best'] == pytest.approx(0.21, abs=1e-3)
    assert home_pick['edge_median'] == pytest.approx(0.10, abs=1e-3)
    assert home_pick['odds'] == 2.20
    assert home_pick['odds_median'] == 2.00
    assert home_pick['book_count'] == 5
