"""
Time-ordered stacking.

WHY NOT StackingClassifier
--------------------------
sklearn's StackingClassifier builds the meta-learner's training inputs with
`cross_val_predict`, which requires the CV scheme to PARTITION the data — every
sample in exactly one test fold. TimeSeriesSplit deliberately doesn't (it leaves
the earliest block train-only), so passing it raises
"cross_val_predict only works for partitions". The production trainer therefore
fell back to `StratifiedKFold(shuffle=True)`, which trains the meta-learner on
fold predictions produced by base models that had already seen later matches.

This class does the expanding-window scheme by hand instead: block k's
meta-features come from base models fitted only on blocks 0..k-1. The earliest
block is never used as a meta-training target, exactly as TimeSeriesSplit
intends. Base models are then refitted on the full training set for inference.

Rows must arrive in chronological order — the caller sorts by date before
splitting, so `fit` treats row order as time order.

Kept in its own module rather than in model_training so unpickling a saved model
inside the API container doesn't drag in the training pipeline's imports.
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone


class TimeSeriesStack(BaseEstimator, ClassifierMixin):
    """Stacked ensemble whose meta-learner only ever sees out-of-time predictions.

    Exposes `named_estimators_` like StackingClassifier so downstream code that
    inspects the base learners — prediction_service._ensemble_agreement, which
    derives its confidence proxy from base-model disagreement — keeps working.
    """

    def __init__(self, estimators, final_estimator, n_blocks: int = 5):
        self.estimators = estimators
        self.final_estimator = final_estimator
        self.n_blocks = n_blocks

    def fit(self, X, y):
        X = np.asarray(X)
        y = np.asarray(y)
        if len(X) != len(y):
            raise ValueError(f"X and y differ in length: {len(X)} vs {len(y)}")

        self.classes_ = np.unique(y)
        n_blocks = max(2, min(self.n_blocks, len(X) // 50 or 2))
        blocks = np.array_split(np.arange(len(X)), n_blocks)

        meta_X, meta_y, used = [], [], 0
        for k in range(1, n_blocks):
            past = np.concatenate(blocks[:k])
            block = blocks[k]
            # A base learner fitted on a slice missing a class emits fewer
            # predict_proba columns, which would misalign the meta-features.
            if len(np.unique(y[past])) != len(self.classes_) or len(block) == 0:
                continue
            preds = []
            for _name, est in self.estimators:
                fold_est = clone(est)
                fold_est.fit(X[past], y[past])
                preds.append(fold_est.predict_proba(X[block]))
            meta_X.append(np.hstack(preds))
            meta_y.append(y[block])
            used += 1

        if not meta_X:
            raise ValueError(
                "No usable expanding-window block — training set is too small or "
                "too class-imbalanced for time-ordered stacking."
            )
        self.n_blocks_used_ = used

        # The per-block guard above checks the PAST has every class; it says
        # nothing about the blocks used as targets. When a class only turns up
        # late, every earlier block gets skipped and the surviving targets can
        # all carry the same label — the meta-learner then dies inside the
        # solver with "needs samples of at least 2 classes", which points
        # nowhere near the real cause. Fail here instead, naming it.
        meta_y_all = np.concatenate(meta_y)
        if len(np.unique(meta_y_all)) < 2:
            raise ValueError(
                "Time-ordered stacking left the meta-learner a single-class "
                f"target ({np.unique(meta_y_all).tolist()}) across "
                f"{used} block(s). The classes are too unevenly spread over time "
                "to build out-of-time meta-features — widen the training window "
                "or lower n_blocks."
            )

        self.final_estimator_ = clone(self.final_estimator)
        self.final_estimator_.fit(np.vstack(meta_X), meta_y_all)

        # Refit the base learners on everything for inference.
        self.named_estimators_ = {}
        for name, est in self.estimators:
            full = clone(est)
            full.fit(X, y)
            self.named_estimators_[name] = full
        return self

    def _meta_features(self, X):
        return np.hstack([
            self.named_estimators_[name].predict_proba(X) for name, _est in self.estimators
        ])

    def predict_proba(self, X):
        return self.final_estimator_.predict_proba(self._meta_features(np.asarray(X)))

    def predict(self, X):
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]
