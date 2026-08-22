"""
Unit tests for TemperatureCalibrator.

Covers:
  - T=1 is identity (no transform)
  - T<1 sharpens, T>1 softens
  - Argmax preserved under any T
  - Probs normalize to 1
  - Fit recovers a known temperature on synthetic data
  - JSON round-trip preserves state
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from calibrator import TemperatureCalibrator, expected_calibration_error


# ----------------------------------------------------------------------------
# Transform: math properties
# ----------------------------------------------------------------------------

class TestTransform:
    def test_temperature_one_is_identity(self):
        probs = np.array([[0.5, 0.3, 0.2], [0.1, 0.7, 0.2]])
        cal = TemperatureCalibrator(temperature=1.0)
        np.testing.assert_allclose(cal.transform(probs), probs, atol=1e-9)

    def test_low_temperature_sharpens_distribution(self):
        # T < 1 → pushes max higher, min lower
        probs = np.array([[0.5, 0.3, 0.2]])
        cal = TemperatureCalibrator(temperature=0.5)
        out = cal.transform(probs)[0]
        assert out[0] > 0.5  # max went up
        assert out[2] < 0.2  # min went down

    def test_high_temperature_softens_distribution(self):
        # T > 1 → compresses toward uniform (0.333 each)
        probs = np.array([[0.7, 0.2, 0.1]])
        cal = TemperatureCalibrator(temperature=2.0)
        out = cal.transform(probs)[0]
        assert out[0] < 0.7
        assert out[2] > 0.1

    def test_output_sums_to_one(self):
        probs = np.array([[0.5, 0.3, 0.2], [0.1, 0.7, 0.2], [0.33, 0.33, 0.34]])
        for T in (0.5, 0.8, 1.0, 1.5, 2.0):
            out = TemperatureCalibrator(temperature=T).transform(probs)
            np.testing.assert_allclose(out.sum(axis=1), 1.0, atol=1e-9)

    def test_argmax_preserved_under_any_temperature(self):
        # Temperature scaling must never change which class is the predicted one
        probs = np.array([[0.5, 0.3, 0.2], [0.1, 0.7, 0.2], [0.2, 0.3, 0.5]])
        original_argmax = probs.argmax(axis=1)
        for T in (0.3, 0.7, 1.0, 1.5, 3.0):
            new_argmax = TemperatureCalibrator(temperature=T).transform(probs).argmax(axis=1)
            np.testing.assert_array_equal(new_argmax, original_argmax)

    def test_1d_input_returns_1d_output(self):
        # Convenience: accept a single probability vector without forcing reshape
        probs = np.array([0.5, 0.3, 0.2])
        out = TemperatureCalibrator(temperature=0.8).transform(probs)
        assert out.shape == (3,)
        assert abs(out.sum() - 1.0) < 1e-9

    def test_handles_zero_probabilities(self):
        # Models can output exact 0 on impossible classes; calibrator must not crash
        probs = np.array([[0.6, 0.4, 0.0]])
        out = TemperatureCalibrator(temperature=0.8).transform(probs)
        assert np.isfinite(out).all()
        assert abs(out.sum() - 1.0) < 1e-9


# ----------------------------------------------------------------------------
# Fit: recovers known temperature on synthetic data
# ----------------------------------------------------------------------------

class TestFit:
    def test_fit_recovers_known_temperature(self):
        """
        Generate data from a 'true' distribution, then sharpen it to T_inv to
        simulate an under-dispersed model. Fitting should recover T_inv.
        """
        rng = np.random.default_rng(42)
        n = 3000
        # Sample 'true' probabilities from a Dirichlet so the test isn't tied
        # to one shape
        true_probs = rng.dirichlet([2, 2, 2], size=n)
        # Sample labels from the true distribution
        labels = np.array([rng.choice(3, p=p) for p in true_probs])
        # Take true probs and SOFTEN them (T_inv > 1) → simulates model
        # under-dispersion. Fit should learn T ≈ 1/T_inv to undo it.
        T_distortion = 1.5
        distorted = TemperatureCalibrator._transform(true_probs, T_distortion)

        cal = TemperatureCalibrator().fit(distorted, labels)
        # Recovered T should be close to 1/1.5 ≈ 0.667
        assert 0.55 < cal.temperature < 0.80, f"Got T={cal.temperature}"
        assert cal.fit_samples == n

    def test_fit_returns_one_when_already_calibrated(self):
        # Data drawn directly from probs: a well-calibrated source.
        # The fitter should land on T ≈ 1.
        rng = np.random.default_rng(0)
        n = 2000
        probs = rng.dirichlet([2, 2, 2], size=n)
        labels = np.array([rng.choice(3, p=p) for p in probs])

        cal = TemperatureCalibrator().fit(probs, labels)
        assert 0.85 < cal.temperature < 1.15

    def test_fit_validates_input_shapes(self):
        cal = TemperatureCalibrator()
        with pytest.raises(ValueError):
            cal.fit(np.array([0.5, 0.5]), np.array([0]))  # 1D probs
        with pytest.raises(ValueError):
            cal.fit(np.array([[0.5, 0.5]]), np.array([0, 1]))  # mismatched lengths

    def test_fit_populates_diagnostics(self):
        rng = np.random.default_rng(1)
        probs = rng.dirichlet([2, 2, 2], size=500)
        labels = np.array([rng.choice(3, p=p) for p in probs])
        cal = TemperatureCalibrator().fit(probs, labels)
        assert cal.fit_samples == 500
        assert cal.fit_nll_before is not None
        assert cal.fit_nll_after is not None
        # Fit should not increase NLL (worst case it's a no-op at T=1)
        assert cal.fit_nll_after <= cal.fit_nll_before + 1e-6


# ----------------------------------------------------------------------------
# Serialization round-trip
# ----------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        original = TemperatureCalibrator(temperature=0.75)
        original.fit_samples = 1234
        original.fit_nll_before = 100.0
        original.fit_nll_after = 80.0

        restored = TemperatureCalibrator.from_dict(original.to_dict())
        assert restored.temperature == 0.75
        assert restored.fit_samples == 1234
        assert restored.fit_nll_before == 100.0

    def test_save_and_load_file(self):
        cal = TemperatureCalibrator(temperature=0.823)
        cal.fit_samples = 500
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            cal.save(f.name)
        try:
            loaded = TemperatureCalibrator.load(f.name)
            assert abs(loaded.temperature - 0.823) < 1e-9
            assert loaded.fit_samples == 500
        finally:
            Path(f.name).unlink()

    def test_save_writes_valid_json(self):
        cal = TemperatureCalibrator(temperature=0.9)
        with tempfile.NamedTemporaryFile(mode='r', suffix='.json', delete=False) as f:
            pass
        try:
            cal.save(f.name)
            with open(f.name) as g:
                data = json.load(g)
            assert data['version'] == 1
            assert data['temperature'] == 0.9
        finally:
            Path(f.name).unlink()


# ----------------------------------------------------------------------------
# ECE helper — sanity that fit actually improves it
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# IsotonicCalibrator — per-outcome flexibility
# ----------------------------------------------------------------------------

class TestIsotonic:
    def _make_synthetic(self, n=1500, seed=0):
        rng = np.random.default_rng(seed)
        true_probs = rng.dirichlet([2, 2, 2], size=n)
        labels = np.array([rng.choice(3, p=p) for p in true_probs])
        return true_probs, labels

    def test_isotonic_fit_and_transform_shape(self):
        from calibrator import IsotonicCalibrator
        probs, labels = self._make_synthetic(n=800)
        cal = IsotonicCalibrator().fit(probs, labels,
                                        class_names=['AWAY_WIN', 'DRAW', 'HOME_WIN'])
        out = cal.transform(probs)
        assert out.shape == probs.shape
        # Each row sums to 1
        np.testing.assert_allclose(out.sum(axis=1), 1.0, atol=1e-9)
        # 3 per-class models stored
        assert len(cal.models) == 3
        assert cal.fit_samples == 800

    def test_isotonic_reduces_nll(self):
        from calibrator import IsotonicCalibrator
        # Distort the true probs to create miscalibration
        probs, labels = self._make_synthetic(n=2000)
        distorted = TemperatureCalibrator._transform(probs, 1.7)
        cal = IsotonicCalibrator().fit(distorted, labels)
        assert cal.fit_nll_after < cal.fit_nll_before

    def test_isotonic_1d_input(self):
        from calibrator import IsotonicCalibrator
        probs, labels = self._make_synthetic(n=500)
        cal = IsotonicCalibrator().fit(probs, labels)
        single = np.array([0.5, 0.3, 0.2])
        out = cal.transform(single)
        assert out.shape == (3,)
        assert abs(out.sum() - 1.0) < 1e-9

    def test_isotonic_save_and_load_roundtrip(self):
        from calibrator import IsotonicCalibrator
        probs, labels = self._make_synthetic(n=500)
        cal = IsotonicCalibrator().fit(probs, labels)
        out_before = cal.transform(probs[:5])

        with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as f:
            cal.save(f.name)
        try:
            loaded = IsotonicCalibrator.load(f.name)
            out_after = loaded.transform(probs[:5])
            np.testing.assert_allclose(out_before, out_after, atol=1e-9)
            assert loaded.fit_samples == 500
        finally:
            Path(f.name).unlink()


def test_fit_reduces_ece_on_miscalibrated_data():
    rng = np.random.default_rng(123)
    n = 3000
    true_probs = rng.dirichlet([2, 2, 2], size=n)
    labels = np.array([rng.choice(3, p=p) for p in true_probs])
    # Soften → model becomes under-confident
    distorted = TemperatureCalibrator._transform(true_probs, 1.6)

    ece_before = expected_calibration_error(distorted, labels)
    cal = TemperatureCalibrator().fit(distorted, labels)
    ece_after = expected_calibration_error(cal.transform(distorted), labels)
    assert ece_after < ece_before
    # The fix should be substantial — at least halve the ECE
    assert ece_after < ece_before * 0.6


class TestIsotonicResolution:
    """
    An isotonic calibrator is a step function. Fit on too few samples it has very
    few steps, and the probability space collapses: distinct fixtures come out
    with byte-identical probabilities and an edge against a bookmaker price stops
    meaning anything.

    Found in service, not in theory. The deployed calibrator had 18 distinct
    outputs on its worst class (1,067 fit samples). Six different fixtures
    rendered as three predictions on the dashboard, and because isotonic is
    fitted per class and then renormalised, it also flipped the model's pick on
    two of them.
    """

    def _fit(self, n, seed=0):
        import numpy as np
        from calibrator import IsotonicCalibrator
        rng = np.random.default_rng(seed)
        probs = rng.dirichlet([2.0, 2.0, 2.0], size=n)
        labels = np.array([rng.choice(3, p=p) for p in probs])
        cal = IsotonicCalibrator()
        cal.fit(probs, labels)
        return cal

    def test_resolution_counts_distinct_outputs(self):
        cal = self._fit(2000)
        assert cal.resolution() > 0

    def test_a_small_fit_produces_a_coarse_calibrator(self):
        """The condition the loader screens for."""
        small = self._fit(150)
        large = self._fit(4000)
        assert small.resolution() < large.resolution()

    def test_resolution_is_zero_for_an_unfitted_calibrator(self):
        from calibrator import IsotonicCalibrator
        assert IsotonicCalibrator().resolution() == 0

    def test_isotonic_can_reorder_outcomes(self):
        """
        Why nothing downstream may assume the calibrated argmax matches the raw
        one. Fitted per class then renormalised, isotonic is not monotone in the
        joint distribution — unlike temperature scaling.
        """
        import numpy as np
        from calibrator import IsotonicCalibrator
        rng = np.random.default_rng(7)
        n = 600
        probs = rng.dirichlet([2.0, 2.0, 2.0], size=n)
        # Label draws far more often than the model expects, so the draw class's
        # isotonic curve lifts hard and can overtake a marginal favourite.
        labels = np.array([1 if rng.random() < 0.62 else rng.choice([0, 2]) for _ in range(n)])
        cal = IsotonicCalibrator()
        cal.fit(probs, labels)
        flipped = sum(1 for p in probs
                      if int(np.argmax(p)) != int(np.argmax(cal.transform(p))))
        assert flipped > 0, "expected per-class isotonic to reorder at least one row"

    def test_temperature_scaling_never_reorders(self):
        """The property that makes temperature safe as the default."""
        import numpy as np
        from calibrator import TemperatureCalibrator
        rng = np.random.default_rng(3)
        probs = rng.dirichlet([2.0, 2.0, 2.0], size=500)
        for t in (0.5, 0.8, 1.3, 2.0):
            cal = TemperatureCalibrator(temperature=t)
            for p in probs:
                assert int(np.argmax(p)) == int(np.argmax(cal.transform(p)))
