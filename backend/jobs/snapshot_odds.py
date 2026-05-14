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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import DatabaseManager
from odds_api import OddsAPIClient
# Re-export run_snapshot for backward compat with any external callers that
# imported it from here. The shared implementation now lives in src/.
from odds_snapshot import run_snapshot  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("snapshot_odds")


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
    markets = tuple(args.markets.split(','))
    summary = run_snapshot(
        db=db,
        client=client,
        hours=args.hours,
        closing=args.closing,
        closing_window_hours=args.closing_window,
        apply_to_bets=args.apply_to_bets,
        markets=markets,
    )
    db.session.commit()
    logger.info("Wrote %d snapshots", summary.get('snapshots_written', 0))
    if args.apply_to_bets:
        logger.info("Updated closing_odds on %d pending bets", summary.get('bets_updated', 0))
    return 0 if summary.get('enabled') else 1


if __name__ == '__main__':
    sys.exit(main())
