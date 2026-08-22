"""
Settlement and CLV regressions for the betting engine.

Three defects are pinned here:

  1. A void leg used to void the WHOLE combo. Norsk Tipping — and every other
     book this app targets — sets a void selection to 1.00 and settles on the
     rest, so winning coupons were being refunded at zero.
  2. POSTPONED voided on sight. The feed reschedules the same match row, so the
     bet should wait for the replay instead of closing at zero.
  3. CLV was measured against the best sharp price. For an operator who prices
     every bet at NT that compares an 8-12% margin to a 2-3% one and is negative
     regardless of bet quality; the de-vigged line is the honest comparison.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _combo(legs, *, stake=100.0, odds=6.0):
    return SimpleNamespace(
        id=1, market='combo', outcome_key='multi', stake=stake,
        odds_at_bet=odds, combo_legs=legs, status='pending',
        settled_at=None, profit_loss=None, placed_at=datetime(2026, 5, 1),
    )


def _leg(match_id, odds=2.0):
    return {'match_id': match_id, 'market': 'h2h', 'outcome_key': 'home', 'odds': odds}


def _patch_legs(monkeypatch, results):
    import app
    seq = iter(results)
    monkeypatch.setattr(app, '_resolve_leg', lambda leg: next(seq))


# ---------------------------------------------------------------------------
# Void legs reduce the combo instead of killing it
# ---------------------------------------------------------------------------

class TestVoidLegReducesCombo:
    def test_void_leg_pays_out_on_the_survivors(self, monkeypatch):
        from app import _settle_one_bet
        legs = [_leg(1, 2.0), _leg(2, 3.0), _leg(3, 1.5)]
        _patch_legs(monkeypatch, ['won', 'won', 'void'])
        bet = _combo(legs, stake=100.0, odds=9.0)
        assert _settle_one_bet(bet, SimpleNamespace()) is True
        assert bet.status == 'won'
        # 2.0 x 3.0 = 6.0 on the surviving legs, not the quoted 9.0.
        assert bet.profit_loss == pytest.approx(100.0 * (6.0 - 1.0))

    def test_all_won_pays_the_quoted_price_not_the_rounded_product(self, monkeypatch):
        """Leg odds are stored to 2dp; multiplying them back drifts off the quote."""
        from app import _settle_one_bet
        legs = [_leg(1, 1.83), _leg(2, 2.17), _leg(3, 1.44)]
        _patch_legs(monkeypatch, ['won', 'won', 'won'])
        bet = _combo(legs, stake=100.0, odds=5.7176)
        assert _settle_one_bet(bet, SimpleNamespace()) is True
        assert bet.status == 'won'
        assert bet.profit_loss == pytest.approx(100.0 * (5.7176 - 1.0))

    def test_every_leg_void_is_a_refund(self, monkeypatch):
        from app import _settle_one_bet
        legs = [_leg(1), _leg(2)]
        _patch_legs(monkeypatch, ['void', 'void'])
        bet = _combo(legs)
        assert _settle_one_bet(bet, SimpleNamespace()) is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_missing_leg_price_refunds_rather_than_guessing(self, monkeypatch):
        """Legacy combos stored before per-leg odds existed can't be reduced."""
        from app import _settle_one_bet
        legs = [{'match_id': 1}, {'match_id': 2}]
        _patch_legs(monkeypatch, ['won', 'void'])
        bet = _combo(legs)
        assert _settle_one_bet(bet, SimpleNamespace()) is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_a_lost_leg_still_kills_the_coupon(self, monkeypatch):
        from app import _settle_one_bet
        legs = [_leg(1), _leg(2), _leg(3)]
        _patch_legs(monkeypatch, ['won', 'void', 'lost'])
        bet = _combo(legs)
        assert _settle_one_bet(bet, SimpleNamespace()) is True
        assert bet.status == 'lost'
        assert bet.profit_loss == -100.0


# ---------------------------------------------------------------------------
# Postponement grace period
# ---------------------------------------------------------------------------

class TestPostponementGrace:
    def _single(self):
        return SimpleNamespace(
            id=1, market='h2h', outcome_key='home', stake=100.0,
            odds_at_bet=2.0, status='pending', settled_at=None,
            profit_loss=None, placed_at=datetime(2026, 5, 1), combo_legs=None,
        )

    def test_rescheduled_fixture_stays_pending(self):
        """The feed moves `date` forward on a replay — wait for it."""
        from app import _settle_one_bet
        bet = self._single()
        match = SimpleNamespace(status='POSTPONED', date=_now() + timedelta(days=4),
                                home_score=None, away_score=None, winner=None)
        assert _settle_one_bet(bet, match) is False
        assert bet.status == 'pending'

    def test_abandoned_fixture_voids_after_the_grace_window(self):
        from app import _settle_one_bet
        bet = self._single()
        match = SimpleNamespace(status='POSTPONED', date=_now() - timedelta(days=10),
                                home_score=None, away_score=None, winner=None)
        assert _settle_one_bet(bet, match) is True
        assert bet.status == 'void'
        assert bet.profit_loss == 0.0

    def test_cancelled_voids_immediately_even_with_a_future_date(self):
        from app import _settle_one_bet
        bet = self._single()
        match = SimpleNamespace(status='CANCELLED', date=_now() + timedelta(days=4),
                                home_score=None, away_score=None, winner=None)
        assert _settle_one_bet(bet, match) is True
        assert bet.status == 'void'

    def test_tz_aware_kickoff_is_compared_correctly(self):
        """Match.date is naive UTC in the DB but callers may hand us an aware one."""
        from app import _match_abandoned
        soon = SimpleNamespace(status='POSTPONED',
                               date=datetime.now(timezone.utc) + timedelta(days=2))
        stale = SimpleNamespace(status='POSTPONED',
                                date=datetime.now(timezone.utc) - timedelta(days=10))
        assert _match_abandoned(soon) is False
        assert _match_abandoned(stale) is True

    def test_replayed_fixture_settles_on_its_real_result(self):
        """The whole point: a postponed bet must survive to be graded."""
        from app import _settle_one_bet
        bet = self._single()
        postponed = SimpleNamespace(status='POSTPONED', date=_now() + timedelta(days=3),
                                    home_score=None, away_score=None, winner=None)
        assert _settle_one_bet(bet, postponed) is False
        played = SimpleNamespace(status='FINISHED', date=_now() + timedelta(days=3),
                                 home_score=2, away_score=0, winner='HOME_TEAM')
        assert _settle_one_bet(bet, played) is True
        assert bet.status == 'won'
        assert bet.profit_loss == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# De-vigged closing line
# ---------------------------------------------------------------------------

def _snap(match_id, market, outcome, median, best=None):
    return SimpleNamespace(match_id=match_id, market=market, outcome_key=outcome,
                           median_odds=median, best_odds=best or median)


class TestFairClosingLine:
    def test_devig_removes_the_margin(self):
        from app import _fair_closing_odds
        # 2.00 / 3.50 / 4.00 implies 0.500 + 0.286 + 0.250 = 1.036 -> 3.6% margin.
        latest = {
            (1, 'h2h', 'home'): _snap(1, 'h2h', 'home', 2.00),
            (1, 'h2h', 'draw'): _snap(1, 'h2h', 'draw', 3.50),
            (1, 'h2h', 'away'): _snap(1, 'h2h', 'away', 4.00),
        }
        fair = _fair_closing_odds([1], latest)
        probs = [1.0 / fair[(1, 'h2h', k)] for k in ('home', 'draw', 'away')]
        assert sum(probs) == pytest.approx(1.0)
        # Every fair price is longer than the quoted one — that IS the margin.
        assert fair[(1, 'h2h', 'home')] > 2.00
        assert fair[(1, 'h2h', 'home')] == pytest.approx(2.0 * (1 / 2 + 1 / 3.5 + 1 / 4))

    def test_partial_market_coverage_is_skipped(self):
        """De-vigging 2 of 3 h2h prices would invent the missing outcome's mass."""
        from app import _fair_closing_odds
        latest = {
            (1, 'h2h', 'home'): _snap(1, 'h2h', 'home', 2.00),
            (1, 'h2h', 'draw'): _snap(1, 'h2h', 'draw', 3.50),
        }
        assert _fair_closing_odds([1], latest) == {}

    def test_median_is_preferred_over_best(self):
        """best_odds is a max over N books and is biased long."""
        from app import _fair_closing_odds
        latest = {
            (1, 'btts', 'yes'): _snap(1, 'btts', 'yes', median=1.90, best=2.10),
            (1, 'btts', 'no'): _snap(1, 'btts', 'no', median=1.90, best=2.05),
        }
        fair = _fair_closing_odds([1], latest)
        assert fair[(1, 'btts', 'yes')] == pytest.approx(2.0)

    def test_falls_back_to_best_when_median_absent(self):
        from app import _fair_closing_odds
        latest = {
            (1, 'totals_2_5', 'over'): _snap(1, 'totals_2_5', 'over', None, 1.95),
            (1, 'totals_2_5', 'under'): _snap(1, 'totals_2_5', 'under', None, 1.95),
        }
        fair = _fair_closing_odds([1], latest)
        assert fair[(1, 'totals_2_5', 'over')] == pytest.approx(2.0)

    def test_an_nt_price_can_still_beat_the_fair_line(self):
        """
        The reason this metric exists. NT quotes 2.05 where the sharp best is
        2.20 — a naive CLV calls that negative. Against the fair line of 2.00
        the bet was actually +2.5%.
        """
        from app import _fair_closing_odds
        latest = {
            (1, 'btts', 'yes'): _snap(1, 'btts', 'yes', median=1.90, best=2.20),
            (1, 'btts', 'no'): _snap(1, 'btts', 'no', median=1.90, best=2.05),
        }
        fair = _fair_closing_odds([1], latest)[(1, 'btts', 'yes')]
        nt_price = 2.05
        assert nt_price / 2.20 - 1 < 0            # what the old metric reported
        assert nt_price / fair - 1 == pytest.approx(0.025)


class TestComboClosingUsesSharedSnapshots:
    def test_reads_from_the_preloaded_map(self):
        from app import _combo_legs_closing
        legs = [_leg(1), _leg(2)]
        snaps = {
            (1, 'h2h', 'home'): _snap(1, 'h2h', 'home', 1.90, best=2.00),
            (2, 'h2h', 'home'): _snap(2, 'h2h', 'home', 1.45, best=1.50),
        }
        assert _combo_legs_closing(legs, snaps) == pytest.approx(3.0)

    def test_missing_leg_snapshot_disqualifies_the_combo(self):
        from app import _combo_legs_closing
        legs = [_leg(1), _leg(2)]
        snaps = {(1, 'h2h', 'home'): _snap(1, 'h2h', 'home', 1.90, best=2.00)}
        assert _combo_legs_closing(legs, snaps) is None
