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

    matches_q = (
        db.session.query(Match)
        .filter(and_(
            Match.status == 'FINISHED',
            Match.date >= since,
            Match.winner.isnot(None),
        ))
        .order_by(Match.date.asc())
    )

    if not overwrite:
        # Skip matches that already have a prediction. We over-fetch (×2) and
        # then trim to `limit` to make sure we land on `limit` new predictions
        # when most candidates have already been done — avoids progress
        # stalls during long catch-up runs.
        predicted_ids = {p.match_id for p in db.session.query(Prediction.match_id).all()}
        matches = [m for m in matches_q.limit(limit * 2)
                   if m.id not in predicted_ids][:limit]
    else:
        matches = matches_q.limit(limit).all()

    total = len(matches)
    if total == 0:
        return {
            'processed': 0, 'written': 0, 'skipped': 0,
            'remaining_estimate': 0,
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
    # cron / curl loop can know when to stop calling. Computed by counting
    # un-predicted FINISHED matches in the window after this batch.
    predicted_ids_after = {p.match_id for p in db.session.query(Prediction.match_id).all()}
    remaining = (
        db.session.query(Match)
        .filter(and_(
            Match.status == 'FINISHED',
            Match.date >= since,
            Match.winner.isnot(None),
            ~Match.id.in_(predicted_ids_after),
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
