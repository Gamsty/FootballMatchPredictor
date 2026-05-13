"""
Value-bet detection across multiple markets.

Definitions
-----------
EV / edge:  prob × decimal_odds − 1
    Positive = +EV (book is underpricing this outcome vs our model).
    We compute two flavors:
      - edge_best   uses the highest priced trusted book → "where to bet"
      - edge_median uses the median of trusted books    → "market reference"
    A large gap (edge_best ≫ edge_median) signals an outlier price.

Kelly fraction:  f* = (p·b − q) / b   where b = decimal_odds − 1, q = 1−p
    Long-run growth-maximising bankroll fraction under repeated independent bets.
    Use fractional Kelly (typically ¼) in practice — model probabilities are
    noisy estimates and full Kelly is brutal when overconfident.

Markets supported
-----------------
- h2h          (1X2 — Home / Draw / Away)
- totals 2.5   (Over / Under 2.5 goals)
- btts         (Both Teams to Score — Yes / No)

The model side comes from `predict_all_markets()` in prediction_service.py:
    match_result.probabilities   → home_win, draw, away_win
    markets.over_2_5.probabilities → Over, Under
    markets.btts.probabilities    → Yes, No
"""

from __future__ import annotations


def compute_edge(prob: float, decimal_odds: float) -> float:
    return prob * decimal_odds - 1.0


def compute_kelly(prob: float, decimal_odds: float) -> float:
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f = (prob * b - (1.0 - prob)) / b
    return max(0.0, f)


def _make_pick(outcome_label, outcome_key, market, prob, odds_bucket):
    """
    Build the standard pick payload from a (prob, aggregated_odds) pair.

    odds_bucket has shape {'best': {price, bookmaker}, 'median': float, 'count': int}
    so we can report both flavors of edge alongside the actual betting price.
    """
    best = odds_bucket.get('best') or {}
    best_price = best.get('price')
    median_price = odds_bucket.get('median')
    if not best_price or not median_price:
        return None
    edge_best = compute_edge(prob, best_price)
    edge_median = compute_edge(prob, median_price)
    return {
        'outcome':       outcome_label,
        'outcome_key':   outcome_key,
        'market':        market,
        'prob':          round(float(prob), 4),
        'odds':          round(float(best_price), 2),
        'odds_median':   round(float(median_price), 2),
        'bookmaker':     best.get('bookmaker') or '?',
        'book_count':    odds_bucket.get('count', 1),
        'edge':          round(edge_best, 4),     # legacy field (= edge_best)
        'edge_best':     round(edge_best, 4),
        'edge_median':   round(edge_median, 4),
        'kelly':         round(compute_kelly(prob, best_price), 4),
    }


# ----------------------------------------------------------------------------
# Per-market value scanning
# ----------------------------------------------------------------------------

def _h2h_picks(prediction: dict, odds: dict, min_edge: float, use_median: bool) -> list[dict]:
    probs = (prediction.get('match_result') or {}).get('probabilities') or {}
    h2h = odds.get('h2h')
    if not h2h or not probs:
        return []
    out = []
    for prob_key, odds_key, label in [
        ('home_win', 'home', 'Home Win'),
        ('draw',     'draw', 'Draw'),
        ('away_win', 'away', 'Away Win'),
    ]:
        p = probs.get(prob_key)
        bucket = h2h.get(odds_key)
        if not p or not bucket:
            continue
        pick = _make_pick(label, odds_key, 'h2h', p, bucket)
        if pick is None:
            continue
        gate = pick['edge_median'] if use_median else pick['edge_best']
        if gate >= min_edge:
            out.append(pick)
    return out


def _totals_picks(prediction: dict, odds: dict, min_edge: float, use_median: bool) -> list[dict]:
    # Our multi-market model uses key 'over_2_5' with labels {'Over', 'Under'}
    market = (prediction.get('markets') or {}).get('over_2_5')
    if not market or 'probabilities' not in market:
        return []
    probs = market['probabilities']
    totals = odds.get('totals')
    if not totals:
        return []
    out = []
    for prob_key, odds_key, label in [
        ('Over',  'over',  'Over 2.5 Goals'),
        ('Under', 'under', 'Under 2.5 Goals'),
    ]:
        p = probs.get(prob_key)
        bucket = totals.get(odds_key)
        if not p or not bucket:
            continue
        pick = _make_pick(label, odds_key, 'totals_2_5', p, bucket)
        if pick is None:
            continue
        gate = pick['edge_median'] if use_median else pick['edge_best']
        if gate >= min_edge:
            out.append(pick)
    return out


def _btts_picks(prediction: dict, odds: dict, min_edge: float, use_median: bool) -> list[dict]:
    market = (prediction.get('markets') or {}).get('btts')
    if not market or 'probabilities' not in market:
        return []
    probs = market['probabilities']
    btts = odds.get('btts')
    if not btts:
        return []
    out = []
    for prob_key, odds_key, label in [
        ('Yes', 'yes', 'BTTS Yes'),
        ('No',  'no',  'BTTS No'),
    ]:
        p = probs.get(prob_key)
        bucket = btts.get(odds_key)
        if not p or not bucket:
            continue
        pick = _make_pick(label, odds_key, 'btts', p, bucket)
        if pick is None:
            continue
        gate = pick['edge_median'] if use_median else pick['edge_best']
        if gate >= min_edge:
            out.append(pick)
    return out


# ----------------------------------------------------------------------------
# Top-level entry
# ----------------------------------------------------------------------------

def value_picks(
    match_prediction: dict | None,
    odds: dict | None,
    min_edge: float = 0.03,
    use_median: bool = True,
) -> list[dict]:
    """
    Return all +EV picks across every market we can resolve odds for.

    Args:
        match_prediction: The output of predict_all_markets() for one match,
            OR (for legacy callers) a dict with just 'probabilities' under top
            level — handled by being lenient about the 'match_result' wrapper.
        odds: The output of OddsAPIClient.odds_for_match() — nested by market.
            For legacy callers passing the flat h2h-only shape, we adapt it.
        min_edge: Threshold gate. Default 3%.
        use_median: When True (default), filter by edge_median — more honest
            given outlier prices on the "best" book. When False, filter by
            edge_best — looser, surfaces stale-line opportunities.

    Returns sorted-by-edge-desc list (using whichever edge flavour gated).
    """
    if not match_prediction or not odds:
        return []

    # Adapt legacy callers: if `match_prediction` is the flat {probabilities: ...}
    # shape from predict_match_result, wrap it in {match_result: ...}.
    if 'match_result' not in match_prediction and 'probabilities' in match_prediction:
        match_prediction = {'match_result': match_prediction}

    # Adapt legacy odds shape: bare {home, draw, away} → {h2h: {home: {best: ..., median, count}}}.
    if 'h2h' not in odds and any(k in odds for k in ('home', 'draw', 'away')):
        odds = {'h2h': {
            k: {'best': v, 'median': (v or {}).get('price'), 'count': 1}
            for k, v in odds.items()
            if v is not None
        }}

    picks = (
        _h2h_picks(match_prediction, odds, min_edge, use_median)
        + _totals_picks(match_prediction, odds, min_edge, use_median)
        + _btts_picks(match_prediction, odds, min_edge, use_median)
    )

    key = 'edge_median' if use_median else 'edge_best'
    picks.sort(key=lambda p: p[key], reverse=True)
    return picks
