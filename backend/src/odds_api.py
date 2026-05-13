"""
The Odds API client — fetches bookmaker odds and resolves them against our fixtures.

Free tier: 500 requests/month. Each call returns every fixture for a sport, so we
cache responses for 30 minutes — that's ~48 calls/day per sport. With 9 sports
that's ~390 reqs/day, well under cap when scoped to value-bet refresh cadence.
Tune CACHE_TTL up if you hit the quota.

Graceful degradation: when ODDS_API_KEY is unset, all methods are no-ops returning
empty results — the rest of the app stays functional without value-bet features.

Team-name matching: The Odds API uses different naming than football-data.org
(e.g. "Man United" vs "Manchester United FC"). We normalize both sides (strip
"FC"/"AFC", lowercase, collapse whitespace) and check via aliases for the common
mismatches. Extend TEAM_ALIASES when you see mismatches in the warning logs.
"""

import logging
import os
import re
import time
from threading import Lock

import requests

logger = logging.getLogger(__name__)


# Map our football-data.org competition names → The Odds API sport keys.
# Source: https://the-odds-api.com/sports-odds-data/sports-apis.html
SPORT_KEY_MAP = {
    'Premier League':         'soccer_epl',
    'Championship':           'soccer_efl_champ',
    'La Liga':                'soccer_spain_la_liga',
    # football-data.org sometimes returns the Spanish league as "Primera Division"
    # (older fixtures / API quirk) and sometimes as "La Liga". Map both to the
    # same Odds API sport so coverage doesn't silently drop ~17 fixtures.
    'Primera Division':       'soccer_spain_la_liga',
    'Bundesliga':             'soccer_germany_bundesliga',
    'Serie A':                'soccer_italy_serie_a',
    'Ligue 1':                'soccer_france_ligue_one',
    'Eredivisie':             'soccer_netherlands_eredivisie',
    'Primeira Liga':          'soccer_portugal_primeira_liga',
    'UEFA Champions League':  'soccer_uefa_champs_league',
}


# Team-name aliases: canonical name → variants seen on The Odds API.
# Written in human-readable form; normalized at module load so the lookup table
# is in the same space as the inputs to _matches(). Add an entry here the first
# time you spot an unmatched warning in the logs.
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

    # Ligue 1 — French clubs use long official names in football-data.org
    'Olympique Lyonnais':         {'Lyon'},
    'Olympique de Marseille':     {'Marseille'},
    'Racing Club de Lens':        {'Lens', 'RC Lens'},
    'Stade Rennais FC 1901':      {'Rennes', 'Stade Rennais'},
    'OGC Nice':                   {'Nice'},
    'Stade de Reims':             {'Reims'},
    'AS Monaco FC':               {'Monaco'},
    'LOSC Lille':                 {'Lille'},
    'FC Nantes':                  {'Nantes'},

    # Eredivisie — Dutch clubs
    'PSV':                        {'PSV Eindhoven'},
    'FC Twente \'65':             {'Twente', 'FC Twente'},
    'AFC Ajax':                   {'Ajax'},
    'Feyenoord Rotterdam':        {'Feyenoord'},
    'AZ':                         {'AZ Alkmaar'},

    # Primeira Liga — Portuguese clubs
    'Sporting Clube de Portugal': {'Sporting CP', 'Sporting Lisbon', 'Sporting'},
    'Sport Lisboa e Benfica':     {'Benfica', 'SL Benfica'},
    'FC Porto':                   {'Porto'},
}


def _normalize(name: str) -> str:
    """Lowercase, strip FC/AFC/CF suffixes, collapse whitespace and punctuation."""
    if not name:
        return ''
    n = name.lower().strip()
    # Strip common club-suffix tokens
    n = re.sub(r'\b(fc|afc|cf|sc|ac|ssc|as|aj|cd|ud|sl|sd|fk|bk|sk|us|nk|sv|tsv|vfb|vfl|psv|kaa|kvc)\b', '', n)
    # Collapse non-letter runs to single space
    n = re.sub(r'[^a-z0-9äöüáéíóúñ&]+', ' ', n)
    return ' '.join(n.split())


# Build normalized alias lookup: each canonical and its variants share a set.
# We index every normalized form to its set so the alias check is a single lookup
# regardless of which side we're given. Built once at module load.
_ALIAS_INDEX: dict[str, frozenset[str]] = {}
for _canon, _variants in _TEAM_ALIASES_RAW.items():
    _all = frozenset({_normalize(_canon), *(_normalize(v) for v in _variants)} - {''})
    for _form in _all:
        _ALIAS_INDEX[_form] = _all


def _matches(name_a: str, name_b: str) -> bool:
    """True if two team names refer to the same club (after normalization + alias lookup)."""
    if not name_a or not name_b:
        return False
    a = _normalize(name_a)
    b = _normalize(name_b)
    if a == b:
        return True
    # Substring match (one is contained in the other) — handles "barcelona" vs "fc barcelona"
    if a in b or b in a:
        return True
    # Alias lookup — both forms map to the same canonical set
    alias_set = _ALIAS_INDEX.get(a) or _ALIAS_INDEX.get(b)
    if alias_set and a in alias_set and b in alias_set:
        return True
    return False


class OddsAPIClient:
    """The Odds API v4 client with per-sport TTL cache."""

    BASE_URL = 'https://api.the-odds-api.com/v4'
    CACHE_TTL = 1800  # 30 minutes — odds move but not every minute, and we have a 500/mo quota

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv('ODDS_API_KEY')
        self._cache: dict[str, dict] = {}
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _cache_get(self, sport_key: str):
        entry = self._cache.get(sport_key)
        if not entry or time.time() > entry['expires']:
            return None
        return entry['data']

    def _cache_set(self, sport_key: str, data):
        self._cache[sport_key] = {'data': data, 'expires': time.time() + self.CACHE_TTL}

    def fetch_h2h(self, sport_key: str, regions: str = 'eu') -> list[dict]:
        """Fetch H2H (1X2) odds for all upcoming events in one sport. Cached for CACHE_TTL."""
        if not self.enabled:
            return []
        with self._lock:
            cached = self._cache_get(sport_key)
        if cached is not None:
            return cached
        try:
            r = requests.get(
                f'{self.BASE_URL}/sports/{sport_key}/odds/',
                params={
                    'apiKey': self.api_key,
                    'regions': regions,
                    'markets': 'h2h',
                    'oddsFormat': 'decimal',
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            logger.info("Odds API: fetched %d events for %s", len(data), sport_key)
        except requests.HTTPError as e:
            # 401 = invalid key, 422 = bad sport key, 429 = quota — all worth seeing
            logger.warning("Odds API HTTP error for %s: %s — response=%s",
                           sport_key, e, getattr(e.response, 'text', '')[:200])
            return []
        except Exception as e:
            logger.warning("Odds API fetch failed for %s: %s", sport_key, e)
            return []
        with self._lock:
            self._cache_set(sport_key, data)
        return data

    def best_odds_for_match(self, competition: str, home_name: str, away_name: str) -> dict | None:
        """
        Find best (highest) decimal odds across all bookmakers for one match.

        Returns:
            {'home': {'price': 2.10, 'bookmaker': 'Pinnacle'},
             'draw': {'price': 3.50, 'bookmaker': 'Bet365'},
             'away': {'price': 3.20, 'bookmaker': 'Pinnacle'}}
            or None if no event matched or sport not supported.
        """
        sport_key = SPORT_KEY_MAP.get(competition)
        if not sport_key or not self.enabled:
            return None
        events = self.fetch_h2h(sport_key)
        for event in events:
            if (_matches(event.get('home_team', ''), home_name) and
                    _matches(event.get('away_team', ''), away_name)):
                return self._extract_best_h2h(event)
        logger.debug("No odds match for %s vs %s in %s", home_name, away_name, competition)
        return None

    def _extract_best_h2h(self, event: dict) -> dict | None:
        """Walk every bookmaker × outcome and keep the highest price per outcome."""
        event_home = event.get('home_team', '')
        event_away = event.get('away_team', '')
        best = {'home': None, 'draw': None, 'away': None}

        for bookmaker in event.get('bookmakers', []):
            for market in bookmaker.get('markets', []):
                if market.get('key') != 'h2h':
                    continue
                for outcome in market.get('outcomes', []):
                    name = outcome.get('name', '')
                    price = outcome.get('price')
                    if not price or price <= 1.0:
                        continue
                    if name.lower() == 'draw':
                        key = 'draw'
                    elif _matches(name, event_home):
                        key = 'home'
                    elif _matches(name, event_away):
                        key = 'away'
                    else:
                        continue
                    if best[key] is None or price > best[key]['price']:
                        best[key] = {'price': float(price), 'bookmaker': bookmaker.get('title', '?')}

        return best if any(best.values()) else None

    def quota_remaining(self) -> int | None:
        """Last-known requests remaining (parsed from response headers)."""
        # The Odds API returns x-requests-remaining + x-requests-used headers.
        # We don't track those here yet — leaves room for a future /api/admin/odds-status.
        return None
