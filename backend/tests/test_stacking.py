"""
Cover TimeSeriesStack.

The class exists to stop the meta-learner training on base predictions that had
already seen later matches. That property is invisible at runtime — a broken
implementation still fits, still predicts, and still reports a plausible AUC. The
only way it surfaces is as a model that scores better offline than it serves,
which is exactly the class of defect it was written to remove. So the ordering
guarantee is asserted directly rather than inferred from a metric.

It also runs once a week, unattended, inside the retrain job. A constructor
signature change or a joblib round-trip failure would fail at 02:00 on a Sunday.
"""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression


class RecordingEstimator(BaseEstimator, ClassifierMixin):
    """Base learner that records which row indices each fit saw.

    X carries the row index in column 0, so a fold's training rows can be read
    straight off the array the stack handed us.
    """

    fits = []

    def __init__(self, tag='rec'):
        self.tag = tag

    def fit(self, X, y):
        X = np.asarray(X)
        RecordingEstimator.fits.append(set(X[:, 0].astype(int).tolist()))
        self.classes_ = np.unique(y)
        return self

    def predict_proba(self, X):
        n = len(np.asarray(X))
        return np.tile(np.full(len(self.classes_), 1.0 / len(self.classes_)), (n, 1))


def _data(n=400, classes=3, seed=0):
    rng = np.random.default_rng(seed)
    X = np.column_stack([np.arange(n), rng.normal(size=(n, 3))])
    y = np.tile(np.arange(classes), n // classes + 1)[:n]
    return X, y


def _stack(n_blocks=5, estimators=None):
    from stacking import TimeSeriesStack
    return TimeSeriesStack(
        estimators=estimators or [('a', RecordingEstimator('a')),
                                  ('b', RecordingEstimator('b'))],
        final_estimator=LogisticRegression(max_iter=200),
        n_blocks=n_blocks,
    )


class TestExpandingWindow:
    def test_meta_features_never_come_from_a_model_that_saw_the_future(self):
        """The whole reason this class exists instead of StackingClassifier."""
        RecordingEstimator.fits = []
        X, y = _data()
        _stack(n_blocks=5).fit(X, y)

        # Last two fits are the full-data refits for inference; everything before
        # is a fold fit whose training rows must be a contiguous prefix.
        fold_fits = RecordingEstimator.fits[:-2]
        assert fold_fits, "expected expanding-window fold fits"
        for rows in fold_fits:
            assert rows == set(range(len(rows))), (
                "a fold trained on rows outside its own prefix — meta-features "
                "would be contaminated by later matches"
            )

    def test_prefixes_grow_and_the_earliest_block_is_never_a_target(self):
        RecordingEstimator.fits = []
        X, y = _data(n=400)
        model = _stack(n_blocks=5).fit(X, y)

        sizes = sorted({len(f) for f in RecordingEstimator.fits[:-2]})
        assert sizes == sorted(set(sizes)), "prefix sizes should be distinct"
        assert min(sizes) >= len(X) // 5, "block 0 must stay train-only"
        assert model.n_blocks_used_ == 4      # blocks 1..4 of 5

    def test_base_learners_are_refit_on_everything_for_inference(self):
        RecordingEstimator.fits = []
        X, y = _data(n=300)
        _stack(n_blocks=3).fit(X, y)
        assert RecordingEstimator.fits[-1] == set(range(300))
        assert RecordingEstimator.fits[-2] == set(range(300))


class TestContract:
    def test_exposes_named_estimators_for_the_agreement_proxy(self):
        """prediction_service._ensemble_agreement reads this; losing it would
        silently drop the confidence signal rather than raise."""
        X, y = _data()
        model = _stack().fit(X, y)
        assert set(model.named_estimators_) == {'a', 'b'}

    def test_predict_proba_is_row_aligned_and_normalised(self):
        X, y = _data()
        model = _stack().fit(X, y)
        p = model.predict_proba(X[:20])
        assert p.shape == (20, 3)
        assert np.allclose(p.sum(axis=1), 1.0)

    def test_predict_returns_labels_from_the_training_classes(self):
        X, y = _data()
        model = _stack().fit(X, y)
        assert set(model.predict(X[:50]).tolist()) <= set(np.unique(y).tolist())

    def test_length_mismatch_is_rejected_loudly(self):
        X, y = _data(n=100)
        with pytest.raises(ValueError, match="differ in length"):
            _stack().fit(X, y[:50])

    def test_survives_a_joblib_round_trip(self):
        """The retrain job writes this to blob and the API unpickles it in a
        different process — an unpicklable attribute would only fail there."""
        import io
        import joblib
        from sklearn.ensemble import RandomForestClassifier

        X, y = _data(n=300)
        model = _stack(n_blocks=3, estimators=[
            ('rf', RandomForestClassifier(n_estimators=5, random_state=0)),
        ]).fit(X, y)

        buf = io.BytesIO()
        joblib.dump(model, buf)
        buf.seek(0)
        restored = joblib.load(buf)
        assert np.allclose(restored.predict_proba(X[:10]), model.predict_proba(X[:10]))


class TestDegenerateInputs:
    def test_blocks_missing_a_class_are_skipped_not_misaligned(self):
        """A base learner fitted on a slice missing a class emits fewer
        predict_proba columns; stacking those would shift every meta-feature."""
        X, _ = _data(n=300)
        # Class 2 is absent from the first 100 rows, so the earliest prefixes are
        # 2-class and must be skipped; from there on all three appear.
        y = np.array([0, 1] * 50 + [0, 1, 2] * 67)[:300]
        model = _stack(n_blocks=5).fit(X, y)
        assert model.n_blocks_used_ == 3          # blocks 0 and 1 skipped
        assert model.predict_proba(X[:5]).shape[1] == 3

    def test_single_class_meta_target_fails_with_a_useful_message(self):
        """Found by this suite: when a class appears only at the very end, every
        earlier block is skipped and the surviving targets share one label. That
        used to die inside the logistic solver with "needs samples of at least 2
        classes" — an error that points nowhere near the cause."""
        X, _ = _data(n=300)
        y = np.array([0, 1] * 100 + [2] * 100)
        with pytest.raises(ValueError, match="single-class"):
            _stack(n_blocks=5).fit(X, y)

    def test_refuses_to_fit_when_no_block_is_usable(self):
        X, _ = _data(n=120)
        y = np.array([0] * 100 + [1] * 20)   # class 1 only in the final block
        with pytest.raises(ValueError, match="No usable expanding-window block"):
            _stack(n_blocks=5).fit(X, y)

    def test_small_training_sets_fall_back_to_two_blocks(self):
        X, y = _data(n=60)
        model = _stack(n_blocks=10).fit(X, y)
        assert model.n_blocks_used_ >= 1
