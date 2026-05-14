"""
Backfill predictions for historical (FINISHED) matches.

WHY THIS EXISTS
---------------
The calibration endpoint (/api/predictions/calibration) needs (prediction, outcome)
pairs to compute ECE / Brier / a calibration curve. We persist predictions for
upcoming matches automatically (in /api/predictions/upcoming and /api/value-bets),
but historical matches in the DB don't have predictions yet — `predictions` is
near-empty while `matches.status='FINISHED'` is in the tens of thousands.

This script fills that gap: for every FINISHED match without a prediction,
recompute features as-of the match date, run the loaded model, store the result
with `actual_winner` populated from the actual match outcome.

LEAKAGE CAVEAT
--------------
Features per match (form, H2H, goals avg, etc.) are computed via FeatureEngineer
which uses match_date as an as-of cutoff — so those are point-in-time correct.

HOWEVER: model_data['elo_ratings'] is a snapshot frozen at training time. If a
historical match was in the training set, the Elo values implicitly encode its
outcome — that's a mild form of leakage for that specific match.

For honest backtest accuracy you'd want walk-forward retraining (retrain the
model up to match_date − 1, then predict). Out of scope here; this script is
specifically for getting *some* calibration data flowing so we can see the
shape of model error. Out-of-sample matches (those after the training cutoff)
are unaffected.

USAGE
-----
    cd backend
    venv/Scripts/python.exe jobs/backfill_predictions.py
    venv/Scripts/python.exe jobs/backfill_predictions.py --since 2024-08-01 --limit 5000
    venv/Scripts/python.exe jobs/backfill_predictions.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import io
import joblib
from sqlalchemy import and_

from database import DatabaseManager, Match, Prediction, PredictionSnapshot
from feature_engineering import FeatureEngineer
from model_storage import load_model_bytes
from prediction_service import compute_features, predict_match_result

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")


WINNER_MAP = {
    'HOME_WIN': 'HOME_TEAM',
    'AWAY_WIN': 'AWAY_TEAM',
    'DRAW':     'DRAW',
}


def load_model() -> dict:
    logger.info("Loading model...")
    try:
        model_bytes = load_model_bytes("best_model.pkl")
        model_data = joblib.load(io.BytesIO(model_bytes))
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        raise
    logger.info("Model loaded: type=%s, features=%d",
                model_data.get('model_type'), len(model_data.get('feature_names', [])))
    return model_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', type=str, default=None,
                        help='Only backfill matches on/after this date (YYYY-MM-DD). '
                             'Default: 365 days ago. Use to limit leakage scope.')
    parser.add_argument('--limit', type=int, default=2000,
                        help='Max matches to process per run (default 2000)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Compute predictions but do not write to DB')
    parser.add_argument('--overwrite', action='store_true',
                        help='Re-predict matches that already have a prediction. '
                             'Default: skip them (idempotent).')
    parser.add_argument('--no-calibration', action='store_true',
                        help='Skip the calibrator even if present. Useful for '
                             'reproducing the raw model output, e.g. to A/B '
                             'compare calibrated vs uncalibrated.')
    parser.add_argument('--pit-elo', action='store_true',
                        help='Use point-in-time Elo ratings (recomputed from match '
                             'history up to each match.date) instead of the frozen '
                             "model_data['elo_ratings'] snapshot. Use this to test "
                             'whether ROI gains are real or due to Elo leakage.')
    args = parser.parse_args()

    # Default cutoff: last 365 days. This trades coverage for leakage safety —
    # a model trained 6 months ago saw most matches >6 months old.
    if args.since:
        since = datetime.strptime(args.since, '%Y-%m-%d')
    else:
        from datetime import timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=365)).replace(tzinfo=None)

    logger.info("Backfill window: from %s, limit %d, dry_run=%s, overwrite=%s",
                since.date(), args.limit, args.dry_run, args.overwrite)

    model_data = load_model()

    # Optionally attach the calibrator so persisted probs are calibrated.
    # When --no-calibration is set we deliberately strip it — useful for
    # producing the raw-model baseline that fit_calibration trains against.
    if args.no_calibration:
        model_data.pop('calibrator', None)
        logger.info("Calibration disabled — using raw model output")
    else:
        try:
            cal_path = Path(__file__).parent.parent / 'models' / 'calibrator.json'
            if cal_path.exists():
                from calibrator import TemperatureCalibrator
                cal = TemperatureCalibrator.load(cal_path)
                model_data['calibrator'] = cal
                logger.info("Calibration enabled (T=%.4f)", cal.temperature)
            else:
                logger.info("No calibrator found at %s — using raw output", cal_path)
        except Exception as e:
            logger.warning("Failed to load calibrator (using raw): %s", e)

    model_version = str(model_data.get('model_version') or 'unversioned')
    model_type = model_data.get('model_type')

    db = DatabaseManager()
    feature_engineer = FeatureEngineer()

    # When --pit-elo is set, precompute the full Elo trajectory across DB history.
    # We'll override model_data['elo_ratings'] per-match below so compute_features
    # sees a point-in-time-correct Elo instead of the frozen training snapshot.
    elo_history = None
    original_elo_snapshot = model_data.get('elo_ratings')
    if args.pit_elo:
        from feature_engineering import compute_elo_history
        logger.info("Pre-computing Elo history from match log (this may take a few seconds)...")
        elo_history = compute_elo_history(db)
        logger.info("Elo history computed for %d teams", len(elo_history))

    # Find finished matches in the window, ordered oldest-first so progress
    # is human-readable. Skip those already predicted unless --overwrite.
    matches_q = (
        db.session.query(Match)
        .filter(and_(
            Match.status == 'FINISHED',
            Match.date >= since,
            Match.winner.isnot(None),
        ))
        .order_by(Match.date.asc())
    )

    if not args.overwrite:
        predicted_ids = {p.match_id for p in db.session.query(Prediction.match_id).all()}
        matches = [m for m in matches_q.limit(args.limit * 2) if m.id not in predicted_ids][:args.limit]
    else:
        matches = matches_q.limit(args.limit).all()

    total = len(matches)
    logger.info("Found %d matches to backfill", total)
    if total == 0:
        return 0

    processed = 0
    written = 0
    skipped = 0
    started = time.time()

    for match in matches:
        try:
            # If point-in-time Elo is enabled, swap the model's frozen ratings
            # for this match's pre-kickoff values BEFORE building features.
            # We only set the two teams that matter here — compute_features
            # only looks up these two names.
            if elo_history is not None:
                from feature_engineering import get_elo_at
                pit_home = get_elo_at(match.home_team.name, match.date, elo_history)
                pit_away = get_elo_at(match.away_team.name, match.date, elo_history)
                model_data['elo_ratings'] = {
                    match.home_team.name: pit_home,
                    match.away_team.name: pit_away,
                }

            features = compute_features(
                match.home_team, match.away_team, feature_engineer, model_data,
                competition=match.competition, match_date=match.date,
            )
            result = predict_match_result(features, model_data)
            probs = result.get('probabilities') or {}
            if not probs.get('home_win'):
                skipped += 1
                continue

            predicted_outcome = result.get('outcome')
            predicted_winner = WINNER_MAP.get(predicted_outcome, predicted_outcome)
            correct = (predicted_winner == match.winner)

            if not args.dry_run:
                existing = db.session.query(Prediction).filter_by(match_id=match.id).first()
                payload = dict(
                    predicted_winner=predicted_winner,
                    home_win_prob=probs['home_win'],
                    draw_prob=probs['draw'],
                    away_win_prob=probs['away_win'],
                    confidence=result.get('confidence'),
                    model_type=model_type,
                    model_version=model_version,
                    # Critical: backfill knows the actual outcome — fill it
                    actual_winner=match.winner,
                    correct=correct,
                )
                if existing:
                    for k, v in payload.items():
                        if v is not None:
                            setattr(existing, k, v)
                else:
                    db.session.add(Prediction(match_id=match.id, **payload))

                # Snapshot too — created_at defaults to now, but for historical
                # backtest you'd want it as-of match_date. Override explicitly.
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

                # Commit in batches of 50 so progress is durable and we don't
                # blow memory on long runs.
                if written % 50 == 0:
                    db.session.commit()
                    elapsed = time.time() - started
                    rate = written / elapsed if elapsed else 0
                    eta = (total - processed) / rate if rate else 0
                    logger.info("Progress: %d/%d (%.1f/sec, ETA %.0fs)",
                                processed + 1, total, rate, eta)
            processed += 1
        except Exception as e:
            logger.warning("Skip match_id=%s: %s", match.id, e)
            skipped += 1

    if not args.dry_run:
        db.session.commit()

    # Restore the original frozen Elo snapshot so subsequent in-process callers
    # (if this module is imported elsewhere) see the same model state as before.
    if args.pit_elo and original_elo_snapshot is not None:
        model_data['elo_ratings'] = original_elo_snapshot

    feature_engineer.close()
    elapsed = time.time() - started
    logger.info("=" * 60)
    logger.info("DONE: processed=%d, written=%d, skipped=%d, elapsed=%.1fs",
                processed, written, skipped, elapsed)
    if args.dry_run:
        logger.info("(dry-run — no writes performed)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
