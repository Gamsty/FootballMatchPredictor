"""
Predictions backfill core — shared between the CLI job
(backend/jobs/backfill_predictions.py) and the admin endpoint
(/api/admin/backfill-predictions).

Lives in src/ rather than jobs/ because the production container only ships
src/ — the jobs/ directory is left out of the image to keep it small.

WHY THIS EXISTS
---------------
The calibration endpoint (/api/predictions/calibration) needs evaluated
predictions to compute ECE / Brier / curve. We persist predictions for
upcoming matches automatically, but the production DB has 0 evaluated rows
because backfill was never run there.

This module provides the bulk backfill — process FINISHED matches in a
bounded batch and write a Prediction row per match with actual_winner set.
The endpoint variant caps batch size so a single HTTP request doesn't hold
the worker for minutes.

LEAKAGE CAVEAT
--------------
Features per match are computed point-in-time via FeatureEngineer's match_date
cutoff, but model_data['elo_ratings'] is a snapshot frozen at training time.
If a historical match was in the training set, its Elo encodes the outcome.
This is fine for getting calibration shape; not fine for honest backtest ROI.
The CLI has --pit-elo for that case; endpoint doesn't.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

from database import Match, Prediction, PredictionSnapshot
from prediction_service import compute_features, predict_match_result

logger = logging.getLogger("predictions_backfill")


WINNER_MAP = {
    'HOME_WIN': 'HOME_TEAM',
    'AWAY_WIN': 'AWAY_TEAM',
    'DRAW':     'DRAW',
}


def run_backfill(
    *,
    db,
    feature_engineer,
    model_data: dict,
    since: datetime | None = None,
    limit: int = 500,
    overwrite: bool = False,
    commit_every: int = 50,
) -> dict:
    """
    Predict + persist for FINISHED matches in the [since, now] window.

    Args:
        db: DatabaseManager
        feature_engineer: FeatureEngineer
        model_data: loaded model dict (must include 'calibrator' if you want
                    calibrated probs persisted — backend hot-attaches this)
        since: only process matches on/after this date (default: 365 days ago)
        limit: max matches per call (default 500 — endpoint-friendly)
        overwrite: re-predict matches that already have a prediction
        commit_every: flush every N writes for durability

    Returns:
        Summary dict for the caller to log / display.
    """
    if since is None:
        since = (datetime.now(timezone.utc) - timedelta(days=365)).replace(tzinfo=None)

    model_version = str(model_data.get('model_version') or 'unversioned')
    model_type = model_data.get('model_type')

    # Base query — all finished matches in the window with known winner.
    base_filter = and_(
        Match.status == 'FINISHED',
        Match.date >= since,
        Match.winner.isnot(None),
    )

    if not overwrite:
        # Exclude already-predicted matches AT THE SQL LAYER, not in Python.
        # The previous version did `.limit(limit*2)` + Python-side filter, which
        # broke as soon as the first `limit*2` matches were all predicted —
        # the slice came back empty even though un-predicted matches existed
        # further down the date axis. That caused the catch-up loop to stop
        # early with a misleading `remaining_estimate: 0`.
        predicted_subq = db.session.query(Prediction.match_id).subquery()
        matches_q = (
            db.session.query(Match)
            .filter(base_filter)
            .filter(~Match.id.in_(predicted_subq))
            .order_by(Match.date.asc())
            .limit(limit)
        )
    else:
        matches_q = (
            db.session.query(Match)
            .filter(base_filter)
            .order_by(Match.date.asc())
            .limit(limit)
        )

    matches = matches_q.all()
    total = len(matches)
    if total == 0:
        # Even when this batch is empty, compute the honest remaining count.
        # Caller's loop uses this to decide whether to stop. The previous
        # version hardcoded 0 here, which caused premature termination.
        if not overwrite:
            predicted_subq = db.session.query(Prediction.match_id).subquery()
            remaining_now = (
                db.session.query(Match)
                .filter(base_filter)
                .filter(~Match.id.in_(predicted_subq))
                .count()
            )
        else:
            remaining_now = 0
        return {
            'processed': 0, 'written': 0, 'skipped': 0,
            'remaining_estimate': remaining_now,
            'message': 'No matches to backfill in window',
            'since': since.isoformat(),
        }

    processed = 0
    written = 0
    skipped = 0
    started = time.time()

    for match in matches:
        try:
            features = compute_features(
                match.home_team, match.away_team, feature_engineer, model_data,
                competition=match.competition, match_date=match.date,
            )
            result = predict_match_result(features, model_data)
            probs = result.get('probabilities') or {}
            if not probs.get('home_win'):
                skipped += 1
                processed += 1
                continue

            predicted_outcome = result.get('outcome')
            predicted_winner = WINNER_MAP.get(predicted_outcome, predicted_outcome)
            correct = (predicted_winner == match.winner)

            existing = db.session.query(Prediction).filter_by(match_id=match.id).first()
            payload = dict(
                predicted_winner=predicted_winner,
                home_win_prob=probs['home_win'],
                draw_prob=probs['draw'],
                away_win_prob=probs['away_win'],
                confidence=result.get('confidence'),
                model_type=model_type,
                model_version=model_version,
                actual_winner=match.winner,
                correct=correct,
            )
            if existing:
                for k, v in payload.items():
                    if v is not None:
                        setattr(existing, k, v)
            else:
                db.session.add(Prediction(match_id=match.id, **payload))

            # Snapshot with as-of timestamp for honest historical record
            db.session.add(PredictionSnapshot(
                match_id=match.id,
                home_win_prob=probs['home_win'],
                draw_prob=probs['draw'],
                away_win_prob=probs['away_win'],
                confidence=result.get('confidence'),
                model_type=model_type,
                model_version=model_version,
                created_at=match.date,
            ))
            written += 1

            if written % commit_every == 0:
                db.session.commit()
            processed += 1
        except Exception as e:
            logger.warning("Skip match_id=%s: %s", match.id, e)
            skipped += 1
            processed += 1

    db.session.commit()
    elapsed = time.time() - started

    # Rough estimate of how many more matches need backfilling. Useful so a
    # cron / curl loop can know when to stop calling. Computed via a SQL
    # subquery so we never materialise the predicted-id set in Python — that
    # would scale O(predictions count) and OOM in catch-up runs.
    predicted_subq_after = db.session.query(Prediction.match_id).subquery()
    remaining = (
        db.session.query(Match)
        .filter(and_(
            Match.status == 'FINISHED',
            Match.date >= since,
            Match.winner.isnot(None),
            ~Match.id.in_(predicted_subq_after),
        ))
        .count()
    )

    return {
        'processed': processed,
        'written': written,
        'skipped': skipped,
        'elapsed_seconds': round(elapsed, 1),
        'remaining_estimate': remaining,
        'since': since.isoformat(),
        'limit': limit,
    }
