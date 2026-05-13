"""
Fit a TemperatureCalibrator against historical (raw prediction, outcome) pairs.

WHY THIS JOB EXISTS
-------------------
Calibration's chicken-and-egg problem: we want to fit T against raw model
output, but the predictions table stores whatever the model returned at the
time — which after T was fitted, will itself be calibrated. Fitting on
already-calibrated probs would just learn T=1.

Solution: this job re-runs the model in-process with `apply_calibration=False`
on every finished match, collects raw probabilities, and fits T against the
known outcomes. The fitted T is then saved to blob storage as `calibrator.json`
(or local `backend/models/calibrator.json` in dev) and picked up at the next
app startup.

USAGE
-----
    cd backend
    venv/Scripts/python.exe jobs/fit_calibration.py
    venv/Scripts/python.exe jobs/fit_calibration.py --since 2024-01-01 --max 5000
    venv/Scripts/python.exe jobs/fit_calibration.py --no-upload  # local-only

The job is idempotent — calling it multiple times overwrites the previous T.
Run it after a model retrain so the new model's calibration is freshly fitted.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import joblib
import numpy as np
from sqlalchemy import and_

from calibrator import TemperatureCalibrator, expected_calibration_error
from database import DatabaseManager, Match
from feature_engineering import FeatureEngineer
from model_storage import load_model_bytes, upload_model
from prediction_service import compute_features, predict_match_result

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fit_calibration")


# AWAY_WIN=0, DRAW=1, HOME_WIN=2 — same order as the trained model's classes
LABEL_MAP = {'AWAY_TEAM': 0, 'DRAW': 1, 'HOME_TEAM': 2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', type=str, default=None,
                        help='Only fit on matches on/after this date (YYYY-MM-DD). '
                             'Default: 365 days ago.')
    parser.add_argument('--max', type=int, default=5000,
                        help='Cap on matches sampled for the fit (default 5000). '
                             'Larger samples reduce fit variance but take longer.')
    parser.add_argument('--no-upload', action='store_true',
                        help='Write only to backend/models/calibrator.json, skip blob upload.')
    args = parser.parse_args()

    if args.since:
        since = datetime.strptime(args.since, '%Y-%m-%d')
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=365)).replace(tzinfo=None)

    logger.info("Fitting on matches since %s (max %d)", since.date(), args.max)

    # Load model — same path as the live app does
    logger.info("Loading model...")
    model_bytes = load_model_bytes("best_model.pkl")
    model_data = joblib.load(io.BytesIO(model_bytes))
    # Critical: NO calibrator attached. predict_match_result(apply_calibration=False)
    # would also work, but being explicit is safer.
    model_data.pop('calibrator', None)

    db = DatabaseManager()
    feature_engineer = FeatureEngineer()

    # Pull finished matches with known winners
    matches = (
        db.session.query(Match)
        .filter(and_(
            Match.status == 'FINISHED',
            Match.date >= since,
            Match.winner.in_(LABEL_MAP.keys()),
        ))
        .order_by(Match.date.asc())
        .limit(args.max)
        .all()
    )
    logger.info("Found %d candidate matches", len(matches))
    if len(matches) < 100:
        logger.error("Not enough data — need ≥100 evaluated matches for a stable fit")
        return 1

    # Predict in-process with raw output
    probs: list[list[float]] = []
    labels: list[int] = []
    skipped = 0
    for i, match in enumerate(matches):
        try:
            features = compute_features(
                match.home_team, match.away_team, feature_engineer, model_data,
                competition=match.competition, match_date=match.date,
            )
            res = predict_match_result(features, model_data, apply_calibration=False)
            p = res.get('raw_probabilities') or res.get('probabilities') or {}
            row = [p.get('away_win'), p.get('draw'), p.get('home_win')]
            if any(v is None for v in row):
                skipped += 1
                continue
            probs.append(row)
            labels.append(LABEL_MAP[match.winner])
            if (i + 1) % 500 == 0:
                logger.info("Predicted %d/%d", i + 1, len(matches))
        except Exception as e:
            logger.warning("Skip match_id=%s: %s", match.id, e)
            skipped += 1

    feature_engineer.close()

    probs_arr = np.array(probs, dtype=float)
    labels_arr = np.array(labels, dtype=int)
    logger.info("Fitting temperature on %d samples (skipped %d)", len(probs_arr), skipped)

    # Fit
    cal = TemperatureCalibrator().fit(probs_arr, labels_arr)

    # Diagnostics: ECE before and after
    ece_before = expected_calibration_error(probs_arr, labels_arr, bins=10)
    ece_after = expected_calibration_error(cal.transform(probs_arr), labels_arr, bins=10)
    nll_reduction = (cal.fit_nll_before - cal.fit_nll_after) / cal.fit_nll_before
    accuracy = (probs_arr.argmax(axis=1) == labels_arr).mean()

    logger.info("=" * 60)
    logger.info("FIT RESULTS")
    logger.info("=" * 60)
    logger.info("Optimal temperature: %.4f", cal.temperature)
    if cal.temperature < 0.95:
        logger.info("  (T < 1 → model was under-dispersed; rescaled probs are sharper)")
    elif cal.temperature > 1.05:
        logger.info("  (T > 1 → model was over-confident; rescaled probs are softer)")
    else:
        logger.info("  (T ≈ 1 → model was already well-calibrated)")
    logger.info("ECE before: %.4f → after: %.4f (Δ %+.4f)",
                ece_before, ece_after, ece_after - ece_before)
    logger.info("NLL  before: %.2f → after: %.2f  (improvement: %.2f%%)",
                cal.fit_nll_before, cal.fit_nll_after, nll_reduction * 100)
    logger.info("Argmax accuracy (unchanged by T scaling): %.4f", accuracy)
    logger.info("=" * 60)

    # Persist
    out_path = Path(__file__).parent.parent / 'models' / 'calibrator.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cal.save(out_path)
    logger.info("Saved locally to %s", out_path)

    if not args.no_upload and os.getenv('USE_BLOB_STORAGE', '').lower() == 'true':
        upload_model(out_path, 'calibrator.json')
        logger.info("Uploaded to blob storage")
    else:
        logger.info("Skipping blob upload (USE_BLOB_STORAGE != true, or --no-upload set)")

    return 0


if __name__ == '__main__':
    sys.exit(main())
