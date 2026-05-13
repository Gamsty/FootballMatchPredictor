"""
Temperature scaling — post-hoc probability calibration for multi-class classifiers.

WHY
---
The stacked-ensemble model is *under-dispersed*: probabilities are clamped
toward the prior (~33% per outcome). Empirically (n=2000):
    Model says 75% home win  →  actual 87%   (under-confident high end)
    Model says 8%  home win  →  actual 1.5%  (over-confident low end)

The same pattern appears on draw and away — probability mass leaks from the
extremes toward the centre. ECE on the predicted side = 0.034, on each
individual outcome 0.04–0.06.

TEMPERATURE SCALING fixes this without retraining: rescale logits by a single
learned parameter T before re-softmaxing. T < 1 sharpens the distribution
(pushes extremes further from the centre); T > 1 softens it. We learn T by
minimising NLL on a validation set of (probs, actual_outcome) pairs.

It's also robust: only one parameter, hard to overfit, doesn't change which
class is argmax, preserves rank-ordering across matches.

WHY NOT something fancier
-------------------------
Platt scaling / isotonic regression are stronger but per-class (one transform
each), so they can change which class is argmax — that's bad when downstream
code depends on `predicted_winner`. Temperature scaling is class-symmetric:
it never changes the argmax, only its confidence. Right trade-off for this
project's stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class TemperatureCalibrator:
    """One-parameter post-softmax probability calibrator.

    Math:
        Given softmax probs p = (p_1, ..., p_K), treat log(p) as logits l.
        New probs: p'_i = exp(l_i / T) / Σ_j exp(l_j / T)
        Equivalent (and numerically nicer): p'_i = p_i^(1/T) / Σ_j p_j^(1/T)

    T < 1  → sharpens (favours extremes)
    T = 1  → identity (no change)
    T > 1  → softens  (compresses toward uniform)
    """

    def __init__(self, temperature: float = 1.0):
        self.temperature = float(temperature)
        # Fit diagnostics, populated by fit(). None until fit is called.
        self.fit_samples: int | None = None
        self.fit_nll_before: float | None = None
        self.fit_nll_after: float | None = None

    @staticmethod
    def _transform(probs: np.ndarray, T: float, eps: float = 1e-10) -> np.ndarray:
        """Apply T to a (N, K) array of probabilities. Returns same shape."""
        # Clip to avoid log(0) / negative bases when probs are exactly 0
        clipped = np.clip(probs, eps, 1.0)
        powered = np.power(clipped, 1.0 / T)
        return powered / powered.sum(axis=-1, keepdims=True)

    def transform(self, probs: np.ndarray) -> np.ndarray:
        """Apply current temperature to probs. Accepts (N, K) or (K,) shape."""
        was_1d = probs.ndim == 1
        if was_1d:
            probs = probs.reshape(1, -1)
        out = self._transform(probs, self.temperature)
        return out[0] if was_1d else out

    def fit(self, probs: np.ndarray, labels: np.ndarray,
            bounds: tuple[float, float] = (0.3, 3.0)) -> 'TemperatureCalibrator':
        """
        Learn T from a validation set.

        Args:
            probs:  (N, K) softmax probabilities from the base model
            labels: (N,) integer class labels in [0, K)
            bounds: search interval for T; 0.3–3.0 is plenty for any realistic model

        Returns self so you can chain `.fit(...).transform(...)`.
        """
        from scipy.optimize import minimize_scalar

        probs = np.asarray(probs, dtype=float)
        labels = np.asarray(labels, dtype=int)
        if probs.ndim != 2:
            raise ValueError(f"probs must be 2-D, got shape {probs.shape}")
        if len(probs) != len(labels):
            raise ValueError(f"probs/labels length mismatch: {len(probs)} vs {len(labels)}")

        n = len(probs)
        row_idx = np.arange(n)

        def nll(T: float) -> float:
            scaled = self._transform(probs, T)
            true_p = np.clip(scaled[row_idx, labels], 1e-12, 1.0)
            return float(-np.sum(np.log(true_p)))

        self.fit_nll_before = nll(1.0)
        result = minimize_scalar(nll, bounds=bounds, method='bounded')
        self.temperature = float(result.x)
        self.fit_nll_after = float(result.fun)
        self.fit_samples = n
        return self

    # ------------------------------------------------------------------
    # Serialization — tiny JSON, no pickle. Calibrator is just one float
    # plus metadata; pickling adds risk for no gain.
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            'version': 1,
            'temperature': self.temperature,
            'fit_samples': self.fit_samples,
            'fit_nll_before': self.fit_nll_before,
            'fit_nll_after': self.fit_nll_after,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'TemperatureCalibrator':
        cal = cls(temperature=d.get('temperature', 1.0))
        cal.fit_samples = d.get('fit_samples')
        cal.fit_nll_before = d.get('fit_nll_before')
        cal.fit_nll_after = d.get('fit_nll_after')
        return cal

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> 'TemperatureCalibrator':
        return cls.from_dict(json.loads(Path(path).read_text()))


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray,
                                bins: int = 10) -> float:
    """
    ECE on a multi-class softmax: bucket the max-probability of each prediction,
    compare bucketed mean confidence against bucketed accuracy.

    Useful for printing a single number before/after fit so the user can see
    if the calibrator actually helped.
    """
    probs = np.asarray(probs)
    labels = np.asarray(labels)
    confidences = probs.max(axis=-1)
    predictions = probs.argmax(axis=-1)
    accuracies = (predictions == labels).astype(float)

    ece = 0.0
    n = len(probs)
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        in_bin = (confidences >= lo) & (confidences < hi if i < bins - 1 else confidences <= hi)
        if not in_bin.any():
            continue
        weight = in_bin.sum() / n
        ece += weight * abs(accuracies[in_bin].mean() - confidences[in_bin].mean())
    return float(ece)
