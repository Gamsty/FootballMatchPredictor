"""
Backfill xG values onto matches.xg_home / matches.xg_away from understat.

USAGE
-----
    # All supported leagues, last 3 seasons:
    python jobs/scrape_xg.py

    # Just one league:
    python jobs/scrape_xg.py --competition "Premier League"

    # Specific season:
    python jobs/scrape_xg.py --year 2024

WHY THIS LIVES IN jobs/
-----------------------
Scrape jobs are one-shot — schedule via Container Apps Job or a manual
PowerShell invocation. They don't belong on the request path; understat is
slow to fetch (1-3s per league-season) and we don't want gunicorn workers
held that long.

MATCHING STRATEGY
-----------------
understat doesn't use the same team IDs as football-data.org. We match by:
  1. Date (within ±36h to handle timezone + postponement)
  2. Normalized team names (lowercase, FC/AFC/CF stripped)

Mismatches are logged but don't fail the run — we'd rather backfill 95% and
flag the rest than abort on the first edge case.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import and_

from database import DatabaseManager, Match
from understat_scraper import LEAGUE_SLUGS, scrape_season_xg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scrape_xg")


def _normalize_team(name: str) -> str:
    """Same normalization as odds_api._normalize — keeps matching consistent."""
    if not name:
        return ''
    s = name.lower()
    # Strip leading/trailing variations
    s = re.sub(r'\b(fc|afc|cf|ac|sc|sk|ssc|cd|club|sociedad|deportivo|real)\b', '', s)
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _find_match(db, *, competition: str, date_str: str,
                home_name: str, away_name: str) -> Match | None:
    """
    Resolve a Match row for the (date, home, away) tuple from understat.
    Date window is ±36h to handle UTC vs local kickoff conversions and
    postponements where understat may show original date and our DB has the
    rescheduled one.
    """
    try:
        match_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return None

    lower = match_date - timedelta(hours=36)
    upper = match_date + timedelta(hours=36)

    home_norm = _normalize_team(home_name)
    away_norm = _normalize_team(away_name)

    # Pull candidates in the time window; filter by team name in Python so
    # we get normalized matching (DB doesn't support our normalize function).
    candidates = (
        db.session.query(Match)
        .filter(and_(
            Match.competition == competition,
            Match.date >= lower,
            Match.date <= upper,
        ))
        .all()
    )
    for m in candidates:
        h = _normalize_team(m.home_team.name if m.home_team else '')
        a = _normalize_team(m.away_team.name if m.away_team else '')
        if home_norm in h or h in home_norm:
            if away_norm in a or a in away_norm:
                return m
    return None


def backfill_competition(db, competition: str, year: int) -> dict:
    """
    Scrape one league × season and write xg_home/xg_away onto matching Match rows.
    Returns a summary dict.
    """
    rows = scrape_season_xg(competition, year)
    matched = 0
    skipped = 0
    not_found = 0

    for r in rows:
        match = _find_match(
            db,
            competition=competition,
            date_str=r.get('date') or '',
            home_name=r.get('home_team') or '',
            away_name=r.get('away_team') or '',
        )
        if match is None:
            not_found += 1
            continue

        # Skip if already populated unless it differs significantly (data
        # correction). Threshold of 0.5 xG handles understat correcting a
        # match's xG retroactively when their model improves.
        if match.xg_home is not None and match.xg_away is not None:
            if (abs(match.xg_home - r['xg_home']) < 0.5
                    and abs(match.xg_away - r['xg_away']) < 0.5):
                skipped += 1
                continue

        match.xg_home = r['xg_home']
        match.xg_away = r['xg_away']
        matched += 1

    db.session.commit()
    logger.info(
        "%s %d: matched=%d, skipped=%d (already current), not_found=%d / %d total",
        competition, year, matched, skipped, not_found, len(rows),
    )
    return {
        'competition': competition,
        'year': year,
        'total_understat_rows': len(rows),
        'matched': matched,
        'skipped': skipped,
        'not_found': not_found,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--competition', type=str, default=None,
                        help='Limit to one league. Default: all understat-supported leagues.')
    parser.add_argument('--year', type=int, default=None,
                        help='Limit to one season. Default: last 3 seasons.')
    args = parser.parse_args()

    competitions = (
        [args.competition] if args.competition
        else list(set(LEAGUE_SLUGS.keys()))
    )
    now = datetime.now()
    current_season = now.year if now.month >= 8 else now.year - 1
    years = [args.year] if args.year else list(range(current_season - 2, current_season + 1))

    db = DatabaseManager()
    summaries: list[dict] = []
    for comp in competitions:
        for year in years:
            try:
                s = backfill_competition(db, comp, year)
                summaries.append(s)
            except Exception as e:
                logger.exception("Failed %s %d: %s", comp, year, e)

    total_matched = sum(s['matched'] for s in summaries)
    total_not_found = sum(s['not_found'] for s in summaries)
    logger.info("=== DONE: %d matches updated, %d unmatched ===",
                total_matched, total_not_found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
