"""
Lineup snapshot logic — shared between the CLI job
(backend/jobs/scrape_lineups.py) and the admin endpoint
(/api/admin/scrape-lineups).

Looks for upcoming matches inside the closing window, resolves the sofascore
event_id (caching it on the Match row), fetches starting XI + missing players,
writes the JSON snapshot + a count of missing regular starters onto
MatchFeatures so the model can use it as a feature.

Design notes:
  - Sofascore posts lineups ~1h before kickoff. We try in a wider window
    (default 6h) to be tolerant of early posts, and accept that most early
    calls return None.
  - Each call only writes lineups we DON'T already have for that match, or
    re-writes once when the previous fetch was non-confirmed. That keeps us
    well inside sofascore's tolerance budget.
  - Reasonable failure mode: scrape errors are logged but don't propagate —
    lineup data is enrichment, predictions still work without it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

from database import Match, MatchFeatures
from sofascore_scraper import (
    find_event_id, fetch_lineups, _SCRAPER_AVAILABLE,
)

logger = logging.getLogger("lineups_scrape")


def run_lineup_scrape(
    *,
    db,
    hours_window: float = 6.0,
    force: bool = False,
) -> dict:
    """
    Walk all non-finished matches within `hours_window` of kickoff and fetch
    lineups for those that don't have them yet.

    Args:
        db: DatabaseManager.
        hours_window: only matches kicking off within this many hours from now.
        force: re-fetch even if lineups are already cached. Default False so
               repeat calls within the same window don't re-hit sofascore.

    Returns a summary dict (matches scanned, lineups fetched, errors).
    Caller commits the session.
    """
    if not _SCRAPER_AVAILABLE:
        return {
            'enabled': False,
            'message': 'cloudscraper not installed — install + rebuild container',
        }

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    upper = now + timedelta(hours=hours_window)

    candidates = (
        db.session.query(Match)
        .filter(and_(
            ~Match.status.in_(('FINISHED', 'CANCELLED')),
            Match.date >= now,
            Match.date <= upper,
        ))
        .all()
    )
    logger.info("Lineup scrape: %d candidates in next %sh", len(candidates), hours_window)

    fetched = 0
    not_yet_posted = 0
    not_found = 0
    errors = 0
    skipped = 0

    for match in candidates:
        try:
            # Skip if we already have lineups AND they're not stale OR we're forcing.
            existing = match.lineups
            if existing and not force:
                # Only re-fetch if the cached snapshot is unconfirmed and we're
                # now within 90 min of kickoff (confirmed lineup likely up).
                cached_confirmed = existing.get('confirmed') if isinstance(existing, dict) else False
                close_to_kickoff = (match.date - now).total_seconds() < 90 * 60
                if cached_confirmed or not close_to_kickoff:
                    skipped += 1
                    continue

            # Resolve event_id if we don't have it yet
            event_id = match.sofascore_event_id
            if event_id is None:
                event_id = find_event_id(
                    match_date_iso=match.date.isoformat(),
                    home_team=match.home_team.name if match.home_team else '',
                    away_team=match.away_team.name if match.away_team else '',
                )
                if event_id is None:
                    not_found += 1
                    continue
                match.sofascore_event_id = event_id

            lineups = fetch_lineups(event_id)
            if lineups is None:
                not_yet_posted += 1
                continue

            match.lineups = lineups
            match.lineups_fetched_at = now
            fetched += 1

            # Materialise the count features so the model can read them
            # without parsing the JSON every prediction.
            home_missing_n = len(lineups.get('home_missing') or [])
            away_missing_n = len(lineups.get('away_missing') or [])
            mf = (db.session.query(MatchFeatures)
                  .filter_by(match_id=match.id).first())
            if mf is None:
                mf = MatchFeatures(match_id=match.id)
                db.session.add(mf)
            mf.home_starters_missing = home_missing_n
            mf.away_starters_missing = away_missing_n

        except Exception as e:
            logger.warning("Lineup scrape failed match_id=%s: %s", match.id, e)
            errors += 1

    return {
        'enabled': True,
        'candidates_scanned': len(candidates),
        'lineups_fetched': fetched,
        'not_yet_posted': not_yet_posted,
        'not_found_on_sofascore': not_found,
        'cached_skipped': skipped,
        'errors': errors,
        'window_hours': hours_window,
    }
