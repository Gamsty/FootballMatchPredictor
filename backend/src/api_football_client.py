"""
api-football.com (api-sports.io) thin client — used to fetch fixtures for
leagues NOT covered by football-data.org's free tier.

Currently scoped to Eliteserien (Norwegian top division, league_id=103). The
client itself is generic and can fetch any league; only the refresh job
(jobs/refresh_eliteserien.py) is league-specific.

FREE TIER LIMITS
----------------
100 requests/day. Fixtures endpoint returns up to ~10 matches per fetch when
date-filtered to a week or so. Running the refresh job once daily is well
within budget.

Headers note: api-football is reachable via two hosts, with the same key:
    1. https://v3.football.api-sports.io  (primary, direct)
    2. https://api-football-v1.p.rapidapi.com  (RapidAPI proxy)
We use #1 — same free tier, lower latency, no RapidAPI middleman.
Headers required: x-rapidapi-key + x-rapidapi-host (yes both are needed even on direct).

GRACEFUL DEGRADATION
--------------------
If API_FOOTBALL_KEY is unset, every method returns empty results — refresh
jobs will log a warning and exit cleanly without affecting the rest of the
application.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class APIFootballClient:
    """Thin wrapper around api-football.com /fixtures and /teams endpoints."""

    BASE_URL = 'https://v3.football.api-sports.io'
    HOST = 'v3.football.api-sports.io'
    MAX_RETRIES = 2  # Free tier rate-limit retry budget. Permanent failures fall through.

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('API_FOOTBALL_KEY')
        # Latest known quota state from response headers — useful for monitoring
        self._quota_remaining: Optional[int] = None
        self._quota_limit: Optional[int] = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def quota_status(self) -> dict:
        return {
            'remaining': self._quota_remaining,
            'limit': self._quota_limit,
            'low': self._quota_remaining is not None and self._quota_remaining < 10,
        }

    def _get(self, endpoint: str, params: dict) -> dict:
        """GET with retry on transient errors. Returns response JSON, or {} on failure."""
        if not self.enabled:
            return {}
        url = f'{self.BASE_URL}{endpoint}'
        headers = {
            'x-rapidapi-key': self.api_key,
            'x-rapidapi-host': self.HOST,
        }
        for attempt in range(self.MAX_RETRIES):
            try:
                r = requests.get(url, headers=headers, params=params, timeout=15)
                # Quota headers — track regardless of status code
                try:
                    self._quota_remaining = int(r.headers.get('x-ratelimit-requests-remaining', -1))
                    self._quota_limit = int(r.headers.get('x-ratelimit-requests-limit', -1))
                except (TypeError, ValueError):
                    pass
                if r.status_code == 429:
                    logger.error("api-football 429 (quota exhausted). Remaining=%s/%s",
                                 self._quota_remaining, self._quota_limit)
                    return {}
                if 400 <= r.status_code < 500:
                    logger.warning("api-football %s for %s: %s",
                                   r.status_code, endpoint, r.text[:200])
                    return {}
                if r.status_code >= 500:
                    # Transient — retry
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return {}
                data = r.json()
                # api-football wraps errors inside a 200 with "errors" populated
                if data.get('errors') and (isinstance(data['errors'], dict) and data['errors']
                                            or isinstance(data['errors'], list) and data['errors']):
                    logger.warning("api-football errors: %s", data['errors'])
                logger.info("api-football: %s ok, %d results, quota %s/%s remaining",
                            endpoint, len(data.get('response', [])),
                            self._quota_remaining, self._quota_limit)
                return data
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.warning("api-football network error after retries: %s", e)
                return {}
            except Exception as e:
                logger.exception("api-football unexpected error: %s", e)
                return {}
        return {}

    def fetch_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        """
        Fetch fixtures for a league + season. Optional date range narrows the result.

        Args:
            league_id: api-football league ID (103 = Eliteserien)
            season:    integer year (Norwegian season runs calendar year)
            date_from: 'YYYY-MM-DD'
            date_to:   'YYYY-MM-DD'

        Returns the raw `response` list (each element has keys: fixture, league,
        teams, goals, score). See https://www.api-football.com/documentation-v3.
        """
        params = {'league': league_id, 'season': season}
        if date_from:
            params['from'] = date_from
        if date_to:
            params['to'] = date_to
        data = self._get('/fixtures', params)
        return data.get('response', [])

    def fetch_teams(self, league_id: int, season: int) -> list[dict]:
        """Fetch the team roster for a league + season. Useful for name-mapping setup."""
        data = self._get('/teams', {'league': league_id, 'season': season})
        return data.get('response', [])
