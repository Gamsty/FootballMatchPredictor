"""
Integration tests for /api/value-bets — wires the full Flask request/response
cycle with mocked dependencies (DB, model, OddsAPIClient) so we don't need a
real Postgres or live Odds API key.

What this catches that unit tests don't:
- app.py wiring bugs (typo in env var, wrong query-param name, mis-passed arg)
- response-shape regressions (meta fields, pick fields)
- error-paths (model not loaded → 503, odds disabled → enabled=false)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ----------------------------------------------------------------------------
# App fixture: import once, replace heavy globals with mocks
# ----------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    """
    A Flask test client with all backend globals (db, model, caches, odds client)
    patched out. Each test gets a fresh, isolated app state.

    We import inside the fixture so the test file can run without a real
    DATABASE_URL and without the model files present.
    """
    # Prevent real DB connection during import
    monkeypatch.setenv('DATABASE_URL', 'postgresql://x:y@127.0.0.1:65535/none')
    # Prevent app from trying to load real Azure/blob model
    monkeypatch.setenv('FLASK_ENV', 'production')
    # Bet-write tests in this file assume the auth gate is *off* so POST /api/bets
    # exercises validation logic, not the 401 path. Clearing here makes the fixture
    # hermetic against developer .env files that set BET_WRITE_TOKEN locally.
    monkeypatch.delenv('BET_WRITE_TOKEN', raising=False)

    # Stub the model_storage loader so import doesn't try blob/disk
    # WARM_CACHE=false: module import no longer warms inline, it spawns a thread —
    # which would race the global-stubbing below and hit the unreachable DB.
    monkeypatch.setenv('WARM_CACHE', 'false')
    with patch('model_storage.load_model_bytes', side_effect=Exception('mock')):
        import app  # imports + executes module body (load_model)

    # FLASK_ENV=production above exists only to stop load_dotenv() at import.
    # Now that import is done, drop back out of production: the bet-write gate
    # refuses unauthenticated writes in production (by design), and these tests
    # deliberately run with BET_WRITE_TOKEN cleared to exercise validation paths
    # rather than the 401 path.
    monkeypatch.setenv('FLASK_ENV', 'testing')

    app.app.config['TESTING'] = True

    # Override critical globals
    app.model_data = {
        'model_type': 'mock_stacked',
        'model_version': 'test-v1',
        'feature_names': ['home_elo', 'away_elo'],
        'scaler': MagicMock(),
        'model': MagicMock(),
    }
    app.multi_market_models = None

    # In-memory DB stub
    app.db = MagicMock()
    app.feature_engineer = MagicMock()
    app.prediction_cache.clear()

    yield app


def _stub_match(match_id, home_name, away_name, competition='Premier League',
                hours_ahead=24):
    m = MagicMock()
    m.id = match_id
    m.home_team_id = match_id * 10
    m.away_team_id = match_id * 10 + 1
    m.home_team.id = m.home_team_id
    m.home_team.name = home_name
    m.home_team.api_id = 11
    m.home_team.short_name = home_name.split()[-1]
    m.away_team.id = m.away_team_id
    m.away_team.name = away_name
    m.away_team.api_id = 12
    m.away_team.short_name = away_name.split()[-1]
    m.competition = competition
    m.stage = 'REGULAR_SEASON'
    m.matchday = 38
    m.date = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return m


# ----------------------------------------------------------------------------
# /api/value-bets
# ----------------------------------------------------------------------------

class TestValueBetsEndpoint:
    def test_503_when_model_not_loaded(self, client):
        client.model_data = None
        c = client.app.test_client()
        resp = c.get('/api/value-bets')
        assert resp.status_code == 503
        assert resp.get_json()['error'] == 'Model not loaded'

    def test_returns_enabled_false_when_odds_key_missing(self, client, monkeypatch):
        monkeypatch.delenv('ODDS_API_KEY', raising=False)
        # Build a fresh client that doesn't see the key
        from odds_api import OddsAPIClient
        client.odds_client = OddsAPIClient()
        c = client.app.test_client()
        resp = c.get('/api/value-bets')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['enabled'] is False
        assert body['value_bets'] == []
        assert 'Odds integration not configured' in body['meta']['message']

    def test_happy_path_with_mocked_odds_returns_picks(self, client):
        # Pretend our model says home is heavily favoured
        client.db.get_upcoming_matches.return_value = [
            _stub_match(1, 'Arsenal FC', 'Burnley FC'),
        ]
        # Skip real feature computation + prediction by pre-seeding the cache
        client.prediction_cache.set(10, 11, {
            'match_result': {
                'outcome': 'HOME_WIN',
                'probabilities': {'home_win': 0.70, 'draw': 0.20, 'away_win': 0.10},
                'confidence': 0.70,
                'ensemble_agreement': 0.92,
            },
            'double_chance': {}, 'markets': {}, 'combos': {}, 'match_stats': {},
        }, date_str=None)
        # Match the date_str the endpoint generates
        date_key = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%d')
        client.prediction_cache.set(10, 11, client.prediction_cache.get(10, 11) or {
            'match_result': {
                'outcome': 'HOME_WIN',
                'probabilities': {'home_win': 0.70, 'draw': 0.20, 'away_win': 0.10},
                'confidence': 0.70,
                'ensemble_agreement': 0.92,
            },
            'double_chance': {}, 'markets': {}, 'combos': {}, 'match_stats': {},
        }, date_str=date_key)

        # Mock odds_client to return a single-market response
        client.odds_client = MagicMock()
        client.odds_client.enabled = True
        client.odds_client.odds_for_match.return_value = {
            'h2h': {
                'home': {'best': {'price': 2.00, 'bookmaker': 'Pinnacle'}, 'median': 1.95, 'count': 4},
                'draw': {'best': {'price': 3.80, 'bookmaker': 'Bet365'},   'median': 3.70, 'count': 4},
                'away': {'best': {'price': 5.50, 'bookmaker': 'Pinnacle'}, 'median': 5.30, 'count': 4},
                'overround_best': 1.05, 'overround_median': 1.07,
            },
        }
        client.odds_client.quota_status.return_value = {'remaining': 420, 'used': 80, 'low': False}

        c = client.app.test_client()
        resp = c.get('/api/value-bets?min_edge=0.05&books=sharp')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['enabled'] is True
        assert body['meta']['count'] >= 1
        # Home should be the strongest pick — model 70%, best odds 2.0 → edge ~40%
        top = body['value_bets'][0]
        assert top['outcome_key'] == 'home'
        assert top['bookmaker'] == 'Pinnacle'
        assert top['edge_best'] > 0.30
        # New fields present
        assert 'edge_median' in top
        assert 'ensemble_agreement' in top
        assert top['ensemble_agreement'] == 0.92
        # Meta echoes back the params
        assert body['meta']['books'] == 'sharp'
        assert body['meta']['edge_ref'] == 'median'

    def test_min_edge_filters(self, client):
        client.db.get_upcoming_matches.return_value = [
            _stub_match(1, 'Arsenal FC', 'Burnley FC'),
        ]
        date_key = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%d')
        client.prediction_cache.set(10, 11, {
            'match_result': {
                'outcome': 'HOME_WIN',
                'probabilities': {'home_win': 0.50, 'draw': 0.25, 'away_win': 0.25},
                'confidence': 0.50,
            },
        }, date_str=date_key)

        client.odds_client = MagicMock()
        client.odds_client.enabled = True
        # Fair odds — no edge anywhere
        client.odds_client.odds_for_match.return_value = {
            'h2h': {
                'home': {'best': {'price': 2.00, 'bookmaker': 'Pinnacle'}, 'median': 2.00, 'count': 3},
                'draw': {'best': {'price': 4.00, 'bookmaker': 'Pinnacle'}, 'median': 4.00, 'count': 3},
                'away': {'best': {'price': 4.00, 'bookmaker': 'Pinnacle'}, 'median': 4.00, 'count': 3},
            },
        }
        client.odds_client.quota_status.return_value = {'remaining': 400, 'used': 100, 'low': False}

        c = client.app.test_client()
        resp = c.get('/api/value-bets?min_edge=0.10')
        body = resp.get_json()
        assert body['meta']['count'] == 0


# ----------------------------------------------------------------------------
# /api/admin/odds-status — auth + payload shape
# ----------------------------------------------------------------------------

class TestOddsStatus:
    def test_unauthorized_without_token(self, client):
        c = client.app.test_client()
        resp = c.get('/api/admin/odds-status')
        assert resp.status_code == 401

    def test_returns_quota_with_valid_token(self, client, monkeypatch):
        monkeypatch.setenv('RELOAD_TOKEN', 'secret')
        client.odds_client = MagicMock()
        client.odds_client.enabled = True
        client.odds_client.quota_status.return_value = {'remaining': 350, 'used': 150, 'low': False}
        client.odds_client.bookmaker_divergence_stats.return_value = None

        c = client.app.test_client()
        resp = c.get('/api/admin/odds-status', headers={'X-Reload-Token': 'secret'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['enabled'] is True
        assert body['quota']['remaining'] == 350


# ----------------------------------------------------------------------------
# /api/predictions/calibration
# ----------------------------------------------------------------------------

class TestCalibrationEndpoint:
    def test_empty_response_when_no_evaluated_predictions(self, client):
        client.db.session.query.return_value.filter.return_value.all.return_value = []
        c = client.app.test_client()
        resp = c.get('/api/predictions/calibration')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['summary']['total'] == 0


# ----------------------------------------------------------------------------
# Bet endpoints — validation + market-coverage
# ----------------------------------------------------------------------------

class TestBetEndpoint:
    def test_post_rejects_missing_fields(self, client):
        c = client.app.test_client()
        resp = c.post('/api/bets', json={'match_id': 1})
        assert resp.status_code == 400
        assert 'Missing fields' in resp.get_json()['error']

    def test_post_rejects_unsupported_market(self, client):
        c = client.app.test_client()
        resp = c.post('/api/bets', json={
            'match_id': 1, 'market': 'corner_kicks', 'outcome_key': 'home',
            'odds_at_bet': 2.0, 'stake': 100,
        })
        assert resp.status_code == 400
        assert 'Unsupported market' in resp.get_json()['error']

    def test_post_rejects_invalid_outcome_key(self, client):
        c = client.app.test_client()
        resp = c.post('/api/bets', json={
            'match_id': 1, 'market': 'h2h', 'outcome_key': 'over',
            'odds_at_bet': 2.0, 'stake': 100,
        })
        assert resp.status_code == 400

    def test_post_rejects_invalid_odds_or_stake(self, client):
        c = client.app.test_client()
        # Odds ≤ 1.0 are nonsensical
        resp = c.post('/api/bets', json={
            'match_id': 1, 'market': 'h2h', 'outcome_key': 'home',
            'odds_at_bet': 0.99, 'stake': 100,
        })
        assert resp.status_code == 400
        # Stake ≤ 0
        resp = c.post('/api/bets', json={
            'match_id': 1, 'market': 'h2h', 'outcome_key': 'home',
            'odds_at_bet': 2.0, 'stake': 0,
        })
        assert resp.status_code == 400

    def test_post_404_when_match_not_found(self, client):
        client.db.session.query.return_value.filter_by.return_value.first.return_value = None
        c = client.app.test_client()
        resp = c.post('/api/bets', json={
            'match_id': 99999, 'market': 'h2h', 'outcome_key': 'home',
            'odds_at_bet': 2.0, 'stake': 100,
        })
        assert resp.status_code == 404
