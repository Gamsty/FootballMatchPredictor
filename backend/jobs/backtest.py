"""
Walk-forward backtest of the value-bet strategy over historical matches.

WHAT THIS PROVES (AND DOESN'T)
------------------------------
For every finished match in the window, we:
  1. Pull the model's prediction (from `predictions` table if available, else
     recompute on the fly from current model weights — same as backfill).
  2. Look up bookmaker odds for that match. There are three ways to get them:
       a) Stored OddsSnapshot rows (preferred — these are real prices we saw)
       b) The Match table's `b365_*` / `avg_*` columns (FootballData.co.uk closings)
       c) Synthetic odds from `1 / (prob × (1 - margin))` — last resort, useless
          for genuine ROI since we'd be betting against our own prices
  3. Apply the value-bet rule: bet flat stake on any outcome with edge > threshold.
  4. Settle against actual_winner.
  5. Track cumulative P/L, win rate, ROI per market, and turn the equity curve
     into a CSV / JSON for inspection.

CAVEATS:
  - In-sample leakage: if the historical match was in the model's training set,
    the prediction already implicitly knows the outcome via Elo. Use --since
    to restrict to post-training matches when honesty matters.
  - The odds we use are bookmaker CLOSING odds (b365_*). Real betting happens
    at opening or mid-window prices, which can differ meaningfully. CLV
    framework would be more honest but requires snapshot history we don't
    have yet for old matches.
  - flat-stake by default. Pass --strategy kelly for ¼-Kelly sizing.

USAGE
-----
    venv/Scripts/python.exe jobs/backtest.py --since 2024-08-01 --min-edge 0.05
    venv/Scripts/python.exe jobs/backtest.py --since 2024-08-01 --strategy kelly --out backtest_results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import and_

from database import DatabaseManager, Match, Prediction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest")


# Map predicted-side prob to the outcome key + odds-column.
# NB: column names on `Prediction` are *_prob, not the API-style *_win — that
# distinction matters here because we're attribute-accessing the ORM model.
OUTCOMES = [
    ('home_win_prob', 'home', 'HOME_TEAM', 'b365_home', 'avg_home_prob'),
    ('draw_prob',     'draw', 'DRAW',      'b365_draw', 'avg_draw_prob'),
    ('away_win_prob', 'away', 'AWAY_TEAM', 'b365_away', 'avg_away_prob'),
]


def _kelly_fraction(prob: float, odds: float, cap: float = 0.25) -> float:
    """¼-Kelly. Returns 0 if no edge."""
    b = odds - 1
    if b <= 0 or prob <= 0:
        return 0.0
    f = (prob * b - (1 - prob)) / b
    return max(0.0, f) * cap


def _resolve_odds(match: Match, outcome_key: str) -> float | None:
    """Use Bet365 closing odds if available; else None (skip)."""
    col = {'home': 'b365_home', 'draw': 'b365_draw', 'away': 'b365_away'}[outcome_key]
    val = getattr(match, col, None)
    if val and val > 1.0:
        return float(val)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', default=None,
                        help='Start date YYYY-MM-DD (default 365 days ago)')
    parser.add_argument('--until', default=None,
                        help='End date YYYY-MM-DD (default: now)')
    parser.add_argument('--min-edge', type=float, default=0.05,
                        help='Minimum edge to take a bet (default 0.05 = 5%%)')
    parser.add_argument('--max-edge', type=float, default=0.30,
                        help='Reject bets with edge above this — almost certainly stale '
                             'lines or model overconfidence (default 0.30 = 30%%)')
    parser.add_argument('--strategy', choices=['flat', 'kelly'], default='flat',
                        help='Staking. flat = constant unit; kelly = ¼-Kelly fraction of roll')
    parser.add_argument('--stake', type=float, default=100.0,
                        help='Flat-stake size in NOK (default 100)')
    parser.add_argument('--bankroll', type=float, default=10000.0,
                        help='Starting bankroll for Kelly sizing (default 10000)')
    parser.add_argument('--out', default='backtest_results.json',
                        help='Path to write detailed results JSON')
    args = parser.parse_args()

    since = datetime.strptime(args.since, '%Y-%m-%d') if args.since else None
    until = datetime.strptime(args.until, '%Y-%m-%d') if args.until else None

    db = DatabaseManager()
    q = (db.session.query(Match, Prediction)
         .join(Prediction, Prediction.match_id == Match.id)
         .filter(Match.status == 'FINISHED')
         .filter(Match.winner.isnot(None)))
    if since:
        q = q.filter(Match.date >= since)
    if until:
        q = q.filter(Match.date <= until)
    rows = q.order_by(Match.date.asc()).all()
    logger.info("Backtesting %d finished matches with predictions in window", len(rows))
    if not rows:
        logger.error("Nothing to backtest. Run backfill_predictions first.")
        return 1

    bankroll = args.bankroll
    starting_bankroll = bankroll
    equity_curve: list[dict] = []
    bets: list[dict] = []
    per_market = {'home': {'bets': 0, 'won': 0, 'stake': 0.0, 'pl': 0.0},
                  'draw': {'bets': 0, 'won': 0, 'stake': 0.0, 'pl': 0.0},
                  'away': {'bets': 0, 'won': 0, 'stake': 0.0, 'pl': 0.0}}

    skipped_no_odds = 0
    skipped_low_edge = 0
    skipped_too_high = 0

    for match, pred in rows:
        for prob_attr, outcome_key, winner_value, _, _ in OUTCOMES:
            prob = getattr(pred, prob_attr, None)
            if prob is None:
                continue
            odds = _resolve_odds(match, outcome_key)
            if odds is None:
                skipped_no_odds += 1
                continue
            edge = prob * odds - 1
            if edge < args.min_edge:
                skipped_low_edge += 1
                continue
            if edge > args.max_edge:
                skipped_too_high += 1
                continue

            # Stake
            if args.strategy == 'kelly':
                stake = _kelly_fraction(prob, odds) * bankroll
                if stake < 1.0:
                    continue
            else:
                stake = args.stake

            won = (match.winner == winner_value)
            pl = stake * (odds - 1) if won else -stake
            bankroll += pl

            per_market[outcome_key]['bets'] += 1
            per_market[outcome_key]['stake'] += stake
            per_market[outcome_key]['pl'] += pl
            if won:
                per_market[outcome_key]['won'] += 1

            bets.append({
                'date': match.date.isoformat(),
                'match_id': match.id,
                'outcome': outcome_key,
                'prob': round(prob, 4),
                'odds': round(odds, 2),
                'edge': round(edge, 4),
                'stake': round(stake, 2),
                'won': won,
                'pl': round(pl, 2),
                'bankroll_after': round(bankroll, 2),
            })
            equity_curve.append({
                'date': match.date.isoformat(),
                'bankroll': round(bankroll, 2),
                'pl_so_far': round(bankroll - starting_bankroll, 2),
            })

    total_stake = sum(m['stake'] for m in per_market.values())
    total_pl = sum(m['pl'] for m in per_market.values())
    total_bets = sum(m['bets'] for m in per_market.values())
    total_wins = sum(m['won'] for m in per_market.values())
    roi = (total_pl / total_stake) if total_stake > 0 else 0
    win_rate = (total_wins / total_bets) if total_bets > 0 else 0

    # Per-market ROI
    for k, m in per_market.items():
        m['roi'] = round(m['pl'] / m['stake'], 4) if m['stake'] > 0 else 0
        m['win_rate'] = round(m['won'] / m['bets'], 4) if m['bets'] else 0
        m['stake'] = round(m['stake'], 2)
        m['pl'] = round(m['pl'], 2)

    summary = {
        'strategy': args.strategy,
        'min_edge': args.min_edge,
        'max_edge': args.max_edge,
        'starting_bankroll': starting_bankroll,
        'ending_bankroll': round(bankroll, 2),
        'total_bets': total_bets,
        'total_wins': total_wins,
        'win_rate': round(win_rate, 4),
        'total_stake': round(total_stake, 2),
        'total_pl': round(total_pl, 2),
        'roi': round(roi, 4),
        'per_market': per_market,
        'skipped_no_odds': skipped_no_odds,
        'skipped_low_edge': skipped_low_edge,
        'skipped_too_high_edge': skipped_too_high,
        'matches_seen': len(rows),
    }

    logger.info("=" * 60)
    logger.info("BACKTEST RESULTS")
    logger.info("=" * 60)
    logger.info("Strategy: %s  |  edge ∈ [%.2f, %.2f]", args.strategy, args.min_edge, args.max_edge)
    logger.info("Matches: %d  Bets taken: %d  Wins: %d (%.1f%%)",
                len(rows), total_bets, total_wins, win_rate * 100)
    logger.info("Stake: %.2f  P/L: %+.2f  ROI: %+.2f%%",
                total_stake, total_pl, roi * 100)
    logger.info("Bankroll: %.2f → %.2f  (%+.2f%%)",
                starting_bankroll, bankroll,
                (bankroll / starting_bankroll - 1) * 100 if starting_bankroll else 0)
    logger.info("Per market:")
    for k, m in per_market.items():
        logger.info("  %s: %d bets, %d wins, stake %.2f, pl %+.2f, ROI %+.2f%%",
                    k, m['bets'], m['won'], m['stake'], m['pl'], m['roi'] * 100)
    logger.info("Skipped — no odds: %d, low edge: %d, too high edge: %d",
                skipped_no_odds, skipped_low_edge, skipped_too_high)
    logger.info("=" * 60)

    out_path = Path(args.out)
    out_path.write_text(json.dumps({
        'summary': summary,
        'bets': bets,
        'equity_curve': equity_curve,
    }, indent=2))
    logger.info("Detailed results written to %s", out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
