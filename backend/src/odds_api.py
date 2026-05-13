"""
The Odds API client — fetches bookmaker odds and resolves them against our fixtures.

QUOTA NOTE
----------
The Odds API charges one credit per (market × region) combination per request.
Default scope: `markets=h2h, regions=eu` → 1 credit per fetch per league. At 30-min
cache TTL and 9 leagues, that's ~390 reqs/day → fits inside the 500/month free tier
because the cache absorbs most calls. Add markets (totals, btts) or regions
(uk, us, au) only when needed and watch `quota_remaining()`.

GRACEFUL DEGRADATION
--------------------
When ODDS_API_KEY is unset, every public method returns empty / None and the
rest of the app continues to work — the Value tab in the dashboard just shows
a "not configured" panel.

TEAM-NAME MATCHING
------------------
The Odds API uses different naming than football-data.org ("Man United" vs
"Manchester United FC", "Lyon" vs "Olympique Lyonnais"). We:
  1. Normalize both sides (lowercase, strip FC/AFC/CF/etc., collapse punctuation)
  2. Exact match → substring → curated alias index → fuzzy ratio ≥ 88

Step 4 (fuzzy) is the new addition over the previous version — it picks up
unforeseen variants automatically, with the threshold tuned so "Real Madrid"
matches "Real Madrid CF" (~95) but "Real Sociedad" doesn't get pulled in (~75).

PRICE AGGREGATION
-----------------
For each outcome we return both:
  - `best`   — highest decimal odds across the trusted set (where to bet)
  - `median` — median price across the trusted set (the true market reference)
A large gap (best ≫ median) signals a stale or outlier price — the same hint
the frontend uses to flag suspicious edges.
"""

from __future__ import annotations

import logging
import os
import re
import statistics
import time
from threading import Lock

import requests
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Trusted-bookmaker set
# ----------------------------------------------------------------------------
# Bookmaker keys (lowercased — match Odds API `bookmaker.key`) we trust to
# represent the real market price. Excludes grey-market / soft-bonus books
# (1xBet, Nordic Bet, BetOnline, GTbets, Coolbet, etc.) which post outlier
# prices that drive fake 100%+ edges via max-across-all-books.
#
# Removed compared to prior version:
#   betfair_sb_uk   — Betfair *Sportsbook* (not exchange) is soft / promotion-driven
#   marathonbet     — historical palp-error reputation, often outlier on long-tail
#
# If you want to validate this set empirically over time, call
# OddsAPIClient.bookmaker_divergence_stats() and compare each book's median price
# to the overall median across the same outcomes — chronic outliers should
# probably be removed.
TRUSTED_BOOKMAKERS = frozenset({
    # Tier 1 — sharp / efficient market makers
    'pinnacle',
    'betfair_ex_eu', 'betfair_ex_uk',
    'matchbook',
    'smarkets',
    # Tier 2 — liquid soft majors (limit winners but real liquidity)
    'bet365',
    'williamhill', 'williamhill_us',
    'unibet_eu', 'unibet_uk', 'unibet_au',
    'ladbrokes_uk',
    'betvictor',
    'paddypower',
    'sport888',
    'skybet',
    'betfred',
})


# Map football-data.org competition names → The Odds API sport keys.
SPORT_KEY_MAP = {
    'Premier League':         'soccer_epl',
    'Championship':           'soccer_efl_champ',
    'La Liga':                'soccer_spain_la_liga',
    # football-data.org sometimes returns Spain's top flight as "Primera Division"
    # and sometimes as "La Liga" — both map to the same Odds API sport.
    'Primera Division':       'soccer_spain_la_liga',
    'Bundesliga':             'soccer_germany_bundesliga',
    'Serie A':                'soccer_italy_serie_a',
    'Ligue 1':                'soccer_france_ligue_one',
    'Eredivisie':             'soccer_netherlands_eredivisie',
    'Primeira Liga':          'soccer_portugal_primeira_liga',
    'UEFA Champions League':  'soccer_uefa_champs_league',
}


# Team-name aliases: canonical name → variants seen on The Odds API. Normalized
# at module load. Fuzzy matching backfills uncatalogued variants, but keeping
# explicit entries here makes intent visible in code review and stops accidental
# false positives (e.g. "Real Madrid" vs "Real Sociedad").
_TEAM_ALIASES_RAW = {
    'Manchester United FC':       {'Man United', 'Man Utd'},
    'Manchester City FC':         {'Man City'},
    'Tottenham Hotspur FC':       {'Tottenham', 'Spurs'},
    'Wolverhampton Wanderers FC': {'Wolves', 'Wolverhampton'},
    'Brighton & Hove Albion FC':  {'Brighton'},
    'Newcastle United FC':        {'Newcastle'},
    'West Ham United FC':         {'West Ham'},
    'Leicester City FC':          {'Leicester'},
    'Leeds United FC':            {'Leeds'},
    'Nottingham Forest FC':       {"Nott'm Forest", 'Nottm Forest'},
    'Paris Saint-Germain FC':     {'PSG', 'Paris SG'},
    'Borussia Dortmund':          {'Dortmund'},
    'Borussia Mönchengladbach':   {'Borussia Monchengladbach', "M'gladbach", 'B. Monchengladbach', 'Monchengladbach'},
    'Bayern München':             {'Bayern Munich', 'Bayern'},
    'Bayer 04 Leverkusen':        {'Bayer Leverkusen', 'Leverkusen'},
    'TSG 1899 Hoffenheim':        {'Hoffenheim', '1899 Hoffenheim'},
    'RB Leipzig':                 {'Leipzig'},
    'Real Madrid CF':             {'Real Madrid'},
    'Atlético Madrid':            {'Atletico Madrid'},
    'Athletic Club':              {'Athletic Bilbao'},
    'FC Internazionale Milano':   {'Inter Milan', 'Inter'},
    'AC Milan':                   {'Milan'},
    'AS Roma':                    {'Roma'},
    'SSC Napoli':                 {'Napoli'},
    'Juventus FC':                {'Juventus'},
    'Olympique Lyonnais':         {'Lyon'},
    'Olympique de Marseille':     {'Marseille'},
    'Racing Club de Lens':        {'Lens', 'RC Lens'},
    'Stade Rennais FC 1901':      {'Rennes', 'Stade Rennais'},
    'OGC Nice':                   {'Nice'},
    'Stade de Reims':             {'Reims'},
    'AS Monaco FC':               {'Monaco'},
    'LOSC Lille':                 {'Lille'},
    'FC Nantes':                  {'Nantes'},
    'PSV':                        {'PSV Eindhoven'},
    'FC Twente \'65':             {'Twente', 'FC Twente'},
    'AFC Ajax':                   {'Ajax'},
    'Feyenoord Rotterdam':        {'Feyenoord'},
    'AZ':                         {'AZ Alkmaar'},
    'Sporting Clube de Portugal': {'Sporting CP', 'Sporting Lisbon', 'Sporting'},
    'Sport Lisboa e Benfica':     {'Benfica', 'SL Benfica'},
    'FC Porto':                   {'Porto'},
}


# Fuzzy match threshold (0-100). 88 found through manual tuning:
#   - "Real Madrid" vs "Real Madrid CF"     → ~95 ✓
#   - "FC Augsburg" vs "Augsburg"            → ~90 ✓
#   - "Real Madrid" vs "Real Sociedad"       → ~75 ✗ (correctly rejected)
#   - "AC Milan" vs "AC Monza"               → ~74 ✗ (correctly rejected)
FUZZ_THRESHOLD = 88


def _normalize(name: str) -> str:
    """Lowercase, strip FC/AFC/CF suffixes, collapse whitespace and punctuation."""
    if not name:
        return ''
    n = name.lower().strip()
    n = re.sub(r'\b(fc|afc|cf|sc|ac|ssc|as|aj|cd|ud|sl|sd|fk|bk|sk|us|nk|sv|tsv|vfb|vfl|psv|kaa|kvc)\b', '', n)
    n = re.sub(r'[^a-z0-9äöüáéíóúñ&]+', ' ', n)
    return ' '.join(n.split())


_ALIAS_INDEX: dict[str, frozenset[str]] = {}
for _canon, _variants in _TEAM_ALIASES_RAW.items():
    _all = frozenset({_normalize(_canon), *(_normalize(v) for v in _variants)} - {''})
    for _form in _all:
        _ALIAS_INDEX[_form] = _all


def _matches(name_a: str, name_b: str) -> bool:
    """
    True if two team names refer to the same club.

    Match cascade (cheap → expensive):
      1. Identical after normalization
      2. One is a substring of the other ("Arsenal" vs "Arsenal FC")
      3. Both map to the same curated alias set
      4. rapidfuzz token-set ratio ≥ FUZZ_THRESHOLD (handles unforeseen variants)
    """
    if not name_a or not name_b:
        return False
    a = _normalize(name_a)
    b = _normalize(name_b)
    if a == b:
        return True
    if a in b or b in a:
        return True
    alias_set = _ALIAS_INDEX.get(a) or _ALIAS_INDEX.get(b)
    if alias_set and a in alias_set and b in alias_set:
        return True
    # token_set_ratio is order-independent and tolerant of extra words —
    # exactly the shape of bookmaker-naming variance we see.
    return fuzz.token_set_ratio(a, b) >= FUZZ_THRESHOLD


# ----------------------------------------------------------------------------
# OddsAPIClient
# ----------------------------------------------------------------------------

class OddsAPIClient:
    """The Odds API v4 client with per-(sport, market, region) TTL cache, retry, quota tracking."""

    BASE_URL = 'https://api.the-odds-api.com/v4'
    CACHE_TTL = 1800           # 30 min — odds move but not by the second
    MAX_RETRIES = 3            # transient 5xx / connection errors only
    INITIAL_BACKOFF = 1.0      # exponential: 1s, 2s, 4s
    DEFAULT_REGIONS = 'eu'     # 'eu', 'uk', 'us', 'au' — comma-separated for multiple
    # h2h and totals are available on the bulk /sports/{sport}/odds endpoint.
    # btts is ONLY available on /sports/{sport}/events/{eventId}/odds (per-event)
    # — we'd need a different fetch path to support it. Including it in bulk
    # produces 422 INVALID_MARKET errors per league per fixture, which without
    # negative-result caching becomes a quota fire.
    DEFAULT_MARKETS = ('h2h',)
    BULK_SUPPORTED_MARKETS = frozenset({'h2h', 'totals', 'spreads'})
    # How long to cache failed fetches. Short enough that transient 5xx will
    # eventually retry; long enough that 4xx (e.g. wrong market key) doesn't
    # spam the API every request. Permanent errors like btts-on-bulk would
    # otherwise drain quota in seconds.
    NEGATIVE_CACHE_TTL = 600  # 10 min

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv('ODDS_API_KEY')
        self._cache: dict[str, dict] = {}
        self._lock = Lock()
        # Latest known quota state from response headers — populated by every successful fetch.
        self._quota_remaining: int | None = None
        self._quota_used: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ---- cache primitives ------------------------------------------------

    def _cache_key(self, sport_key: str, markets: tuple[str, ...], regions: str) -> str:
        return f'{sport_key}|{",".join(sorted(markets))}|{regions}'

    def _cache_get(self, key: str):
        entry = self._cache.get(key)
        if not entry or time.time() > entry['expires']:
            return None
        return entry['data']

    def _cache_set(self, key: str, data, ttl: float | None = None):
        self._cache[key] = {
            'data': data,
            'expires': time.time() + (ttl if ttl is not None else self.CACHE_TTL),
        }

    def clear_cache(self) -> int:
        """Drop everything. Called by retrain webhook after model promotion so the
        new model's probabilities are compared against fresh odds instead of stale ones."""
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
        logger.info("Odds cache cleared (%d entries dropped)", n)
        return n

    # ---- HTTP layer ------------------------------------------------------

    def fetch_odds(
        self,
        sport_key: str,
        markets: tuple[str, ...] | None = None,
        regions: str | None = None,
    ) -> list[dict]:
        """
        Fetch odds for one sport, with retry on transient failures.

        Each (markets × regions) combination is cached separately. Default is
        ('h2h',) and 'eu' — adding markets or regions multiplies quota cost.
        """
        if not self.enabled:
            return []
        markets = markets or self.DEFAULT_MARKETS
        regions = regions or self.DEFAULT_REGIONS

        # Drop markets that aren't supported on the bulk endpoint to avoid 422s.
        # Caller asked for btts? They get nothing for btts, but h2h+totals still work.
        unsupported = [m for m in markets if m not in self.BULK_SUPPORTED_MARKETS]
        markets = tuple(m for m in markets if m in self.BULK_SUPPORTED_MARKETS)
        if unsupported:
            logger.info("Odds API: dropping markets not supported by bulk endpoint: %s",
                        unsupported)
        if not markets:
            return []  # nothing to fetch — caller asked only for unsupported markets

        key = self._cache_key(sport_key, markets, regions)
        with self._lock:
            cached = self._cache_get(key)
        if cached is not None:
            return cached

        params = {
            'apiKey': self.api_key,
            'regions': regions,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
        }
        url = f'{self.BASE_URL}/sports/{sport_key}/odds/'

        for attempt in range(self.MAX_RETRIES):
            try:
                r = requests.get(url, params=params, timeout=10)
                # Always update quota from response headers (succeeds or not, header is present)
                self._update_quota_from_headers(r.headers)
                # 429 = quota / rate limit; don't retry (it'll just fail again immediately)
                if r.status_code == 429:
                    logger.error("Odds API 429 for %s (quota exhausted or rate-limited). "
                                 "Remaining=%s, used=%s", sport_key,
                                 self._quota_remaining, self._quota_used)
                    return []
                # 4xx that aren't 429 are permanent — bad key, bad sport, unsupported
                # market. We negative-cache for 10 min so subsequent calls in the
                # same loop (e.g. one /api/value-bets request hitting 73 fixtures)
                # don't re-issue dozens of doomed requests.
                if 400 <= r.status_code < 500:
                    logger.warning("Odds API %s for %s: %s",
                                   r.status_code, sport_key, r.text[:200])
                    with self._lock:
                        self._cache_set(key, [], ttl=self.NEGATIVE_CACHE_TTL)
                    return []
                # 5xx = transient, retry with backoff
                if r.status_code >= 500:
                    raise requests.HTTPError(f"server returned {r.status_code}", response=r)
                # 2xx success
                data = r.json()
                logger.info("Odds API: fetched %d events for %s (markets=%s, regions=%s) "
                            "[quota remaining=%s]",
                            len(data), sport_key, ','.join(markets), regions,
                            self._quota_remaining)
                with self._lock:
                    self._cache_set(key, data)
                return data
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning("Odds API transient error for %s (attempt %d/%d): %s "
                                   "— retrying in %.1fs",
                                   sport_key, attempt + 1, self.MAX_RETRIES, e, backoff)
                    time.sleep(backoff)
                    continue
                logger.warning("Odds API gave up on %s after %d retries: %s",
                               sport_key, self.MAX_RETRIES, e)
                return []
            except Exception as e:
                logger.exception("Odds API unexpected error for %s: %s", sport_key, e)
                return []

        return []  # unreachable, but mypy is happier

    # Backward-compat alias: existing callers use fetch_h2h.
    def fetch_h2h(self, sport_key: str, regions: str = 'eu') -> list[dict]:
        return self.fetch_odds(sport_key, markets=('h2h',), regions=regions)

    def _update_quota_from_headers(self, headers) -> None:
        """Pull `x-requests-remaining` / `x-requests-used` from response."""
        try:
            remaining = headers.get('x-requests-remaining')
            used = headers.get('x-requests-used')
            if remaining is not None:
                self._quota_remaining = int(remaining)
            if used is not None:
                self._quota_used = int(used)
        except (TypeError, ValueError):
            pass

    def quota_status(self) -> dict:
        """Last known quota state — surfaced by /api/admin/odds-status for monitoring."""
        return {
            'remaining': self._quota_remaining,
            'used': self._quota_used,
            'low': self._quota_remaining is not None and self._quota_remaining < 50,
        }

    # ---- public market resolution ---------------------------------------

    def odds_for_match(
        self,
        competition: str,
        home_name: str,
        away_name: str,
        books: str = 'sharp',
        regions: str | None = None,
        markets: tuple[str, ...] | None = None,
    ) -> dict | None:
        """
        Find odds for one match across one or more markets.

        Returns a dict shaped like:
            {
              'h2h':    {'home': {best, median, count}, 'draw': {...}, 'away': {...},
                         'overround_best': 1.04, 'overround_median': 1.06},
              'totals': {'over': {best, median, count}, 'under': {...}, 'point': 2.5,
                         'overround_best': 1.04, 'overround_median': 1.06},
              'btts':   {'yes':  {best, median, count}, 'no':   {...},
                         'overround_best': 1.05, 'overround_median': 1.07},
            }
        Markets the API didn't return are absent from the dict. Each `best`
        is `{'price': float, 'bookmaker': str}`; `median` is a float; `count`
        is the number of trusted books that priced this outcome.

        Returns None if the fixture isn't matched at all.
        """
        sport_key = SPORT_KEY_MAP.get(competition)
        if not sport_key or not self.enabled:
            return None
        markets = markets or self.DEFAULT_MARKETS
        events = self.fetch_odds(sport_key, markets=markets, regions=regions)
        bookmaker_filter = TRUSTED_BOOKMAKERS if books == 'sharp' else None

        for event in events:
            if (_matches(event.get('home_team', ''), home_name) and
                    _matches(event.get('away_team', ''), away_name)):
                return self._extract_all_markets(event, markets, bookmaker_filter)
        logger.debug("No odds match for %s vs %s in %s", home_name, away_name, competition)
        return None

    # Backward-compat: keep best_odds_for_match returning just the h2h `best` mapping
    # so older tests / callers don't break.
    def best_odds_for_match(
        self,
        competition: str,
        home_name: str,
        away_name: str,
        books: str = 'sharp',
    ) -> dict | None:
        full = self.odds_for_match(competition, home_name, away_name, books=books)
        if not full or 'h2h' not in full:
            return None
        h2h = full['h2h']
        return {
            'home': h2h['home']['best'] if h2h.get('home') else None,
            'draw': h2h['draw']['best'] if h2h.get('draw') else None,
            'away': h2h['away']['best'] if h2h.get('away') else None,
        }

    # ---- market-specific extraction -------------------------------------

    def _extract_all_markets(
        self,
        event: dict,
        markets: tuple[str, ...],
        bookmaker_filter: frozenset[str] | None,
    ) -> dict:
        """Dispatch to a per-market extractor for each market we asked for."""
        out: dict = {}
        for m in markets:
            if m == 'h2h':
                h2h = self._extract_h2h(event, bookmaker_filter)
                if h2h:
                    out['h2h'] = h2h
            elif m == 'totals':
                totals = self._extract_totals(event, bookmaker_filter, point=2.5)
                if totals:
                    out['totals'] = totals
            elif m == 'btts':
                btts = self._extract_btts(event, bookmaker_filter)
                if btts:
                    out['btts'] = btts
        return out

    def _walk_outcomes(
        self,
        event: dict,
        market_key: str,
        bookmaker_filter: frozenset[str] | None,
    ):
        """Yield (bookmaker_title, outcome_dict) for every priced outcome in a market."""
        for bookmaker in event.get('bookmakers', []):
            if bookmaker_filter is not None and bookmaker.get('key', '').lower() not in bookmaker_filter:
                continue
            title = bookmaker.get('title', '?')
            for market in bookmaker.get('markets', []):
                if market.get('key') != market_key:
                    continue
                for outcome in market.get('outcomes', []):
                    price = outcome.get('price')
                    if price and price > 1.0:
                        yield title, outcome

    def _aggregate(self, prices: list[tuple[float, str]]) -> dict | None:
        """Turn a list of (price, bookmaker) into the best + median + count shape."""
        if not prices:
            return None
        best_price, best_bm = max(prices, key=lambda p: p[0])
        return {
            'best': {'price': round(float(best_price), 2), 'bookmaker': best_bm},
            'median': round(float(statistics.median(p for p, _ in prices)), 2),
            'count': len(prices),
        }

    def _extract_h2h(self, event, bookmaker_filter) -> dict | None:
        event_home = event.get('home_team', '')
        event_away = event.get('away_team', '')
        buckets: dict[str, list[tuple[float, str]]] = {'home': [], 'draw': [], 'away': []}
        for title, outcome in self._walk_outcomes(event, 'h2h', bookmaker_filter):
            name = outcome.get('name', '')
            if name.lower() == 'draw':
                bucket = 'draw'
            elif _matches(name, event_home):
                bucket = 'home'
            elif _matches(name, event_away):
                bucket = 'away'
            else:
                continue
            buckets[bucket].append((outcome['price'], title))
        agg = {k: self._aggregate(v) for k, v in buckets.items()}
        if not any(agg.values()):
            return None
        return {**agg, **self._overround_pair(buckets)}

    def _extract_totals(self, event, bookmaker_filter, point: float = 2.5) -> dict | None:
        # Only keep outcomes priced at the exact point we asked for. Different
        # books may offer different lines; we want a comparable apples-to-apples set.
        buckets: dict[str, list[tuple[float, str]]] = {'over': [], 'under': []}
        for title, outcome in self._walk_outcomes(event, 'totals', bookmaker_filter):
            if abs(float(outcome.get('point') or 0) - point) > 0.001:
                continue
            name = (outcome.get('name') or '').lower()
            if name == 'over':
                buckets['over'].append((outcome['price'], title))
            elif name == 'under':
                buckets['under'].append((outcome['price'], title))
        agg = {k: self._aggregate(v) for k, v in buckets.items()}
        if not any(agg.values()):
            return None
        return {**agg, 'point': point, **self._overround_pair(buckets)}

    def _extract_btts(self, event, bookmaker_filter) -> dict | None:
        buckets: dict[str, list[tuple[float, str]]] = {'yes': [], 'no': []}
        for title, outcome in self._walk_outcomes(event, 'btts', bookmaker_filter):
            name = (outcome.get('name') or '').strip().lower()
            if name == 'yes':
                buckets['yes'].append((outcome['price'], title))
            elif name == 'no':
                buckets['no'].append((outcome['price'], title))
        agg = {k: self._aggregate(v) for k, v in buckets.items()}
        if not any(agg.values()):
            return None
        return {**agg, **self._overround_pair(buckets)}

    def _overround_pair(self, buckets: dict[str, list[tuple[float, str]]]) -> dict:
        """
        Compute overround (bookmaker margin) from BEST and MEDIAN prices.

        Overround = Σ 1/odds_i across outcomes. A fair market has overround = 1.0
        (no margin). Typical 1X2 overround = 1.04-1.08 on liquid books, ≥1.10
        on soft books. >1.20 strongly suggests one outcome wasn't priced —
        consumer should treat overround_best/_median as None in that case.
        """
        def sum_inv(prices_per_outcome):
            if not all(prices_per_outcome):
                return None
            return round(sum(1 / p for p in prices_per_outcome), 4)

        best_prices = [max(v)[0] if v else None for v in buckets.values()]
        med_prices = [statistics.median(p for p, _ in v) if v else None for v in buckets.values()]
        return {
            'overround_best': sum_inv(best_prices),
            'overround_median': sum_inv(med_prices),
        }

    # ---- diagnostic --------------------------------------------------------

    def bookmaker_divergence_stats(self, sport_key: str) -> dict | None:
        """
        For each bookmaker that priced the latest fetched events, compute the
        mean absolute log-ratio between its prices and the cross-book median.
        Used to validate the TRUSTED_BOOKMAKERS set empirically — bookmakers
        with consistently high divergence are either palp-prone or low-liquidity
        outlier-posters.

        Returns None when no events have been cached for this sport.
        """
        import math

        # Pull any cached payload for this sport (markets/regions agnostic).
        relevant = [v['data'] for k, v in self._cache.items()
                    if k.startswith(sport_key + '|') and time.time() <= v['expires']]
        if not relevant:
            return None
        events = relevant[0]

        per_book: dict[str, list[float]] = {}
        for event in events:
            # Pool all priced outcomes for this match across all bookmakers + markets
            outcome_prices: dict[tuple[str, str], list[tuple[float, str]]] = {}
            for bookmaker in event.get('bookmakers', []):
                key = bookmaker.get('key', '').lower()
                for market in bookmaker.get('markets', []):
                    mkey = market.get('key', '')
                    for outcome in market.get('outcomes', []):
                        oname = outcome.get('name', '')
                        price = outcome.get('price')
                        if price and price > 1.0:
                            outcome_prices.setdefault((mkey, oname), []).append((float(price), key))
            for prices in outcome_prices.values():
                if len(prices) < 3:
                    continue  # median is unstable with <3 samples
                med = statistics.median(p for p, _ in prices)
                for p, k in prices:
                    per_book.setdefault(k, []).append(abs(math.log(p / med)))

        return {
            book: {
                'mean_abs_log_dev': round(sum(devs) / len(devs), 4),
                'samples': len(devs),
            }
            for book, devs in per_book.items()
            if len(devs) >= 10
        }
