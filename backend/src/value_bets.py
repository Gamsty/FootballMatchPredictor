"""
Value-bet detection: combine model probabilities with bookmaker odds to find
positive-EV picks.

Definitions
-----------
EV (edge): edge = prob × decimal_odds − 1
    edge > 0 → +EV — bookmaker is underpricing this outcome relative to our model
    edge ≤ 0 → −EV — model agrees with book, no value

Kelly fraction: f* = (prob × b − (1 − prob)) / b   where b = decimal_odds − 1
    The fraction of bankroll that maximises long-run log-wealth growth under
    repeated independent bets. In practice always use fractional Kelly
    (typically Kelly/4) to mitigate the variance from model probabilities
    being noisy estimates — full Kelly assumes the model probability is true,
    which it never quite is.

Threshold (min_edge):
    Filters out marginal bets where the edge is within model-error noise.
    Typical values: 0.02–0.05. Below 0.02 you're betting on rounding error;
    above 0.05 you'll find very few opportunities except on misprice events.
"""

from __future__ import annotations


def compute_edge(prob: float, decimal_odds: float) -> float:
    """Expected value per unit stake. Positive = +EV."""
    return prob * decimal_odds - 1.0


def compute_kelly(prob: float, decimal_odds: float) -> float:
    """
    Kelly stake fraction. Returns 0 if the bet has no edge (avoids negative stakes).
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f = (prob * b - (1.0 - prob)) / b
    return max(0.0, f)


def value_picks(
    match_prediction: dict | None,
    best_odds: dict | None,
    min_edge: float = 0.03,
) -> list[dict]:
    """
    Return all +EV picks for a single match's 1X2 market.

    Args:
        match_prediction: dict with 'probabilities': {'home_win', 'draw', 'away_win'}
        best_odds: dict from OddsAPIClient.best_odds_for_match()
            {'home': {'price', 'bookmaker'}, 'draw': {...}, 'away': {...}}
        min_edge: minimum edge to include (default 0.03 = 3%)

    Returns:
        Sorted-by-edge-desc list of:
            {outcome, prob, odds, bookmaker, edge, kelly}
    """
    if not match_prediction or not best_odds:
        return []
    probs = match_prediction.get('probabilities') or {}

    outcome_map = [
        ('home_win', 'home', 'Home Win'),
        ('draw',     'draw', 'Draw'),
        ('away_win', 'away', 'Away Win'),
    ]

    picks: list[dict] = []
    for prob_key, odds_key, label in outcome_map:
        p = probs.get(prob_key)
        o = best_odds.get(odds_key)
        if not p or not o or not o.get('price'):
            continue
        odds = float(o['price'])
        edge = compute_edge(p, odds)
        if edge < min_edge:
            continue
        picks.append({
            'outcome':   label,
            'outcome_key': odds_key,  # 'home' | 'draw' | 'away' — useful for FE styling
            'prob':      round(float(p), 4),
            'odds':      round(odds, 2),
            'bookmaker': o.get('bookmaker') or '?',
            'edge':      round(edge, 4),
            'kelly':     round(compute_kelly(p, odds), 4),
        })

    picks.sort(key=lambda x: x['edge'], reverse=True)
    return picks
