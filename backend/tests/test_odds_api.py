"""
Unit tests for the Odds API client.

We don't hit the live network here — fetch_h2h is bypassed by injecting events
directly into the per-sport cache, so these tests stay deterministic and offline.
"""
import time

import pytest

from odds_api import OddsAPIClient, _normalize, _matches, SPORT_KEY_MAP


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
    """Inject events directly into the per-sport TTL cache to skip HTTP."""
    client._cache[sport_key] = {'data': events, 'expires': time.time() + 3600}


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
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Pinnacle': [('Arsenal FC', 2.10), ('Draw', 3.30), ('Chelsea FC', 3.80)],
                'Bet365':   [('Arsenal FC', 2.05), ('Draw', 3.50), ('Chelsea FC', 3.60)],
                'Unibet':   [('Arsenal FC', 2.15), ('Draw', 3.20), ('Chelsea FC', 3.90)],
            }),
        ])
        best = c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        assert best is not None
        assert best['home'] == {'price': 2.15, 'bookmaker': 'Unibet'}
        assert best['draw'] == {'price': 3.50, 'bookmaker': 'Bet365'}
        assert best['away'] == {'price': 3.90, 'bookmaker': 'Unibet'}

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
        c = OddsAPIClient(api_key='x')
        _seed_cache(c, 'soccer_epl', [
            _event('Arsenal FC', 'Chelsea FC', {
                'Broken': [('Arsenal FC', 1.0), ('Draw', 0.0), ('Chelsea FC', 2.5)],
            }),
        ])
        best = c.best_odds_for_match('Premier League', 'Arsenal FC', 'Chelsea FC')
        assert best is not None
        # Only Chelsea (away) had a valid price
        assert best['home'] is None
        assert best['draw'] is None
        assert best['away']['price'] == 2.5


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
