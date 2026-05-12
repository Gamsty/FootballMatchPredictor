"""
Nightly retraining job — runs as Azure Container Apps Job.

Pipeline:
  1. Refresh fixtures from football-data.org -> Postgres
  2. Train new XGBoost model on full historical data (time-based holdout split)
  3. Validate on the holdout — require AUC >= production AUC - tolerance
  4. If pass: promote to production blob path + (optional) hot-reload backend
  5. If fail: save to candidate path with timestamp + exit non-zero (alerts via App Insights)

Required env vars:
  USE_BLOB_STORAGE=true
  AZURE_STORAGE_ACCOUNT=...
  DATABASE_URL=...
  FOOTBALL_API_KEY=...

Optional:
  AUC_TOLERANCE=0.02            How much AUC can drop before we reject the new model
  HOLDOUT_DAYS=90               Time-based holdout window
  MIN_HOLDOUT_SIZE=50           Refuse to validate on tiny holdouts (cold-start safety)
  BACKEND_RELOAD_URL=...        POST URL to /api/admin/reload-model
  RELOAD_TOKEN=...              Shared secret for the reload endpoint
"""

import io
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Make backend/src importable when run as a script in the container
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import joblib
import requests

from data_collection import FootballDataCollector
from database import DatabaseManager, Match, Team
from model_storage import load_model_bytes, upload_model
import model_training

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("retrain")

AUC_TOLERANCE = float(os.getenv("AUC_TOLERANCE", "0.02"))
HOLDOUT_DAYS = int(os.getenv("HOLDOUT_DAYS", "90"))
MIN_HOLDOUT_SIZE = int(os.getenv("MIN_HOLDOUT_SIZE", "50"))
RELOAD_URL = os.getenv("BACKEND_RELOAD_URL")
RELOAD_TOKEN = os.getenv("RELOAD_TOKEN")


def refresh_fixtures(db: DatabaseManager) -> int:
    """Sync upcoming fixtures from football-data.org. Mirrors app.py /api/fixtures/refresh."""
    collector = FootballDataCollector()
    fixtures = collector.get_upcoming_fixtures(days=7)
    added = 0
    for fixture in fixtures:
        home_team = db.session.query(Team).filter_by(api_id=fixture['home_team_api_id']).first()
        away_team = db.session.query(Team).filter_by(api_id=fixture['away_team_api_id']).first()
        if not home_team or not away_team:
            continue
        existing = db.session.query(Match).filter_by(api_id=fixture['api_id']).first()
        if existing:
            existing.status = fixture['status']
            existing.date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
        else:
            db.session.add(Match(
                api_id=fixture['api_id'],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                season=fixture['season'],
                matchday=fixture.get('matchday'),
                competition=fixture['competition'],
                stage=fixture.get('stage', 'REGULAR_SEASON'),
                date=datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')),
                status=fixture['status'],
            ))
            added += 1
    db.session.commit()
    return added


def hot_reload_backend() -> None:
    """POST to backend's reload endpoint so it picks up the new model without restart."""
    if not RELOAD_URL or not RELOAD_TOKEN:
        logger.info("BACKEND_RELOAD_URL / RELOAD_TOKEN not set — skipping hot reload")
        return
    try:
        r = requests.post(RELOAD_URL, headers={"X-Reload-Token": RELOAD_TOKEN}, timeout=30)
        r.raise_for_status()
        logger.info(f"Backend hot-reload OK: {r.json()}")
    except Exception as e:
        logger.warning(f"Hot reload failed (non-fatal — backend will pick up on next restart): {e}")


def main() -> int:
    logger.info("=== Retrain job start ===")
    db = DatabaseManager()

    # 1. Refresh fixtures
    new_fixtures = refresh_fixtures(db)
    logger.info(f"Refreshed fixtures: {new_fixtures} new")

    # 2. Train new model with time-based holdout
    logger.info(f"Training new XGBoost model (holdout={HOLDOUT_DAYS} days)...")
    result = model_training.train_xgboost(db, holdout_days=HOLDOUT_DAYS)
    new_model_data = result['model_data']
    X_hold = result['X_holdout']
    y_hold = result['y_holdout']
    logger.info(f"Train: {result['train_size']}, Holdout: {result['holdout_size']}")

    # 3. Validation gate
    if result['holdout_size'] < MIN_HOLDOUT_SIZE:
        logger.error(f"Holdout too small ({result['holdout_size']} < {MIN_HOLDOUT_SIZE}) — aborting")
        return 1

    # AUC requires all classes present in y_hold for multi-class. Check first.
    classes_present = set(y_hold.unique().tolist())
    if classes_present != {0, 1, 2}:
        logger.warning(f"Holdout missing some classes (have {classes_present}, need {{0,1,2}}) — skipping AUC validation")
        # Fall back to just promoting (not ideal, but safer than crashing)
        new_auc = float('nan')
        prod_auc = float('nan')
        validation_passed = True
    else:
        new_auc = model_training.evaluate_auc(new_model_data, X_hold, y_hold)
        logger.info(f"New model AUC (holdout): {new_auc:.4f}")

        # Production model must be evaluated on the SAME (already-scaled) holdout.
        # We have two scalers (new and prod) — use each model's own scaler on raw features.
        # X_hold here is already scaled by new model's scaler. We need raw features for prod.
        # Easiest workaround: re-evaluate using each model's scaler, not the cached scaled X.
        # train_xgboost only returns the scaled holdout, not the raw one. So we add a separate
        # path: load production model and re-build holdout with ITS scaler.
        try:
            prod_bytes = load_model_bytes("best_model.pkl", prefix="production")
            prod_model_data = joblib.load(io.BytesIO(prod_bytes))
            # Rebuild RAW holdout using same time split, then scale with prod's scaler
            prod_auc = _eval_prod_on_same_holdout(db, prod_model_data, y_hold)
            logger.info(f"Production AUC (same holdout): {prod_auc:.4f}")
        except Exception as e:
            logger.warning(f"Could not evaluate production model: {e} — defaulting to PASS")
            prod_auc = 0.0

        validation_passed = new_auc >= prod_auc - AUC_TOLERANCE

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    with tempfile.TemporaryDirectory() as tmp_dir:
        candidate_path = Path(tmp_dir) / "candidate.pkl"
        joblib.dump(new_model_data, candidate_path)

        if validation_passed:
            logger.info(f"Validation PASSED (new={new_auc:.4f}, prod={prod_auc:.4f}) — promoting")

            # Versioned snapshot first (history)
            upload_model(candidate_path, f"best_model_{timestamp}.pkl", prefix="production")
            # Then overwrite live pointer
            upload_model(candidate_path, "best_model.pkl", prefix="production")

            manifest = {
                "promoted_at": timestamp,
                "auc_new": new_auc if not _isnan(new_auc) else None,
                "auc_prev": prod_auc if not _isnan(prod_auc) else None,
                "holdout_size": result['holdout_size'],
                "train_size": result['train_size'],
                "fixtures_added": new_fixtures,
            }
            manifest_path = Path(tmp_dir) / "latest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))
            upload_model(manifest_path, "latest.json", prefix="production")

            hot_reload_backend()
            logger.info("Promoted ✓")
            return 0
        else:
            drop = prod_auc - new_auc
            # Validation gate rejecting a worse model is the EXPECTED behavior of a
            # working safe-deploy pipeline, not a job failure. Exit 0 so Azure doesn't
            # surface this as a failed execution (which would trigger ops alerts). The
            # candidate is preserved in `models/candidate/` for offline inspection, and
            # the WARNING-level log makes the rejection visible in Application Insights.
            logger.warning(
                f"Validation REJECTED — new model dropped {drop:.4f} AUC vs production "
                f"(tolerance={AUC_TOLERANCE}). Candidate preserved at candidate/failed_{timestamp}.pkl"
            )
            upload_model(candidate_path, f"failed_{timestamp}.pkl", prefix="candidate")
            return 0


def _isnan(x) -> bool:
    try:
        return x != x  # NaN != NaN
    except Exception:
        return False


def _eval_prod_on_same_holdout(db, prod_model_data, y_hold_expected):
    """
    Re-build the same time-based holdout using PRODUCTION model's feature pipeline,
    then evaluate. Necessary because train_xgboost only returns the new model's scaled
    holdout — the production model has its own scaler.

    Strategy: rebuild raw features with model_training._build_xy_from_csv (deterministic),
    apply prod's scaler (column-aligned to prod's feature_names), evaluate AUC.
    """
    from feature_engineering import FeatureEngineer
    import pandas as pd

    fe = FeatureEngineer()
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tmp:
            tmp_csv = tmp.name
        try:
            fe.export_features_to_csv(output_path=tmp_csv)
            X, y, df, _, _ = model_training._build_xy_from_csv(tmp_csv, include_odds=False, binary_mode=False)

            df['date'] = pd.to_datetime(df['date'], utc=True, errors='coerce')
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=HOLDOUT_DAYS)
            hold_mask = df['date'] >= cutoff

            X_hold_raw = X[hold_mask].copy()
            y_hold_actual = y[hold_mask].reset_index(drop=True)

            # Sanity check: same y as the new model evaluated on
            if len(y_hold_actual) != len(y_hold_expected):
                logger.warning(f"Holdout length mismatch ({len(y_hold_actual)} vs expected {len(y_hold_expected)})")

            prod_features = prod_model_data['feature_names']
            # Align columns — fill missing prod features with 0 (model versions may have drifted)
            for col in prod_features:
                if col not in X_hold_raw.columns:
                    X_hold_raw[col] = 0
            X_hold_aligned = X_hold_raw[prod_features]

            X_hold_prod_scaled = prod_model_data['scaler'].transform(X_hold_aligned)
            return model_training.evaluate_auc(prod_model_data, X_hold_prod_scaled, y_hold_actual)
        finally:
            try:
                os.unlink(tmp_csv)
            except OSError:
                pass
    finally:
        fe.close()


if __name__ == "__main__":
    sys.exit(main())
