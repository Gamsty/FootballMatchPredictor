"""
Unit tests for the Odds API client.

We don't hit the live network here — fetch_h2h is bypassed by injecting events
directly into the per-sport cache, so these tests stay deterministic and offline.
"""
import time

import pytest

from odds_api import OddsAPIClient, _normalize, _matches, SPORT_KEY_MAP, TRUSTED_BOOKMAKERS


@pytest.fixture(autouse=True)
def isolate_cache_file(tmp_path, monkeypatch):
    """Point every OddsAPIClient created in this module at a per-test tmp cache
    file so we don't pick up real cache state from backend/data/odds_cache.json
    (the running dev server may have populated it). Without this, tests that
    assert 'first call hits network' fail when the cache has the key cached."""
    monkeypatch.setenv('ODDS_API_CACHE_FILE', str(tmp_path / 'odds.json'))


# ----------------------------------------------------------------------------
# Name normalization + alias matching
# ----------------------------------------------------------------------------

class TestNormalize:
    def test_strips_fc_suffix(self):
        assert _normalize('Arsenal FC') == 'arsenal'

    def test_strips_fc_prefix(self):
        assert _normalize('FC Barcelona') == 'barcelona'

    def test_lowercases(self):
        assert _normalize('MANCHESTER') == 'manchester'

    def test_collapses_punctuation(self):
        assert _normalize('Paris Saint-Germain') == 'paris saint germain'

    def test_keeps_numerals(self):
        # "Bayer 04 Leverkusen" must keep the 04 so alias matching is non-ambiguous
        assert '04' in _normalize('Bayer 04 Leverkusen')

    def test_empty_input(self):
        assert _normalize('') == ''
        assert _normalize(None) == ''


class TestMatches:
    @pytest.mark.parametrize('a,b', [
        ('Manchester United FC', 'Man United'),
        ('Tottenham Hotspur FC', 'Spurs'),
        ('Bayer 04 Leverkusen', 'Bayer Leverkusen'),
        ('FC Internazionale Milano', 'Inter Milan'),
        ('Paris Saint-Germain FC', 'PSG'),
        ('Paris Saint-Germain FC', 'Paris SG'),
        ('Wolverhampton Wanderers FC', 'Wolves'),
        ('Real Madrid CF', 'Real Madrid'),
        ('Atlético Madrid', 'Atletico Madrid'),
        # Substring path (no alias needed)
        ('Arsenal FC', 'Arsenal'),
        ('FC Barcelona', 'Barcelona'),
        # Identity
        ('Liverpool FC', 'Liverpool FC'),
    ])
    def test_known_aliases_match(self, a, b):
        assert _matches(a, b), f'expected {a!r} ↔ {b!r}'

    @pytest.mark.parametrize('a,b', [
        ('Liverpool FC', 'Chelsea FC'),
        ('Arsenal FC', 'Aston Villa FC'),
        ('Real Madrid', 'Atletico Madrid'),
    ])
    def test_different_clubs_dont_match(self, a, b):
        assert not _matches(a, b)

    def test_empty_inputs_dont_match(self):
        assert not _matches('', 'Arsenal')
        assert not _matches('Arsenal', '')
        assert not _matches(None, None)


# ----------------------------------------------------------------------------
# OddsAPIClient: enabled flag + no-op behaviour
# ----------------------------------------------------------------------------

class TestEnabled:
    def test_disabled_when_no_key(self, monkeypatch):
        monkeypatch.delenv('ODDS_API_KEY', raising=False)
        assert OddsAPIClient().enabled is False

    def test_enabled_when_key_present(self):
        assert OddsAPIClient(api_key='x').enabled is True

    def test_disabled_client_returns_no_odds(self, monkeypatch):
        monkeypatch.delenv('ODDS_API_KEY', raising=False)
        c = OddsAPIClient()
        # Must not raise and must not make a network call (no key = guaranteed by `enabled`).
        assert c.fetch_h2h('soccer_epl') == []
        assert c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC') is None


# ----------------------------------------------------------------------------
# best_odds_for_match: walk the bookmakers list, pick best per outcome
# ----------------------------------------------------------------------------

def _seed_cache(client: OddsAPIClient, sport_key: str, events: list[dict]):
    """Inject events into the (sport, h2h-only, eu) cache key — skips HTTP."""
    client._cache[client._cache_key(sport_key, ('h2h',), 'eu')] = {
        'data': events, 'expires': time.time() + 3600,
    }


def _event(home, away, prices: dict[str, list[tuple[str, float]]]):
    """
    Build a synthetic Odds API event payload.

    `prices`: {bookmaker_title: [(outcome_name, decimal_price), ...]}
        outcome_name is one of: the home name, the away name, or 'Draw'
    """
    return {
        'home_team': home,
        'away_team': away,
        'bookmakers': [
            {
                'key': title.lower(),
                'title': title,
                'markets': [{
                    'key': 'h2h',
                    'outcomes': [{'name': name, 'price': price} for name, price in outcomes],
                }],
            }
            for title, outcomes in prices.items()
        ],
    }


class TestBestOdds:
    def test_picks_highest_price_per_outcome(self):
        # All three keys live in TRUSTED_BOOKMAKERS so they survive the sharp filter.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Pinnacle':  [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                'Bet365':    [('Arsenal FC', 2.05), ('Draw', 3.50), ('Chelsea FC', 3.60)],
                'BetVictor': [('Arsenal FC', 2.15), ('Draw', 3.20), ('Chelsea FC', 3.90)],
            }),
        ])
        best = c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        assert best is not None
        assert best['home'] == {'price': 2.15, 'bookmaker': 'BetVictor'}
        assert best['draw'] == {'price': 3.50, 'bookmaker': 'Bet365'}
        assert best['away'] == {'price': 3.90, 'bookmaker': 'BetVictor'}

    def test_resolves_via_alias(self):
        # Our fixture name is canonical, Odds API uses a shortened form.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Man United', 'Spurs', {
                'Pinnacle': [('Man United', 1.80), ('Draw', 3.80), ('Spurs', 4.50)],
            }),
        ])
        best = c.best_odds_for_match(
            'Premier League', 'Manchester United FC', 'Tottenham Hotspur FC'
        )
        assert best is not None
        assert best['home']['price'] == 1.80
        assert best['away']['price'] == 4.50

    def test_returns_none_when_no_event_matches(self):
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Pinnacle': [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
            }),
        ])
        # Different teams altogether
        assert c.best_odds_for_match('Premier League', 'Liverpool FC', 'Everton FC') is None

    def test_returns_none_for_unsupported_competition(self):
        c = OddsAPIClient(api_key='x')
        # The match exists on paper but the competition has no sport_key mapping.
        assert c.best_odds_for_match('Some Random League', 'A', 'B') is None

    def test_ignores_outcomes_with_zero_or_unity_price(self):
        # Price <= 1.0 is nonsensical for decimal odds; we skip it rather than crash.
        # books='all' here so a synthetic "Broken" book isn't filtered out.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Broken': [('Arsenal FC', 1.0), ('Draw', 0.0), ('Chelsea FC', 2.5)],
            }),
        ])
        best = c.best_odds_for_match(
            'Premier League', 'Arsenal FC', 'Chelsea FC', books='all',
        )
        assert best is not None
        # Only Chelsea (away) had a valid price
        assert best['home'] is None
        assert best['draw'] is None
        assert best['away']['price'] == 2.5


# ----------------------------------------------------------------------------
# Sharp bookmaker filter — the core defense against palp-error fake edges
# ----------------------------------------------------------------------------

class TestSharpFilter:
    def test_sharp_default_drops_untrusted_bookmaker(self):
        # 1xBet posts an outlier 32.0 (palp error). Default sharp filter must
        # exclude it and only see Pinnacle's real 2.10.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Pinnacle': [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                '1xBet':    [('Arsenal FC', 32.0), ('Draw', 3.30), ('Chelsea FC', 3.80)],
            }),
        ])
        best = c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        assert best['home']['price'] == 2.10
        assert best['home']['bookmaker'] == 'Pinnacle'

    def test_books_all_includes_untrusted(self):
        # Explicit opt-in surfaces the outlier — diagnostics-only behavior.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Pinnacle': [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                '1xBet':    [('Arsenal FC', 32.0), ('Draw', 3.30), ('Chelsea FC', 3.80)],
            }),
        ])
        best = c.best_odds_for_match(
            'Premier League', 'Arsenal FC', 'Chelsea FC', books='all',
        )
        assert best['home']['price'] == 32.0
        assert best['home']['bookmaker'] == '1xBet'

    def test_sharp_returns_none_when_no_trusted_book_priced_the_match(self):
        # Match exists on Odds API but only grey-market books have priced it
        # → sharp mode correctly reports "no data" rather than chasing junk.
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Nordic Bet':   [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                'BetOnline.ag': [('Arsenal FC', 2.05), ('Draw', 3.40), ('Chelsea FC', 3.70)],
            }),
        ])
        assert c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC') is None

    def test_trusted_set_contains_expected_books(self):
        # Spot-check: a few must-have keys. Don't pin the entire set since
        # we're likely to extend it.
        for key in ('pinnacle', 'betfair_ex_eu', 'bet365', 'williamhill'):
            assert key in TRUSTED_BOOKMAKERS
        # Negative: keys we never want to trust (incl. ones removed in tightening)
        for key in ('1xbet', 'nordicbet', 'betonlineag', 'gtbets', 'coolbet',
                    'betfair_sb_uk', 'marathonbet'):
            assert key not in TRUSTED_BOOKMAKERS


# ----------------------------------------------------------------------------
# Fuzzy team matching — catches unforeseen name variants without an alias entry
# ----------------------------------------------------------------------------

class TestFuzzyMatching:
    @pytest.mark.parametrize('a,b', [
        # Slight spelling variation that the alias table doesn't cover
        ('FC Augsburg', 'Augsburg'),
        # Word-order independence
        ('FC Bayern Munich', 'Bayern Munich FC'),
    ])
    def test_fuzzy_matches_close_variants(self, a, b):
        assert _matches(a, b)

    @pytest.mark.parametrize('a,b', [
        ('Real Madrid', 'Real Sociedad'),
        ('AC Milan', 'AC Monza'),
        ('Manchester United', 'Manchester City'),
    ])
    def test_fuzzy_rejects_different_clubs_with_shared_words(self, a, b):
        assert not _matches(a, b)


# ----------------------------------------------------------------------------
# Multi-market extraction (totals, btts) + median aggregation + overround
# ----------------------------------------------------------------------------

def _multi_market_event(home, away, h2h_prices=None, totals_prices=None, btts_prices=None):
    """Build an event with multiple markets per bookmaker."""
    by_book: dict = {}
    for title, prices in (h2h_prices or {}).items():
        by_book.setdefault(title, []).append({
            'key': 'h2h',
            'outcomes': [{'name': n, 'price': p} for n, p in prices],
        })
    for title, prices in (totals_prices or {}).items():
        by_book.setdefault(title, []).append({
            'key': 'totals',
            'outcomes': [{'name': n, 'price': p, 'point': 2.5} for n, p in prices],
        })
    for title, prices in (btts_prices or {}).items():
        by_book.setdefault(title, []).append({
            'key': 'btts',
            'outcomes': [{'name': n, 'price': p} for n, p in prices],
        })
    return {
        'home_team': home,
        'away_team': away,
        'bookmakers': [
            {'key': title.lower(), 'title': title, 'markets': markets}
            for title, markets in by_book.items()
        ],
    }


class TestMultiMarket:
    def test_odds_for_match_returns_h2h_with_median(self):
        c = OddsAPIClient(api_key='x')
        c._cache[c._cache_key('soccer_epl', ('h2h',), 'eu')] = {
            'data': [_multi_market_event(
                'Arsenal FC', 'Chelsea FC',
                h2h_prices={
                    'Pinnacle':  [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                    'Bet365':    [('Arsenal FC', 2.05), ('Draw', 3.50), ('Chelsea FC', 3.60)],
                    'BetVictor': [('Arsenal FC', 2.15), ('Draw', 3.20), ('Chelsea FC', 3.90)],
                },
            )],
            'expires': time.time() + 3600,
        }
        odds = c.odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        assert 'h2h' in odds
        home = odds['h2h']['home']
        assert home['best']['price'] == 2.15  # BetVictor
        assert home['median'] == 2.10         # median of 2.05, 2.10, 2.15
        assert home['count'] == 3

    def test_totals_only_returns_requested_point(self):
        # Bookmakers offer multiple lines; we only want the 2.5 line.
        c = OddsAPIClient(api_key='x')
        event = {
            'home_team': 'Arsenal FC',
            'away_team': 'Chelsea FC',
            'bookmakers': [{
                'key': 'pinnacle', 'title': 'Pinnacle',
                'markets': [{
                    'key': 'totals',
                    'outcomes': [
                        {'name': 'Over',  'price': 1.90, 'point': 2.5},
                        {'name': 'Under', 'price': 1.95, 'point': 2.5},
                        {'name': 'Over',  'price': 2.30, 'point': 3.5},  # ignored
                        {'name': 'Under', 'price': 1.60, 'point': 3.5},  # ignored
                    ],
                }],
            }],
        }
        c._cache[c._cache_key('soccer_epl', ('totals',), 'eu')] = {
            'data': [event], 'expires': time.time() + 3600,
        }
        odds = c.odds_for_match(
            'Premier League', 'Arsenal FC', 'Chelsea FC', markets=('totals',),
        )
        assert odds and odds['totals']['point'] == 2.5
        assert odds['totals']['over']['best']['price'] == 1.90
        assert odds['totals']['under']['best']['price'] == 1.95

    def test_overround_is_computed_when_all_outcomes_priced(self):
        c = OddsAPIClient(api_key='x')
        c._cache[c._cache_key('soccer_epl', ('h2h',), 'eu')] = {
            'data': [_multi_market_event(
                'Arsenal FC', 'Chelsea FC',
                h2h_prices={
                    # Fair odds for 50/25/25 split = 2.0 / 4.0 / 4.0 (overround = 1.0)
                    # Here books take 5% margin: 1.90 / 3.80 / 3.80 (overround ~1.053)
                    'Pinnacle': [('Arsenal FC', 1.90), ('Draw', 3.80), ('Chelsea FC', 3.80)],
                },
            )],
            'expires': time.time() + 3600,
        }
        odds = c.odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        over = odds['h2h']['overround_best']
        # 1/1.90 + 2 * 1/3.80 = 0.526 + 0.526 = 1.052 (5.2% margin)
        assert over is not None
        assert 1.04 < over < 1.07


# ----------------------------------------------------------------------------
# Quota tracking + retry behaviour (mocked HTTP)
# ----------------------------------------------------------------------------

class TestQuota:
    def test_quota_updated_from_response_headers(self):
        # Direct unit test on the parser — no HTTP needed
        c = OddsAPIClient(api_key='x')
        c._update_quota_from_headers({
            'x-requests-remaining': '423',
            'x-requests-used': '77',
        })
        s = c.quota_status()
        assert s['remaining'] == 423
        assert s['used'] == 77
        assert s['low'] is False

    def test_quota_low_flag_trips_under_50(self):
        c = OddsAPIClient(api_key='x')
        c._update_quota_from_headers({'x-requests-remaining': '12'})
        assert c.quota_status()['low'] is True

    def test_quota_status_when_never_fetched(self):
        c = OddsAPIClient(api_key='x')
        s = c.quota_status()
        # Subset-check quota-specific keys so additions to quota_status
        # (cache_age_seconds, cache_ttl_seconds, ...) don't break this test.
        assert s['remaining'] is None
        assert s['used'] is None
        assert s['low'] is False
        assert s['circuit_breaker_active'] is False
        assert s['quota_floor'] == c.quota_floor
        assert s['cache_age_seconds'] is None
        assert s['cache_ttl_seconds'] > 0


class TestCircuitBreaker:
    """
    Quota circuit breaker: when remaining quota falls below the floor, fetch
    must refuse instead of making the request. Without this, a bug or abusive
    caller could drain the last few credits before month-end reset.
    """

    def test_fetch_blocked_when_below_floor(self, monkeypatch):
        c = OddsAPIClient(api_key='x')
        c.quota_floor = 20
        c._quota_remaining = 15  # below floor
        # Patch requests.get — should never be called when breaker trips.
        called = []
        monkeypatch.setattr('odds_api.requests.get',
                            lambda *a, **kw: called.append(True) or None)
        result = c.fetch_odds('soccer_epl', markets=('h2h',))
        assert result == []
        assert called == [], "requests.get must not run when breaker trips"

    def test_fetch_allowed_when_above_floor(self, monkeypatch):
        from unittest.mock import MagicMock
        c = OddsAPIClient(api_key='x')
        c.quota_floor = 20
        c._quota_remaining = 100  # well above floor
        fake_resp = MagicMock(status_code=200, headers={
            'x-requests-remaining': '99', 'x-requests-used': '401',
        })
        fake_resp.json.return_value = []
        monkeypatch.setattr('odds_api.requests.get', lambda *a, **kw: fake_resp)
        result = c.fetch_odds('soccer_epl', markets=('h2h',))
        assert result == []  # empty events list, but DID make the call

    def test_fetch_allowed_on_first_call_unknown_quota(self, monkeypatch):
        """
        Initial state: _quota_remaining is None until we've made one request.
        Breaker must not refuse the first call — we need it to read the
        response headers and learn the budget.
        """
        from unittest.mock import MagicMock
        c = OddsAPIClient(api_key='x')
        assert c._quota_remaining is None
        fake_resp = MagicMock(status_code=200, headers={
            'x-requests-remaining': '450', 'x-requests-used': '50',
        })
        fake_resp.json.return_value = []
        called = []
        def _capture(*a, **kw):
            called.append(True)
            return fake_resp
        monkeypatch.setattr('odds_api.requests.get', _capture)
        c.fetch_odds('soccer_epl', markets=('h2h',))
        assert called == [True], "first call must go through to learn quota"

    def test_quota_status_reports_breaker_state(self):
        c = OddsAPIClient(api_key='x')
        c.quota_floor = 20
        # Above floor → breaker inactive
        c._quota_remaining = 100
        assert c.quota_status()['circuit_breaker_active'] is False
        # At floor → breaker active (≤ not <)
        c._quota_remaining = 20
        assert c.quota_status()['circuit_breaker_active'] is True
        # Below floor → breaker active
        c._quota_remaining = 5
        assert c.quota_status()['circuit_breaker_active'] is True

    def test_env_var_overrides_default_floor(self, monkeypatch):
        monkeypatch.setenv('ODDS_API_QUOTA_FLOOR', '100')
        c = OddsAPIClient(api_key='x')
        assert c.quota_floor == 100

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv('ODDS_API_QUOTA_FLOOR', 'not-a-number')
        c = OddsAPIClient(api_key='x')
        assert c.quota_floor == OddsAPIClient.DEFAULT_QUOTA_FLOOR


# ----------------------------------------------------------------------------
# clear_cache — used by /api/admin/reload-model after model promotion
# ----------------------------------------------------------------------------

def test_clear_cache_drops_all_entries():
    c = OddsAPIClient(api_key='x')
    c._cache_set('soccer_epl|h2h|eu', [{'a': 1}])
    c._cache_set('soccer_spain_la_liga|h2h|eu', [{'a': 2}])
    assert len(c._cache) == 2
    dropped = c.clear_cache()
    assert dropped == 2
    assert len(c._cache) == 0


# ----------------------------------------------------------------------------
# Bulk endpoint market filtering — drops `btts` before it hits the API
# (The Odds API rejects btts on /sports/{sport}/odds with 422; we drop it
# client-side so callers don't burn quota on doomed requests.)
# ----------------------------------------------------------------------------

def test_btts_is_silently_dropped_from_bulk_fetch(monkeypatch):
    c = OddsAPIClient(api_key='x')
    # If fetch_odds tries to HTTP-call, this would intercept and fail loudly
    def _no_http(*args, **kwargs):
        raise AssertionError("Should not HTTP — btts-only request should short-circuit")
    monkeypatch.setattr('requests.get', _no_http)
    # btts-only request returns empty list without an HTTP call
    assert c.fetch_odds('soccer_epl', markets=('btts',)) == []


def test_mixed_markets_drop_btts_keep_h2h(monkeypatch):
    captured = {}
    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self): return []
    def _capture(url, params, timeout):
        captured['markets'] = params['markets']
        return FakeResp()
    c = OddsAPIClient(api_key='x')
    monkeypatch.setattr('requests.get', _capture)
    c.fetch_odds('soccer_epl', markets=('h2h', 'btts'))
    # btts was filtered out; only h2h hit the wire
    assert captured['markets'] == 'h2h'


# ----------------------------------------------------------------------------
# Sport key map sanity
# ----------------------------------------------------------------------------

def test_sport_key_map_covers_supported_leagues():
    # Spot-check: each league listed in app's COMPETITIONS should have a sport_key.
    # We don't import COMPETITIONS here to keep the test isolated; instead we lock
    # in the exact set so a typo or accidental removal causes a test failure.
    expected = {
        'Premier League', 'Championship', 'La Liga', 'Primera Division',
        'Bundesliga', 'Serie A', 'Ligue 1', 'Eredivisie', 'Primeira Liga',
        'UEFA Champions League',
    }
    assert set(SPORT_KEY_MAP) == expected


def test_la_liga_and_primera_division_map_to_same_sport():
    # football-data.org uses both names across seasons — must resolve to the same API.
    assert SPORT_KEY_MAP['La Liga'] == SPORT_KEY_MAP['Primera Division']
