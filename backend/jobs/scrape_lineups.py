"""
CLI wrapper around the lineups_scrape module — for one-off runs and as the
default entrypoint when scheduled as a Container Apps Job. The actual logic
lives in src/lineups_scrape.py so it can be shared with the admin endpoint.

USAGE
-----
    # Default — matches within 6h of kickoff:
    python jobs/scrape_lineups.py

    # Tighter window for the closing-call cron:
    python jobs/scrape_lineups.py --hours 2

    # Force re-fetch even when cached:
    python jobs/scrape_lineups.py --force
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
from lineups_scrape import run_lineup_scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scrape_lineups")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=float, default=6.0,
                        help='Only matches kicking off within N hours (default 6)')
    parser.add_argument('--force', action='store_true',
                        help='Re-fetch even when lineup already cached')
    args = parser.parse_args()

    db = DatabaseManager()
    summary = run_lineup_scrape(
        db=db,
        hours_window=args.hours,
        force=args.force,
    )
    db.session.commit()
    logger.info("Result: %s", summary)
    return 0 if summary.get('enabled') else 1


if __name__ == '__main__':
    sys.exit(main())
