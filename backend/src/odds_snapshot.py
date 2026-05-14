"""
Closing-odds snapshot logic — shared between the CLI job (jobs/snapshot_odds.py)
and the admin endpoint (/api/admin/snapshot-closing-odds).

Lives in src/ rather than jobs/ because the production container only ships
src/ — the jobs/ directory is left out of the image to keep it small. The CLI
job script imports run_snapshot from here too.

DESIGN
------
- Caller passes in a DatabaseManager and an OddsAPIClient — keeps this module
  free of import-time side effects (no module-level engine creation).
- Caller is responsible for committing the session after we return. That lets
  the admin endpoint wrap the snapshot in its own request transaction without
  weird double-commit behaviour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from database import Bet, Match, OddsSnapshot

logger = logging.getLogger("odds_snapshot")


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


def run_snapshot(
    *,
    db,
    client,
    hours: int = 24,
    closing: bool = False,
    closing_window_hours: float = 2.0,
    apply_to_bets: bool = False,
    markets: tuple[str, ...] = ('h2h',),
) -> dict:
    """
    Snapshot best/median odds for matches whose kickoff falls inside the window.

    closing=True (or apply_to_bets=True) flips the window to a tight `closing_window_hours`
    window before kickoff and tags snapshots as 'closing'. That's what the
    GitHub Actions cron uses every 30 min.

    Returns a summary dict (matches scanned, snaps written, bets updated,
    quota remaining). Does NOT commit — caller's responsibility.
    """
    if not client.enabled:
        return {'enabled': False, 'message': 'ODDS_API_KEY not set'}

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if closing or apply_to_bets:
        lower = now
        upper = now + timedelta(hours=closing_window_hours)
        snapshot_type = 'closing'
    else:
        lower = now
        upper = now + timedelta(hours=hours)
        snapshot_type = 'realtime'

    # All non-finished statuses are candidates. football-data.org uses
    # SCHEDULED, TIMED, POSTPONED, SUSPENDED, IN_PLAY, PAUSED, AWARDED,
    # CANCELLED — exclude FINISHED + CANCELLED so closing snapshots don't
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

    safe_markets = tuple(m for m in markets if m in ('h2h', 'totals')) or ('h2h',)

    snaps_written = 0
    bets_updated = 0
    fetch_errors = 0

    for match in matches:
        try:
            odds = client.odds_for_match(
                match.competition, match.home_team.name, match.away_team.name,
                markets=safe_markets,
            )
        except Exception as e:
            logger.warning("Fetch failed match_id=%s: %s", match.id, e)
            fetch_errors += 1
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

            if apply_to_bets:
                # Backfill closing_odds on matching pending bets so CLV can be
                # computed once they settle.
                bets = (db.session.query(Bet)
                        .filter_by(match_id=match.id, market=market,
                                   outcome_key=outcome_key, status='pending')
                        .all())
                for b in bets:
                    b.closing_odds = float(best_price)
                    b.closing_snapshot_at = now
                    bets_updated += 1

    return {
        'enabled': True,
        'matches_scanned': len(matches),
        'snapshot_type': snapshot_type,
        'window': {'from': lower.isoformat(), 'to': upper.isoformat()},
        'snapshots_written': snaps_written,
        'bets_updated': bets_updated,
        'fetch_errors': fetch_errors,
        'quota': client.quota_status(),
    }
