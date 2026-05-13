"""
Snapshot best bookmaker odds for upcoming matches, optionally as the "closing"
price used for CLV calculation.

USE CASES
---------
1. Regular `realtime` snapshots (every 30-60 min via a Container Apps Job cron)
   give us a price-movement history per match. Useful to see how the market is
   moving toward kickoff.

2. A single `closing` snapshot ~1 hour before kickoff. This is the canonical
   reference price for CLV: if you placed a bet at 2.10 and closing is 1.95,
   you "beat the close" by 7.7%. Positive CLV over many bets is the only
   short-run +EV proof that doesn't depend on win/loss noise.

The job is idempotent on (match_id, market, outcome_key, snapshot_at): re-runs
won't duplicate.

USAGE
-----
    # All upcoming matches in the next N hours, default 24:
    venv/Scripts/python.exe jobs/snapshot_odds.py --hours 24

    # Only matches within `closing_window` hours of kickoff (default 2),
    # tagged as 'closing' for CLV:
    venv/Scripts/python.exe jobs/snapshot_odds.py --closing --closing-window 2

    # Also write closing_odds onto any pending bets for those matches:
    venv/Scripts/python.exe jobs/snapshot_odds.py --closing --apply-to-bets
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import Bet, DatabaseManager, Match, OddsSnapshot
from odds_api import OddsAPIClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("snapshot_odds")


# How outcome keys in odds_for_match() map to the (market, outcome_key) we store
MARKET_MAP = [
    ('h2h', 'home'), ('h2h', 'draw'), ('h2h', 'away'),
    ('totals_2_5', 'over'), ('totals_2_5', 'under'),
]


def _walk_odds_buckets(odds: dict):
    """Yield (market, outcome_key, bucket_dict) for every priced outcome."""
    h2h = odds.get('h2h') or {}
    for k in ('home', 'draw', 'away'):
        if h2h.get(k):
            yield 'h2h', k, h2h[k]
    totals = odds.get('totals') or {}
    for k in ('over', 'under'):
        if totals.get(k):
            yield 'totals_2_5', k, totals[k]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=int, default=24,
                        help='Look at matches kicking off within N hours (default 24)')
    parser.add_argument('--closing', action='store_true',
                        help='Tag snapshot as "closing" (vs default "realtime")')
    parser.add_argument('--closing-window', type=float, default=2.0,
                        help='When --closing is set, only snap matches within this many '
                             'hours of kickoff (default 2)')
    parser.add_argument('--apply-to-bets', action='store_true',
                        help='Also write each snapshotted price into matching pending bets '
                             "as closing_odds. Implies --closing semantics.")
    parser.add_argument('--markets', default='h2h',
                        help="Comma-separated markets to fetch (h2h,totals). "
                             "btts not supported on bulk endpoint.")
    args = parser.parse_args()

    client = OddsAPIClient()
    if not client.enabled:
        logger.error("ODDS_API_KEY not set — nothing to snapshot")
        return 1

    db = DatabaseManager()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if args.closing or args.apply_to_bets:
        lower = now
        upper = now + timedelta(hours=args.closing_window)
        snapshot_type = 'closing'
    else:
        lower = now
        upper = now + timedelta(hours=args.hours)
        snapshot_type = 'realtime'

    # Cover all non-finished statuses. football-data.org uses SCHEDULED, TIMED,
    # POSTPONED, SUSPENDED, IN_PLAY, PAUSED, AWARDED, CANCELLED — only FINISHED
    # is settled. We exclude FINISHED + CANCELLED so closing snapshots don't
    # waste API credits on dead fixtures.
    matches = (
        db.session.query(Match)
        .filter(~Match.status.in_(('FINISHED', 'CANCELLED')))
        .filter(Match.date >= lower)
        .filter(Match.date <= upper)
        .all()
    )
    logger.info("Snapshotting %d upcoming matches (type=%s, window=%s..%s)",
                len(matches), snapshot_type, lower.date(), upper.date())

    markets = tuple(m for m in args.markets.split(',') if m in ('h2h', 'totals')) or ('h2h',)

    snaps_written = 0
    bets_updated = 0

    for match in matches:
        try:
            odds = client.odds_for_match(
                match.competition, match.home_team.name, match.away_team.name,
                markets=markets,
            )
        except Exception as e:
            logger.warning("Fetch failed match_id=%s: %s", match.id, e)
            continue
        if not odds:
            continue

        for market, outcome_key, bucket in _walk_odds_buckets(odds):
            best = bucket.get('best') or {}
            best_price = best.get('price')
            if not best_price:
                continue
            db.session.add(OddsSnapshot(
                match_id=match.id,
                market=market,
                outcome_key=outcome_key,
                best_odds=float(best_price),
                best_bookmaker=best.get('bookmaker'),
                median_odds=bucket.get('median'),
                book_count=bucket.get('count', 1),
                snapshot_type=snapshot_type,
                snapshot_at=now,
            ))
            snaps_written += 1

            if args.apply_to_bets:
                # Update any pending bet on this match/market/outcome with closing odds
                bets = (db.session.query(Bet)
                        .filter_by(match_id=match.id, market=market,
                                   outcome_key=outcome_key, status='pending')
                        .all())
                for b in bets:
                    b.closing_odds = float(best_price)
                    b.closing_snapshot_at = now
                    bets_updated += 1

    db.session.commit()
    logger.info("Wrote %d snapshots", snaps_written)
    if args.apply_to_bets:
        logger.info("Updated closing_odds on %d pending bets", bets_updated)
    return 0


if __name__ == '__main__':
    sys.exit(main())
