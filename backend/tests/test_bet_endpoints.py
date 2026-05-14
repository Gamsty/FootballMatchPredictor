"""
Endpoint tests for bet mutation paths.

We don't go through the full app fixture (model load + DB connect + odds API)
— Flask's test_client is enough to exercise the routing + validation +
permission layers. Tests that need a real DB session use the test_persistence
in-memory fixture.

What's NOT covered here:
  - The actual settle logic on real matches (covered by test_combo_settle.py
    via the resolver path).
  - The full /api/value-bets pipeline (covered by test_value_bets_endpoint.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch):
    """
    Flask test client. Wipes BET_WRITE_TOKEN so writes go through without
    auth (the auth layer itself is exercised by test_combo_settle.py).
    """
    monkeypatch.delenv('BET_WRITE_TOKEN', raising=False)
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# POST /api/bets/combo — validation paths
# ---------------------------------------------------------------------------

class TestComboEndpointValidation:
    def test_rejects_under_two_legs(self, client):
        r = client.post('/api/bets/combo', json={'legs': [], 'stake': 100})
        assert r.status_code == 400
        assert 'at least 2' in r.get_json()['error']

    def test_rejects_single_leg(self, client):
        r = client.post('/api/bets/combo', json={
            'legs': [{'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0}],
            'stake': 100,
        })
        assert r.status_code == 400

    def test_rejects_over_ten_legs(self, client):
        legs = [{'match_id': i, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0}
                for i in range(11)]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400
        assert '10 legs' in r.get_json()['error']

    def test_rejects_zero_stake(self, client):
        legs = [{'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
                {'match_id': 2, 'market': 'h2h', 'outcome_key': 'away', 'odds': 2.5}]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 0})
        assert r.status_code == 400
        assert 'stake' in r.get_json()['error']

    def test_rejects_negative_stake(self, client):
        legs = [{'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
                {'match_id': 2, 'market': 'h2h', 'outcome_key': 'away', 'odds': 2.5}]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': -50})
        assert r.status_code == 400

    def test_rejects_leg_missing_required_field(self, client):
        legs = [
            {'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
            {'match_id': 2, 'market': 'h2h'},  # missing outcome_key + odds
        ]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400
        assert 'leg 1' in r.get_json()['error']

    def test_rejects_unsupported_market(self, client):
        legs = [
            {'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
            {'match_id': 2, 'market': 'asian_handicap', 'outcome_key': 'home', 'odds': 1.9},
        ]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400
        assert 'unsupported market' in r.get_json()['error']

    def test_rejects_unsupported_outcome_key(self, client):
        legs = [
            {'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
            {'match_id': 2, 'market': 'totals_2_5', 'outcome_key': 'goofy', 'odds': 1.9},
        ]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400

    def test_rejects_leg_with_odds_below_1(self, client):
        legs = [
            {'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
            {'match_id': 2, 'market': 'h2h', 'outcome_key': 'away', 'odds': 1.0},  # =1.0 invalid
        ]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400
        assert 'odds' in r.get_json()['error']

    def test_rejects_duplicate_match_id(self, client):
        legs = [
            {'match_id': 1, 'market': 'h2h', 'outcome_key': 'home', 'odds': 2.0},
            {'match_id': 1, 'market': 'totals_2_5', 'outcome_key': 'over', 'odds': 1.9},
        ]
        r = client.post('/api/bets/combo', json={'legs': legs, 'stake': 100})
        assert r.status_code == 400
        assert 'duplicate match_id' in r.get_json()['error']


# ---------------------------------------------------------------------------
# Compound bet outcome_key normalization
# ---------------------------------------------------------------------------

class TestCompoundOutcomeKeyNormalization:
    """
    Frontend sends compound outcome_keys with capital prefix (`H_btts_yes`) —
    that's how prediction_service.py emits them. Backend must lowercase before
    validating against _BET_OUTCOME_RESOLVERS['compound'].
    """

    def test_post_with_capital_compound_key_validates(self, client):
        # match_id=99999 won't exist → expect 404 (validation passed but FK
        # check fails). The KEY signal: NOT 400 "Unsupported outcome_key".
        r = client.post('/api/bets', json={
            'match_id': 99999,
            'market': 'compound',
            'outcome_key': 'H_btts_yes',  # capital prefix
            'odds_at_bet': 2.5,
            'stake': 100,
        })
        # Either 404 (match not found, validation passed) or 500 (DB error in test env)
        # — what we DON'T want is 400 with "Unsupported outcome_key" message.
        if r.status_code == 400:
            body = r.get_json() or {}
            assert 'Unsupported outcome_key' not in body.get('error', ''), \
                "outcome_key normalization broken: H_btts_yes should be accepted"


# ---------------------------------------------------------------------------
# POST /api/bets — combo market rejected (routes to /combo)
# ---------------------------------------------------------------------------

class TestComboMarketGatedFromSingleEndpoint:
    def test_market_combo_rejected_on_singles_endpoint(self, client):
        """
        market='combo' on POST /api/bets must redirect callers to /api/bets/combo
        — combo bets need legs[] which the singles endpoint doesn't parse.
        Without this, a buggy frontend could create malformed combo rows.
        """
        r = client.post('/api/bets', json={
            'match_id': 1,
            'market': 'combo',
            'outcome_key': 'multi',
            'odds_at_bet': 5.0,
            'stake': 100,
        })
        assert r.status_code == 400
        assert '/api/bets/combo' in r.get_json()['error']
