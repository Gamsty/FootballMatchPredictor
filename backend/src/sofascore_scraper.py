"""
sofascore.com lineup + injury scraper.

⚠ KNOWN-BROKEN AS OF 2026-05-14 ⚠
----------------------------------
sofascore now layers fingerprint checks on top of Cloudflare's standard JS
challenge. cloudscraper passes the JS challenge but the follow-up returns
403 Forbidden — confirmed via prod logs hitting /sport/football/scheduled-events/.
A working bypass would need playwright (real browser) which we judged too
heavy for the marginal feature gain.

This module is kept in the repo as a STUB / future-reference. The matching
GitHub Actions cron (scrape-lineups.yml) has been disabled. The admin
endpoint (/api/admin/scrape-lineups) returns gracefully with empty results
when sofascore 403s. If we ever switch lineup sources (fotmob unofficial
API or api-football paid), only the fetch_* functions need to swap — the
JSON output shape from `fetch_lineups()` is what lineups_scrape.py depends on.

WHY SOFASCORE WAS PICKED ORIGINALLY
-----------------------------------
- Free, covers every league we care about (including Eredivisie / Primeira
  Liga / Championship — where api-football's free tier failed us)
- Has pre-match lineups (typically posted ~1h before kickoff) AND injuries
- Public JSON API at api.sofascore.com — not officially documented but stable
  for years until 2026

WHY CLOUDSCRAPER (kept for reference)
-------------------------------------
sofascore is fronted by Cloudflare. Plain `requests` gets 403s on most calls.
`cloudscraper` wraps requests, solves the JS challenge on first hit, and
caches the cf_clearance cookie for subsequent calls. Works without a
headless browser — much lighter for a container. Stopped working when
sofascore added fingerprint validation.

API SHAPE WE USE
----------------
- GET /api/v1/event/{event_id}/lineups   → starting XI + bench + missing players
- GET /api/v1/team/{team_id}/players     → squad with `missingType` and `reason`
- GET /api/v1/sport/football/scheduled-events/{date}  → match list to resolve event_id

ETHICS / TOS
------------
sofascore's robots.txt and ToS technically restrict scraping. For a personal
research/betting tool with sub-100 reqs/day, we're in gray-area-but-tolerated
territory. We respect:
  - 1.5s minimum between requests
  - Identifiable User-Agent
  - No parallelism, no retries beyond cloudflare's challenge
If they ever rate-limit us, we step away rather than escalate. The whole
module is also wrapped so a hard failure here doesn't crash the prediction
flow — lineups are an enrichment, not a hard requirement.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

try:
    import cloudscraper
    _SCRAPER_AVAILABLE = True
except ImportError:
    _SCRAPER_AVAILABLE = False

logger = logging.getLogger("sofascore")

BASE_URL = "https://api.sofascore.com/api/v1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FootballMatchPredictor/1.0"
REQUEST_DELAY = 1.5  # seconds between requests
REQUEST_TIMEOUT = 20


_scraper = None
_last_request_at = 0.0


def _get_scraper():
    """
    Lazily build the cloudscraper instance. Building it eagerly at module
    import time runs the JS challenge before we know whether sofascore is
    even going to be used — wasteful and adds startup latency.
    """
    global _scraper
    if not _SCRAPER_AVAILABLE:
        raise RuntimeError(
            "cloudscraper not installed — sofascore scraping disabled. "
            "Add cloudscraper to requirements.txt and rebuild the container."
        )
    if _scraper is None:
        _scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )
        _scraper.headers.update({'User-Agent': USER_AGENT})
    return _scraper


def _throttle():
    """Sleep just enough to honor REQUEST_DELAY since the last request."""
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_at = time.time()


def _get_json(path: str) -> Optional[dict]:
    """
    GET a sofascore API endpoint and return parsed JSON. None on any failure —
    callers MUST handle the None case. We deliberately don't raise because
    lineup data is enrichment, not critical.
    """
    if not _SCRAPER_AVAILABLE:
        return None
    _throttle()
    url = f"{BASE_URL}{path}"
    try:
        scraper = _get_scraper()
        r = scraper.get(url, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logger.warning("sofascore fetch failed (%s): %s", path, e)
        return None
    if r.status_code == 404:
        # Match doesn't exist or lineup not yet posted. Common case — don't
        # warn-log this.
        return None
    if r.status_code != 200:
        logger.warning("sofascore %s on %s — body: %s", r.status_code, path, r.text[:200])
        return None
    try:
        return r.json()
    except ValueError as e:
        logger.warning("sofascore returned non-JSON for %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Event discovery — find sofascore's internal event_id from (date, teams)
# ---------------------------------------------------------------------------

def find_event_id(match_date_iso: str, home_team: str, away_team: str) -> Optional[int]:
    """
    Resolve a (date, home, away) tuple to sofascore's event_id.

    `match_date_iso` is YYYY-MM-DD. We query their scheduled-events endpoint
    for that day and match teams by case-insensitive substring — sofascore's
    team names mostly align with football-data's, but we're tolerant.
    """
    date_str = match_date_iso[:10]  # strip time if present
    data = _get_json(f"/sport/football/scheduled-events/{date_str}")
    if not data or 'events' not in data:
        return None

    home_norm = home_team.lower().strip()
    away_norm = away_team.lower().strip()

    for ev in data['events']:
        try:
            h = ev['homeTeam']['name'].lower()
            a = ev['awayTeam']['name'].lower()
        except (KeyError, TypeError):
            continue
        if (home_norm in h or h in home_norm) and (away_norm in a or a in away_norm):
            return int(ev['id'])
    return None


# ---------------------------------------------------------------------------
# Lineup fetch
# ---------------------------------------------------------------------------

def fetch_lineups(event_id: int) -> Optional[dict]:
    """
    Fetch starting XI + bench + missing players for one event.

    Returns dict with keys:
      - confirmed (bool): whether sofascore marks the lineup as confirmed
                          (vs probable/predicted)
      - home_starting (list of {player_id, name, position, jersey})
      - away_starting (same)
      - home_missing (list of {player_id, name, reason})
      - away_missing (same)

    Returns None if lineups aren't posted yet (very common >2h before kickoff).
    """
    data = _get_json(f"/event/{event_id}/lineups")
    if not data:
        return None
    return {
        'confirmed': bool(data.get('confirmed')),
        'home_starting': _extract_starting(data.get('home', {})),
        'away_starting': _extract_starting(data.get('away', {})),
        'home_missing': _extract_missing(data.get('home', {})),
        'away_missing': _extract_missing(data.get('away', {})),
    }


def _extract_starting(team_block: dict) -> list[dict]:
    out = []
    for p in team_block.get('players', []):
        if p.get('substitute'):
            continue
        player = p.get('player') or {}
        out.append({
            'player_id': player.get('id'),
            'name': player.get('name'),
            'position': p.get('position'),
            'jersey': p.get('jerseyNumber'),
        })
    return out


def _extract_missing(team_block: dict) -> list[dict]:
    out = []
    for m in team_block.get('missingPlayers', []) or []:
        player = m.get('player') or {}
        out.append({
            'player_id': player.get('id'),
            'name': player.get('name'),
            'reason': m.get('reason'),       # 1=injured, 2=suspended, etc.
            'type': m.get('type'),           # 'missing' | 'doubtful'
        })
    return out


# ---------------------------------------------------------------------------
# Team-level squad / injuries — useful for pre-week feature generation when
# lineups aren't posted yet
# ---------------------------------------------------------------------------

def fetch_team_injuries(team_id: int) -> Optional[list[dict]]:
    """
    Pull the team's current injury/suspension list (sofascore's `team_id`,
    not football-data's). Each row: {player_id, name, reason, type}.
    """
    data = _get_json(f"/team/{team_id}/players")
    if not data:
        return None
    out = []
    for p in data.get('players', []):
        if p.get('missingType'):  # only those flagged as missing
            player = p.get('player') or {}
            out.append({
                'player_id': player.get('id'),
                'name': player.get('name'),
                'reason': p.get('reason'),
                'type': p.get('missingType'),
            })
    return out
