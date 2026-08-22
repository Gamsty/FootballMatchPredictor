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

    def __init__(self, default_ttl=3600, max_size=2000):
        """
        Args:
            default_ttl: Default time-to-live in seconds (1 hour)
            max_size: Hard ceiling on retained entries. Expiry alone only ever
                triggered on lookup, so a key nobody asks for again — every
                fixture that has since kicked off — sat in the dict until the
                worker recycled. Each entry is a full multi-market payload, so
                that is real memory in a process already holding ~500MB of models.
        """
        self._cache = {}
        self._lock = Lock()
        self.default_ttl = default_ttl
        self.max_size = max_size

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
        """Store prediction in cache, evicting expired (then oldest) entries."""
        key = self._make_key(home_team_id, away_team_id, date_str)
        ttl = ttl or self.default_ttl
        with self._lock:
            self._cache[key] = {
                'data': data,
                'expires': time.time() + ttl,
            }
            if len(self._cache) > self.max_size:
                self._evict_locked()

    def _evict_locked(self):
        """Drop expired entries; if still over budget, drop soonest-to-expire.

        Caller must hold the lock.
        """
        now = time.time()
        for k in [k for k, v in self._cache.items() if now > v['expires']]:
            del self._cache[k]
        overflow = len(self._cache) - self.max_size
        if overflow > 0:
            # Soonest-expiring first ≈ oldest-written first, since writes share
            # a TTL. Cheaper than tracking access order and good enough here.
            for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1]['expires'])[:overflow]:
                del self._cache[k]

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
