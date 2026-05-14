"""
Sync Eliteserien fixtures + results from api-football.com into the local DB.

WHY
---
Eliteserien isn't in football-data.org's free tier, so it doesn't get refreshed
by the normal /api/fixtures/refresh pipeline. This script bridges that gap
using api-football.com's free tier (100 requests/day, plenty for one league).

USAGE
-----
    # Requires API_FOOTBALL_KEY in env (or .env). Free signup at api-sports.io.
    cd backend
    venv/Scripts/python.exe jobs/refresh_eliteserien.py
    venv/Scripts/python.exe jobs/refresh_eliteserien.py --season 2026 --days-ahead 14

TEAM NAME MAPPING
-----------------
api-football names (e.g. "Bodø / Glimt") may differ from any names already in
our DB. The team lookup cascade is:
    1. Exact name match in Team table, scope competition='Eliteserien'
    2. Fuzzy match (rapidfuzz token_set_ratio ≥ 85)
    3. Create new Team row with synthetic api_id (1_100_000 + offset)

Subsequent runs see the team already exists and update it instead of duplicating.

QUOTA
-----
One fetch covers all fixtures in a date range; we typically use ~2-4 requests
per run (one fixtures call, possibly one teams call if seeding). Set up as a
daily cron to stay well under the 100/day cap.
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

from api_football_client import APIFootballClient
from database import DatabaseManager, Match, Team

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("refresh_eliteserien")


# api-football league id for Eliteserien — verified via /leagues?country=Norway
ELITESERIEN_LEAGUE_ID = 103
COMPETITION_LABEL = 'Eliteserien'

# Synthetic api_id offset for Norwegian teams created by this script. Keeps
# them disjoint from football-data.org's id space (their ids are <100_000).
SYNTHETIC_TEAM_API_ID_OFFSET = 1_100_000


# api-football status code → our internal status
STATUS_MAP = {
    'NS':   'SCHEDULED',
    'TBD':  'SCHEDULED',
    '1H':   'IN_PLAY',
    'HT':   'IN_PLAY',
    '2H':   'IN_PLAY',
    'ET':   'IN_PLAY',
    'P':    'IN_PLAY',
    'FT':   'FINISHED',
    'AET':  'FINISHED',
    'PEN':  'FINISHED',
    'PST':  'POSTPONED',
    'CANC': 'CANCELLED',
    'ABD':  'CANCELLED',
    'AWD':  'AWARDED',
    'WO':   'AWARDED',
    'SUSP': 'SUSPENDED',
}


def _winner_from_goals(home: int | None, away: int | None) -> str | None:
    """Compute WLD label from scores. None when scores unknown."""
    if home is None or away is None:
        return None
    if home > away:
        return 'HOME_TEAM'
    if away > home:
        return 'AWAY_TEAM'
    return 'DRAW'


def _normalize_name(name: str) -> str:
    """Lowercase + collapse whitespace + drop slashes for matching purposes."""
    if not name:
        return ''
    return ' '.join(name.lower().replace('/', ' ').replace('-', ' ').split())


def _find_or_create_team(session, api_team: dict, next_synth_id: list[int]) -> Team:
    """
    Look up an existing Eliteserien team by name; if absent, create a new one.

    next_synth_id is a single-element list so we can mutate it across calls
    without making this a class. Caller initializes it to the next available
    synthetic id at start of run.
    """
    name_raw = api_team.get('name') or ''
    norm = _normalize_name(name_raw)

    # 1. Direct match
    existing = (session.query(Team)
                .filter(Team.competition == COMPETITION_LABEL)
                .all())
    for t in existing:
        if _normalize_name(t.name) == norm:
            return t

    # 2. Fuzzy match — rapidfuzz already a dependency
    from rapidfuzz import fuzz
    best, best_score = None, 0
    for t in existing:
        s = fuzz.token_set_ratio(norm, _normalize_name(t.name))
        if s > best_score:
            best, best_score = t, s
    if best is not None and best_score >= 85:
        logger.info("Fuzzy-matched '%s' → existing '%s' (score=%d)",
                    name_raw, best.name, best_score)
        return best

    # 3. Create new
    synth_id = next_synth_id[0]
    next_synth_id[0] += 1
    team = Team(
        api_id=synth_id,
        name=name_raw,
        short_name=name_raw.split()[0][:50],
        competition=COMPETITION_LABEL,
    )
    session.add(team)
    session.flush()  # so team.id is available before commit
    logger.info("Created team '%s' (id=%d, api_id=%d)", name_raw, team.id, synth_id)
    return team


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int, default=None,
                        help='Season year (default: current calendar year, since '
                             'Eliteserien runs March–November in one year)')
    parser.add_argument('--days-ahead', type=int, default=14,
                        help='Fetch fixtures up to N days into the future (default 14)')
    parser.add_argument('--days-back', type=int, default=7,
                        help='Also re-sync results for matches in last N days (default 7) '
                             'so newly finished games get scores + winners')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be done, do not write to DB')
    args = parser.parse_args()

    client = APIFootballClient()
    if not client.enabled:
        logger.error("API_FOOTBALL_KEY not set. Get one at https://api-sports.io and "
                     "add to backend/.env.")
        return 1

    season = args.season or datetime.now(timezone.utc).year
    now = datetime.now(timezone.utc).date()
    date_from = (now - timedelta(days=args.days_back)).isoformat()
    date_to = (now + timedelta(days=args.days_ahead)).isoformat()

    logger.info("Fetching Eliteserien fixtures: season=%d window %s..%s",
                season, date_from, date_to)
    fixtures = client.fetch_fixtures(
        ELITESERIEN_LEAGUE_ID, season,
        date_from=date_from, date_to=date_to,
    )
    if not fixtures:
        logger.warning("No fixtures returned. Possible causes: season=%d wrong, "
                       "API key invalid, or quota exhausted. Quota: %s",
                       season, client.quota_status())
        return 1

    logger.info("Fetched %d fixtures (quota: %s)", len(fixtures), client.quota_status())

    db = DatabaseManager()

    # Seed next_synth_id from highest existing synthetic id in Eliteserien
    max_id = (db.session.query(Team.api_id)
              .filter(Team.competition == COMPETITION_LABEL)
              .filter(Team.api_id >= SYNTHETIC_TEAM_API_ID_OFFSET)
              .order_by(Team.api_id.desc())
              .first())
    next_synth_id = [(max_id[0] + 1) if max_id else SYNTHETIC_TEAM_API_ID_OFFSET]

    added = 0
    updated = 0
    skipped = 0

    for fx in fixtures:
        try:
            fixture = fx.get('fixture', {})
            teams = fx.get('teams', {})
            goals = fx.get('goals', {})

            api_id = fixture.get('id')
            fixture_date = fixture.get('date')  # ISO 8601
            status_short = (fixture.get('status') or {}).get('short')
            our_status = STATUS_MAP.get(status_short, 'SCHEDULED')

            if not api_id or not fixture_date or not teams.get('home') or not teams.get('away'):
                skipped += 1
                continue

            # Resolve teams
            home_team = _find_or_create_team(db.session, teams['home'], next_synth_id)
            away_team = _find_or_create_team(db.session, teams['away'], next_synth_id)

            # Parse fixture date
            dt = datetime.fromisoformat(fixture_date.replace('Z', '+00:00'))
            dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt

            home_score = goals.get('home')
            away_score = goals.get('away')
            winner = _winner_from_goals(home_score, away_score)

            existing = db.session.query(Match).filter_by(api_id=api_id).first()
            if existing:
                existing.status = our_status
                existing.date = dt_naive
                if home_score is not None:
                    existing.home_score = home_score
                if away_score is not None:
                    existing.away_score = away_score
                if winner is not None:
                    existing.winner = winner
                updated += 1
            else:
                m = Match(
                    api_id=api_id,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    season=season,
                    matchday=(fx.get('league') or {}).get('round'),
                    competition=COMPETITION_LABEL,
                    stage='REGULAR_SEASON',
                    date=dt_naive,
                    status=our_status,
                    home_score=home_score,
                    away_score=away_score,
                    winner=winner,
                )
                db.session.add(m)
                added += 1

        except Exception as e:
            logger.warning("Skip fixture id=%s: %s", fx.get('fixture', {}).get('id'), e)
            skipped += 1

    if args.dry_run:
        logger.info("Dry run — rolling back. Would have: added=%d updated=%d skipped=%d",
                    added, updated, skipped)
        db.session.rollback()
    else:
        db.session.commit()
        logger.info("Done. added=%d updated=%d skipped=%d", added, updated, skipped)

    return 0


if __name__ == '__main__':
    sys.exit(main())
