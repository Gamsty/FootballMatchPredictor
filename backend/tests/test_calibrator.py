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
