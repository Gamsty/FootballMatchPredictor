"""
Simple in-memory TTL cache for predictions.

No external dependencies (no Redis). Predictions for a match are stable
within a given day, so a 1-hour TTL is safe and avoids re-computing
features + model inference on every request.
"""

import time
from threading import Lock


class PredictionCache:
    """Thread-safe in-memory cache with TTL expiry."""

    def __init__(self, default_ttl=3600):
        """
        Args:
            default_ttl: Default time-to-live in seconds (1 hour)
        """
        self._cache = {}
        self._lock = Lock()
        self.default_ttl = default_ttl

    def _make_key(self, home_team_id, away_team_id, date_str=None):
        """Build cache key from match identifiers."""
        return f"{home_team_id}:{away_team_id}:{date_str or 'manual'}"

    def get(self, home_team_id, away_team_id, date_str=None):
        """Get cached prediction if it exists and hasn't expired."""
        key = self._make_key(home_team_id, away_team_id, date_str)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry['expires']:
                del self._cache[key]
                return None
            return entry['data']

    def set(self, home_team_id, away_team_id, data, date_str=None, ttl=None):
        """Store prediction in cache."""
        key = self._make_key(home_team_id, away_team_id, date_str)
        ttl = ttl or self.default_ttl
        with self._lock:
            self._cache[key] = {
                'data': data,
                'expires': time.time() + ttl,
            }

    def clear(self):
        """Clear all cached predictions."""
        with self._lock:
            self._cache.clear()

    def cleanup(self):
        """Remove expired entries."""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._cache.items() if now > v['expires']]
            for k in expired:
                del self._cache[k]

    @property
    def size(self):
        return len(self._cache)
