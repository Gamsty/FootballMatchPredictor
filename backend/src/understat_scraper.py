"""
understat.com xG scraper.

understat publishes Expected-Goals data as JSON embedded in `<script>` tags on
match/team/league pages. No public API, no auth — we just parse the HTML.

LEAGUE COVERAGE
---------------
understat covers EPL, La Liga, Bundesliga, Serie A, Ligue 1 + Russian
Premier League. NOT Eredivisie, Primeira Liga, Championship — exactly the
leagues your audit found to have the most model edge. So xG features will
mostly improve top-5 prediction quality, not the most-profitable buckets.
That's still worth doing because:
  1. Top-5 ROI is currently flat/negative — xG might unlock value there
  2. Calibration on draws is worst; xG helps draw prediction specifically
  3. Code is reusable when we add fbref (Eredivisie, etc.) later

API SHAPE
---------
- `fetch_team_season(team_slug, year)` → list of {date, h_xg, a_xg, h_goals, a_goals, ...}
- `fetch_match(match_id)` → {shots, summary} — heavier, used for per-shot xG

For training features we mostly need season-level + recent-form aggregations,
so team-season is the primary entry point.

RATE LIMITING + TOS
-------------------
understat is permissive but courteous: 1 req/sec, identify yourself in User-Agent,
back off on 429. We respect that. Production usage stays in single-digit
requests per scheduled scrape.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger("understat")

BASE_URL = "https://understat.com"
USER_AGENT = "FootballMatchPredictor/1.0 (research; respectful rate limit)"
REQUEST_TIMEOUT = 15
DEFAULT_DELAY = 1.0  # seconds between requests

# Slug-style league IDs that understat uses in URLs.
LEAGUE_SLUGS = {
    'Premier League':     'EPL',
    'La Liga':            'La_liga',
    'Primera Division':   'La_liga',
    'Bundesliga':         'Bundesliga',
    'Serie A':            'Serie_A',
    'Ligue 1':            'Ligue_1',
    # No mapping for Eredivisie / Primeira Liga / Championship / RPL —
    # those leagues aren't on understat. Callers should check before fetching.
}


def _http_get(url: str, *, retries: int = 2) -> Optional[str]:
    """GET with a single transient retry. Returns None on permanent error."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            if attempt == retries:
                logger.warning("understat fetch failed (gave up): %s — %s", url, e)
                return None
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 503):
            # Rate-limited or briefly unavailable — back off and retry.
            wait = (2 ** attempt) * 2
            logger.info("understat %s on %s, sleeping %ds", r.status_code, url, wait)
            time.sleep(wait)
            continue
        logger.warning("understat %s on %s — not retrying", r.status_code, url)
        return None
    return None


def _extract_json_var(html: str, var_name: str) -> Optional[list | dict]:
    """
    Pull `var <var_name> = JSON.parse('<escaped json>');` out of the HTML.

    understat does this on every page. The string is JSON-escaped twice
    (once for JS string literal, once for JSON.parse), so we have to undo
    the unicode-escape layer before parsing.
    """
    pattern = re.compile(
        rf"var\s+{re.escape(var_name)}\s*=\s*JSON\.parse\('([^']+)'\)",
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    try:
        decoded = m.group(1).encode('utf-8').decode('unicode_escape')
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("Failed to parse understat %s payload: %s", var_name, e)
        return None


def fetch_league_season(competition: str, year: int) -> list[dict] | None:
    """
    Fetch all matches for one league × season. Returns a list of match dicts
    with keys: id, isResult, h{id,title,short_title}, a{id,...}, goals{h,a},
    xG{h,a}, datetime, forecast{w,d,l}.

    Returns None for unsupported leagues or on fetch error.
    """
    slug = LEAGUE_SLUGS.get(competition)
    if not slug:
        logger.info("understat: no slug for competition %r — skipping", competition)
        return None
    url = f"{BASE_URL}/league/{slug}/{year}"
    html = _http_get(url)
    if html is None:
        return None
    matches = _extract_json_var(html, "datesData")
    if not isinstance(matches, list):
        return None
    return matches


def fetch_team_season(team_id: int, year: int) -> dict | None:
    """
    Fetch a single team's full season — match-by-match xG, ppda, etc.

    `team_id` is understat's internal ID (visible in their URLs). We don't
    store these long-term — caller should resolve team-name → id via
    fetch_league_season() first, then cache the mapping.
    """
    url = f"{BASE_URL}/team/{team_id}/{year}"
    html = _http_get(url)
    if html is None:
        return None
    matches = _extract_json_var(html, "datesData")
    if matches is None:
        return None
    return {
        'team_id': team_id,
        'year': year,
        'matches': matches,
    }


def extract_match_xg(match: dict) -> dict | None:
    """
    Normalize one match dict from understat into a flat (date, home, away,
    xg_home, xg_away, goals_home, goals_away) row. Returns None for
    unfinished matches (isResult is False).
    """
    if not match.get('isResult'):
        return None
    try:
        h = match.get('h') or {}
        a = match.get('a') or {}
        xg = match.get('xG') or {}
        goals = match.get('goals') or {}
        return {
            'understat_match_id': int(match['id']),
            'date': match.get('datetime'),  # 'YYYY-MM-DD HH:MM:SS' UTC
            'home_team': h.get('title'),
            'away_team': a.get('title'),
            'home_team_id': int(h.get('id')) if h.get('id') else None,
            'away_team_id': int(a.get('id')) if a.get('id') else None,
            'xg_home': float(xg.get('h', 0)),
            'xg_away': float(xg.get('a', 0)),
            'goals_home': int(goals.get('h', 0)) if goals.get('h') is not None else None,
            'goals_away': int(goals.get('a', 0)) if goals.get('a') is not None else None,
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Skipping malformed understat match: %s — %s", match.get('id'), e)
        return None


def scrape_season_xg(competition: str, year: int,
                     polite_delay: float = DEFAULT_DELAY) -> list[dict]:
    """
    Top-level convenience: fetch + normalize all finished matches for one
    competition × season. Sleeps `polite_delay` after the request to stay
    under understat's implicit rate budget.
    """
    raw = fetch_league_season(competition, year)
    if polite_delay > 0:
        time.sleep(polite_delay)
    if raw is None:
        return []
    rows: list[dict] = []
    for m in raw:
        normalized = extract_match_xg(m)
        if normalized:
            rows.append(normalized)
    logger.info("understat %s %d: %d finished matches with xG", competition, year, len(rows))
    return rows
