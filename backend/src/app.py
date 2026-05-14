"""
Flask API for Football Match Predictor

Endpoints:
    - /api/health                  — API health check and model status
    - /api/teams                   — List all teams
    - /api/teams/<id>              — Team details with statistics and recent form
    - /api/predict                 — Predict match outcome (H/D/A)
    - /api/predict/markets         — Full multi-market prediction
    - /api/predictions/history     — Past predictions with accuracy stats
    - /api/predictions/upcoming    — Batch predictions for upcoming matches (dashboard)
    - /api/predictions/calibration — Bucket predictions vs actual outcomes (model calibration)
    - /api/value-bets              — +EV picks: model probabilities vs bookmaker odds
    - /api/admin/odds-status       — Odds-API quota + per-bookmaker divergence stats
    - /api/admin/refit-calibration — Re-fit temperature scaling on historical data
    - /api/bets                    — List/create paper bets
    - /api/bets/<id>               — Detail / delete a bet
    - /api/bets/performance        — Aggregate ROI, win rate, CLV across all bets
    - /api/bets/settle             — Settle all pending bets against finished matches
    - /api/matches                 — List matches with optional filters
    - /api/matches/<id>            — Single match detail
    - /api/matches/upcoming        — Raw upcoming matches with basic predictions
    - /api/competitions            — List of competitions in database
    - /api/fixtures/refresh        — Fetch new fixtures from football-data.org
    - /api/statistics/overview     — Overall match and goal statistics
    - /api/statistics/head-to-head — Head-to-head record between two teams
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timezone
import joblib
from dotenv import load_dotenv
import hmac
import io
import logging
import os
import re

from database import (
    DatabaseManager, Match, Team, Prediction, PredictionSnapshot, MatchFeatures,
    Bet, init_db,
)
from feature_engineering import FeatureEngineer
from prediction_service import (
    compute_features, predict_match_result, predict_all_markets, classify_match
)
from cache import PredictionCache
from calibrator import TemperatureCalibrator
from pathlib import Path
from model_storage import load_model_bytes
from odds_api import OddsAPIClient
from telemetry import setup_telemetry
from value_bets import value_picks
from sqlalchemy import and_, desc, distinct

# Load environment variables. Only read a local .env file in development; in
# production (Azure Container Apps), env vars come from Key Vault references and
# allowing .env to override them would be a footgun if one ever slipped into an image.
if os.getenv("FLASK_ENV", "development") != "production":
    load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Reject request bodies over 256 KB. Our largest legitimate request is a single
# /api/predict body (~200 bytes), so this is a generous ceiling against
# memory-exhaustion DoS from unbounded JSON.
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024

# Wire up Azure Monitor (no-op when APPLICATIONINSIGHTS_CONNECTION_STRING is unset)
setup_telemetry(app)
logger = logging.getLogger("fotballpred")
logger.setLevel(logging.INFO)

FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')

# Allowed CORS origins. Vercel preview deploys get matched via regex.
ALLOWED_ORIGINS = [
    FRONTEND_URL,
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://localhost:8080",
    "https://football-match-predictor-pearl.vercel.app",
]
# Matches any Vercel deploy URL for this project: production alias (-pearl), per-commit
# (-<sha>-gamstys-projects), branch previews. Stays scoped to football-match-predictor-*.
VERCEL_PREVIEW_REGEX = re.compile(
    r"^https://football-match-predictor-[a-z0-9-]+\.vercel\.app$"
)

CORS(app, resources={
    r"/api/*": {
        "origins": ALLOWED_ORIGINS + [VERCEL_PREVIEW_REGEX],
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "allow_headers": ["Content-Type"]
    }
})

# Models load via model_storage abstraction:
# - prod (USE_BLOB_STORAGE=true): from Azure Blob via Managed Identity
# - dev: from backend/models/ on local disk
model_data = None
multi_market_models = None

def load_model():
    """Load ML models at startup (blob in prod, local in dev)."""
    global model_data, multi_market_models
    try:
        model_bytes = load_model_bytes("best_model.pkl")
        model_data = joblib.load(io.BytesIO(model_bytes))
        logger.info(
            "Main model loaded",
            extra={
                "model_type": model_data.get("model_type"),
                "feature_count": len(model_data.get("feature_names", [])),
            },
        )
    except Exception as e:
        logger.error(f"Error loading main model: {e}")
        model_data = None

    try:
        mm_bytes = load_model_bytes("multi_market_models.pkl")
        multi_market_models = joblib.load(io.BytesIO(mm_bytes))
        logger.info(
            "Multi-market models loaded",
            extra={"markets": list(multi_market_models.keys())},
        )
    except Exception as e:
        logger.warning(f"Multi-market models not found (optional): {e}")
        multi_market_models = None

    # Load optional calibrator. Two kinds supported:
    #   - calibrator.json (TemperatureCalibrator — preferred for production)
    #   - calibrator_isotonic.pkl (IsotonicCalibrator — more flexible, less
    #     stable across retrains, may not preserve argmax)
    # If both exist, isotonic wins (newer/explicitly-chosen). Absence is fine
    # — predictions stay uncalibrated.
    if model_data:
        model_data['calibrator'] = None
        # Try isotonic first
        try:
            from calibrator import IsotonicCalibrator
            iso_bytes = load_model_bytes("calibrator_isotonic.pkl")
            iso_path = Path(__file__).parent.parent / 'models' / 'calibrator_isotonic.pkl'
            iso_path.parent.mkdir(parents=True, exist_ok=True)
            iso_path.write_bytes(iso_bytes)
            cal = IsotonicCalibrator.load(iso_path)
            model_data['calibrator'] = cal
            logger.info(
                "IsotonicCalibrator loaded",
                extra={"fit_samples": cal.fit_samples, "classes": len(cal.models)},
            )
        except Exception:
            # Fall through to temperature
            try:
                cal_bytes = load_model_bytes("calibrator.json")
                import json as _json
                cal = TemperatureCalibrator.from_dict(_json.loads(cal_bytes.decode('utf-8')))
                model_data['calibrator'] = cal
                logger.info(
                    "TemperatureCalibrator loaded",
                    extra={"temperature": cal.temperature, "fit_samples": cal.fit_samples},
                )
            except Exception as e:
                logger.info(f"No calibrator available — predictions stay uncalibrated: {e}")

# Load models on startup
load_model()

# Ensure schema exists (idempotent — no-op if tables already present).
# Critical for fresh Postgres containers / first deploys to Azure Flexible Server.
try:
    init_db()
except Exception as e:
    logger.error(f"init_db failed (continuing — will retry on first query): {e}")

# Database manager
db = DatabaseManager()
feature_engineer = FeatureEngineer()

# Prediction cache (2 hour TTL)
prediction_cache = PredictionCache(default_ttl=7200)

# Bookmaker odds client — no-op when ODDS_API_KEY is unset.
# Used by /api/value-bets to compute edge = model_prob × decimal_odds − 1.
odds_client = OddsAPIClient()


def warm_cache():
    """Precompute predictions for upcoming matches so the first page load is fast."""
    if not model_data:
        return
    try:
        matches = db.get_upcoming_matches(days=3)
        count = 0
        for match in matches:
            try:
                date_str = match.date.strftime('%Y-%m-%d') if match.date else None
                if prediction_cache.get(match.home_team_id, match.away_team_id, date_str):
                    continue
                features = compute_features(
                    match.home_team, match.away_team, feature_engineer, model_data,
                    competition=match.competition, match_date=match.date
                )
                prediction_data = predict_all_markets(features, model_data, multi_market_models)
                prediction_cache.set(match.home_team_id, match.away_team_id, prediction_data, date_str)
                count += 1
            except Exception as e:
                logger.warning("Cache warm skip match_id=%s: %s", match.id, e)
        logger.info("Cache warmed: %d matches precomputed", count)
    except Exception:
        logger.exception("Cache warm error")

# Warm cache at startup
warm_cache()

def team_dict(team):
    """Build a team dict with crest URL derived from api_id."""
    return {
        'id': team.id,
        'name': team.name,
        'short_name': team.short_name,
        'crest': f'https://crests.football-data.org/{team.api_id}.png',
    }


def iso_utc(dt):
    """
    Serialize a naive datetime as an ISO 8601 string with explicit UTC tz.

    Our DateTime columns store naive UTC (see database._utcnow_naive) — calling
    plain `.isoformat()` produces e.g. '2026-05-18T19:00:00' which JS Date
    parses as LOCAL TIME, silently shifting every displayed kickoff by the
    user's tz offset. Appending '+00:00' makes JS interpret it as UTC and
    convert to local for display, which is what we want.

    Use this everywhere a match.date or other naive-UTC datetime is going
    out over the wire.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt.tzinfo is None else dt.isoformat()

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Remove thread-local session after each request to prevent stale connections."""
    db.close()

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Check API health and model status"""
    return jsonify({
        'status': 'healthy',
        'model_loaded': model_data is not None,
        'multi_market_loaded': multi_market_models is not None,
        'model_type': model_data['model_type'] if model_data else None,
        'cache_size': prediction_cache.size,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }), 200


def _error_response(message: str, status: int, exc: Exception | None = None, *, endpoint: str | None = None):
    """
    Return a sanitized JSON error to the client while logging the full exception
    (and traceback) server-side. Avoids leaking SQL errors, file paths, stack frames,
    or DB schema names through the response body.
    """
    if exc is not None:
        logger.exception("Unhandled error in %s: %s", endpoint or "endpoint", exc)
    else:
        logger.warning("Error in %s: %s", endpoint or "endpoint", message)
    return jsonify({"error": message}), status


def _require_admin_token() -> bool:
    """
    Constant-time shared-secret check for admin endpoints. Returns True iff the
    request carries a valid X-Reload-Token matching the RELOAD_TOKEN env var.
    Used by both /api/admin/reload-model and /api/fixtures/refresh.
    """
    expected_token = os.getenv("RELOAD_TOKEN")
    provided_token = request.headers.get("X-Reload-Token", "")
    if not expected_token:
        return False
    # hmac.compare_digest is constant-time — avoids leaking token length/prefix
    # via response-time analysis.
    return hmac.compare_digest(expected_token, provided_token)


@app.route('/api/admin/reload-model', methods=['POST'])
def reload_model():
    """
    Hot-reload the ML model from blob storage without restarting the container.
    Called by the retraining job after a successful validation+promotion.

    Side effects:
      - Reloads model weights from blob
      - Clears prediction cache (probs will differ under new weights)
      - Clears odds cache (stale prices shouldn't shadow fresh model probs;
        the next /api/value-bets call will refetch — costs one extra API credit
        per league but keeps probabilities and odds time-aligned)

    Auth: shared secret in X-Reload-Token header.
    """
    if not _require_admin_token():
        return jsonify({"error": "Unauthorized"}), 401
    load_model()
    prediction_cache.clear()
    odds_dropped = odds_client.clear_cache()
    return jsonify({
        "reloaded": True,
        "model_type": model_data["model_type"] if model_data else None,
        "odds_cache_entries_dropped": odds_dropped,
    }), 200


@app.route('/api/admin/refit-calibration', methods=['POST'])
def refit_calibration():
    """
    Re-fit the temperature calibrator using raw model output on finished matches.

    Body (optional):
        { "since": "2024-08-01", "max": 5000, "upload": true }

    Side effects:
      - Writes backend/models/calibrator.json
      - Uploads to blob storage if `upload=true` AND USE_BLOB_STORAGE=true
      - Hot-attaches the new calibrator to the running model_data (no restart)

    Auth: X-Reload-Token header (same as model reload).
    """
    if not _require_admin_token():
        return jsonify({"error": "Unauthorized"}), 401
    if not model_data:
        return jsonify({"error": "Model not loaded"}), 503

    try:
        import numpy as np
        from datetime import timedelta

        body = request.get_json(silent=True) or {}
        max_n = max(100, min(int(body.get('max', 5000)), 20000))
        if body.get('since'):
            since = datetime.strptime(body['since'], '%Y-%m-%d')
        else:
            since = (datetime.now(timezone.utc) - timedelta(days=365)).replace(tzinfo=None)
        upload = bool(body.get('upload', True))

        # Pull finished matches with known winners
        LABEL_MAP = {'AWAY_TEAM': 0, 'DRAW': 1, 'HOME_TEAM': 2}
        matches = (
            db.session.query(Match)
            .filter(and_(
                Match.status == 'FINISHED',
                Match.date >= since,
                Match.winner.in_(LABEL_MAP.keys()),
            ))
            .order_by(Match.date.asc())
            .limit(max_n)
            .all()
        )
        if len(matches) < 100:
            return jsonify({
                "error": f"Not enough evaluated data ({len(matches)} matches, need ≥100)",
            }), 400

        # Predict with apply_calibration=False to get raw probs
        probs: list[list[float]] = []
        labels: list[int] = []
        for m in matches:
            try:
                features = compute_features(
                    m.home_team, m.away_team, feature_engineer, model_data,
                    competition=m.competition, match_date=m.date,
                )
                res = predict_match_result(features, model_data, apply_calibration=False)
                p = res.get('raw_probabilities') or res.get('probabilities') or {}
                row = [p.get('away_win'), p.get('draw'), p.get('home_win')]
                if any(v is None for v in row):
                    continue
                probs.append(row)
                labels.append(LABEL_MAP[m.winner])
            except Exception:
                continue

        if len(probs) < 100:
            return jsonify({"error": f"Only {len(probs)} valid samples after feature computation"}), 400

        from calibrator import expected_calibration_error
        probs_arr = np.array(probs, dtype=float)
        labels_arr = np.array(labels, dtype=int)
        ece_before = expected_calibration_error(probs_arr, labels_arr, bins=10)

        cal = TemperatureCalibrator().fit(probs_arr, labels_arr)
        ece_after = expected_calibration_error(cal.transform(probs_arr), labels_arr, bins=10)

        # Persist locally
        from pathlib import Path
        local_path = Path(__file__).parent.parent / 'models' / 'calibrator.json'
        local_path.parent.mkdir(parents=True, exist_ok=True)
        cal.save(local_path)

        # Optionally upload to blob
        uploaded = False
        if upload and os.getenv('USE_BLOB_STORAGE', '').lower() == 'true':
            from model_storage import upload_model
            upload_model(local_path, 'calibrator.json')
            uploaded = True

        # Hot-attach so subsequent predictions use it immediately, no restart needed
        model_data['calibrator'] = cal
        prediction_cache.clear()  # discard any cached uncalibrated predictions

        return jsonify({
            "fitted": True,
            "temperature": round(cal.temperature, 4),
            "samples": len(probs_arr),
            "ece_before": round(ece_before, 4),
            "ece_after": round(ece_after, 4),
            "nll_before": round(cal.fit_nll_before, 4),
            "nll_after": round(cal.fit_nll_after, 4),
            "uploaded_to_blob": uploaded,
        }), 200

    except Exception as e:
        return _error_response("Calibration fit failed", 500, e, endpoint="refit_calibration")


@app.route('/api/admin/odds-status', methods=['GET'])
def odds_status():
    """
    Operational health for the Odds API integration.

    Returns:
      - enabled: bool — whether ODDS_API_KEY is configured
      - quota: { remaining, used, low } — from the last response headers
      - divergence_stats: per-bookmaker mean |log(price/median)| across the
        most recent cached events. Used to validate TRUSTED_BOOKMAKERS empirically:
        bookmakers with consistently high divergence (≥ 0.15) are likely
        outlier-posters or palp-prone and should be reviewed.

    Auth: shared secret (same X-Reload-Token as model reload). Quota info
    isn't sensitive in itself but the divergence stats reveal which books
    we're using, so we gate the whole thing.
    """
    if not _require_admin_token():
        return jsonify({"error": "Unauthorized"}), 401
    if not odds_client.enabled:
        return jsonify({"enabled": False, "message": "ODDS_API_KEY not set"}), 200

    # Sample divergence stats from any league with cached data
    stats: dict = {}
    for comp_name, sport_key in [
        ('Premier League', 'soccer_epl'),
        ('La Liga', 'soccer_spain_la_liga'),
        ('Bundesliga', 'soccer_germany_bundesliga'),
    ]:
        s = odds_client.bookmaker_divergence_stats(sport_key)
        if s:
            stats[comp_name] = s

    return jsonify({
        "enabled": True,
        "quota": odds_client.quota_status(),
        "divergence_stats": stats,
    }), 200


def _persist_prediction(match, prediction_data: dict) -> None:
    """
    Persist a prediction in two places:
      1. `predictions` — upserts latest state (one row per match, used by calibration)
      2. `prediction_snapshots` — appends an immutable history row (used by CLV
         and version comparisons)

    Best-effort: errors are logged but never raised — a DB blip must not
    take down a read endpoint.
    """
    if not model_data or not match.id or not prediction_data:
        return
    try:
        result = prediction_data.get('match_result') or {}
        probs = result.get('probabilities') or {}
        if not probs.get('home_win'):
            return  # nothing useful to persist
        winner_map = {
            'HOME_WIN': 'HOME_TEAM',
            'AWAY_WIN': 'AWAY_TEAM',
            'DRAW':     'DRAW',
        }
        model_version = str(model_data.get('model_version') or 'unversioned')
        model_type = model_data.get('model_type')

        # 1. Upsert the canonical Prediction row
        existing = db.session.query(Prediction).filter_by(match_id=match.id).first()
        payload = dict(
            predicted_winner=winner_map.get(result.get('outcome'), result.get('outcome')),
            home_win_prob=probs.get('home_win'),
            draw_prob=probs.get('draw'),
            away_win_prob=probs.get('away_win'),
            confidence=result.get('confidence'),
            model_type=model_type,
            model_version=model_version,
        )
        if existing:
            for k, v in payload.items():
                if v is not None:
                    setattr(existing, k, v)
        else:
            db.session.add(Prediction(match_id=match.id, **payload))

        # 2. Append an immutable snapshot. We dedupe by (match_id, model_version):
        # if a snapshot already exists for THIS match under THIS model version
        # with identical probabilities, skip (avoids spam on cache-warming).
        latest_snap = (
            db.session.query(PredictionSnapshot)
            .filter_by(match_id=match.id, model_version=model_version)
            .order_by(PredictionSnapshot.created_at.desc())
            .first()
        )
        should_append = True
        if latest_snap:
            tol = 1e-4
            same = (
                abs((latest_snap.home_win_prob or 0) - (probs.get('home_win') or 0)) < tol
                and abs((latest_snap.draw_prob or 0) - (probs.get('draw') or 0)) < tol
                and abs((latest_snap.away_win_prob or 0) - (probs.get('away_win') or 0)) < tol
            )
            if same:
                should_append = False

        if should_append:
            db.session.add(PredictionSnapshot(
                match_id=match.id,
                home_win_prob=probs.get('home_win'),
                draw_prob=probs.get('draw'),
                away_win_prob=probs.get('away_win'),
                confidence=result.get('confidence'),
                model_type=model_type,
                model_version=model_version,
            ))

        db.session.commit()
    except Exception as e:
        logger.warning("Persist prediction failed match_id=%s: %s", match.id, e)
        db.session.rollback()

# ============================================================================
# TEAMS
# ============================================================================

@app.route('/api/teams', methods=['GET'])
def get_teams():
    """Get list of all teams"""
    try:
        teams = db.session.query(Team).order_by(Team.name).all()

        teams_list = [{
            'id': team.id,
            'name': team.name,
            'short_name': team.short_name,
            'competition': team.competition
        } for team in teams]

        return jsonify(teams_list), 200

    except Exception as e:
        return _error_response("Failed to load teams", 500, e, endpoint="get_teams")

@app.route('/api/teams/<int:team_id>', methods=['GET'])
def get_team(team_id):
    """Get team details and statistics"""
    try:
        team = db.session.query(Team).filter_by(id=team_id).first()

        if not team:
            return jsonify({'error': 'Team not found'}), 404

        # Get team's matches
        matches = db.session.query(Match).filter(
            and_(
                (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
                Match.status == 'FINISHED'
            )
        ).order_by(desc(Match.date)).all()

        # Calculate statistics
        total_matches = len(matches)
        wins = 0
        draws = 0
        losses = 0
        goals_scored = 0
        goals_conceded = 0

        for match in matches:
            if match.home_team_id == team_id:
                goals_scored += match.home_score or 0
                goals_conceded += match.away_score or 0
                if match.winner == 'HOME_TEAM':
                    wins += 1
                elif match.winner == 'DRAW':
                    draws += 1
                else:
                    losses += 1
            else:
                goals_scored += match.away_score or 0
                goals_conceded += match.home_score or 0
                if match.winner == 'AWAY_TEAM':
                    wins += 1
                elif match.winner == 'DRAW':
                    draws += 1
                else:
                    losses += 1

        # Recent form (last 5 matches)
        recent_results = []
        for match in matches[:5]:
            if match.home_team_id == team_id:
                result = "W" if match.winner == 'HOME_TEAM' else ('D' if match.winner == 'DRAW' else 'L')
            else:
                result = "W" if match.winner == 'AWAY_TEAM' else ('D' if match.winner == 'DRAW' else 'L')
            recent_results.append(result)

        return jsonify({
            'id': team.id,
            'name': team.name,
            'short_name': team.short_name,
            'competition': team.competition,
            'statistics': {
                'total_matches': total_matches,
                'wins': wins,
                'draws': draws,
                'losses': losses,
                'win_rate': round(wins / total_matches, 3) if total_matches > 0 else 0,
                'goals_scored': goals_scored,
                'goals_conceded': goals_conceded,
                'goal_difference': goals_scored - goals_conceded,
                'avg_goals_scored': round(goals_scored / total_matches, 2) if total_matches > 0 else 0,
                'avg_goals_conceded': round(goals_conceded / total_matches, 2) if total_matches > 0 else 0
            },
            'recent_form': ''.join(recent_results)
        }), 200

    except Exception as e:
        return _error_response("Failed to load team", 500, e, endpoint="get_team")

# ============================================================================
# PREDICTIONS
# ============================================================================

@app.route('/api/predict', methods=['POST'])
def predict_match():
    """Predict match outcome (H/D/A) for two teams."""
    try:
        if not model_data:
            return jsonify({'error': 'Model not loaded'}), 503

        data = request.get_json(silent=True) or {}
        try:
            home_team_id = int(data['home_team_id'])
            away_team_id = int(data['away_team_id'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'error': 'home_team_id and away_team_id must be integers'}), 400

        home_team = db.session.query(Team).filter_by(id=home_team_id).first()
        away_team = db.session.query(Team).filter_by(id=away_team_id).first()

        if not home_team or not away_team:
            return jsonify({'error': 'Invalid team ID'}), 404

        # Compute features using shared service
        features_dict = compute_features(
            home_team, away_team, feature_engineer, model_data
        )

        # Predict
        result = predict_match_result(features_dict, model_data)

        response = {
            'home_team': {'id': home_team.id, 'name': home_team.name},
            'away_team': {'id': away_team.id, 'name': away_team.name},
            'prediction': {
                'outcome': result['outcome'],
                'probabilities': result['probabilities'],
                'confidence': result['confidence']
            },
            'features_used': features_dict,
            'model_type': model_data['model_type'],
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        return jsonify(response), 200

    except Exception as e:
        return _error_response("Prediction failed", 500, e, endpoint="predict_match")


@app.route('/api/predict/markets', methods=['POST'])
def predict_markets():
    """
    Predict all betting markets for a match.

    Request body: { "home_team_id": 1, "away_team_id": 2 }

    Returns: match_result, double_chance, markets, combos
    """
    try:
        if not model_data:
            return jsonify({'error': 'Main model not loaded'}), 503

        data = request.get_json(silent=True) or {}
        try:
            home_team_id = int(data['home_team_id'])
            away_team_id = int(data['away_team_id'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'error': 'home_team_id and away_team_id must be integers'}), 400

        home_team = db.session.query(Team).filter_by(id=home_team_id).first()
        away_team = db.session.query(Team).filter_by(id=away_team_id).first()

        if not home_team or not away_team:
            return jsonify({'error': 'Invalid team ID'}), 404

        # Check cache
        cached = prediction_cache.get(home_team_id, away_team_id)
        if cached:
            return jsonify(cached), 200

        # Compute features
        features_dict = compute_features(
            home_team, away_team, feature_engineer, model_data
        )

        # Full multi-market prediction
        prediction = predict_all_markets(features_dict, model_data, multi_market_models)

        response = {
            'home_team': {'id': home_team.id, 'name': home_team.name},
            'away_team': {'id': away_team.id, 'name': away_team.name},
            **prediction,
            'model_type': model_data['model_type'],
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        # Cache it
        prediction_cache.set(home_team_id, away_team_id, response)

        return jsonify(response), 200

    except Exception as e:
        return _error_response("Prediction failed", 500, e, endpoint="predict_markets")


@app.route('/api/predictions/history', methods=['GET'])
def get_prediction_history():
    """Get historical predictions with accuracy"""
    try:
        predictions = db.session.query(Prediction).join(Match).order_by(
            desc(Match.date)
        ).limit(100).all()

        predictions_list = []
        for pred in predictions:
            match = pred.match
            predictions_list.append({
                'id': pred.id,
                'match': {
                    'id': match.id,
                    'home_team': match.home_team.name,
                    'away_team': match.away_team.name,
                    'date': iso_utc(match.date),
                    'actual_winner': match.winner
                },
                'prediction': {
                    'predicted_winner': pred.predicted_winner,
                    'home_win_prob': pred.home_win_prob,
                    'draw_prob': pred.draw_prob,
                    'away_win_prob': pred.away_win_prob,
                    'confidence': pred.confidence
                },
                'correct': pred.correct,
                'model_type': pred.model_type,
                'created_at': iso_utc(pred.created_at)
            })

        total = len([p for p in predictions if p.actual_winner is not None])
        correct = len([p for p in predictions if p.correct])
        accuracy = correct / total if total > 0 else 0

        return jsonify({
            'predictions': predictions_list,
            'statistics': {
                'total_predictions': len(predictions_list),
                'evaluated_predictions': total,
                'correct_predictions': correct,
                'accuracy': round(accuracy, 4)
            }
        }), 200

    except Exception as e:
        return _error_response("Failed to load prediction history", 500, e, endpoint="prediction_history")


@app.route('/api/predictions/upcoming', methods=['GET'])
def get_upcoming_predictions():
    """
    Batch predictions for upcoming matches — powers the dashboard.

    Query params:
        days (int): Days ahead (default 14)
        competition (str): Filter by competition name
        min_confidence (float): Minimum confidence threshold
        sort_by (str): confidence | date | competition (default: date)
        category (str): high_confidence | upset | banker | top_league
    """
    try:
        if not model_data:
            return jsonify({'error': 'Model not loaded'}), 503

        # Clamp days to a 30-day forward window — anything longer hits matches we
        # don't have fixtures for and just wastes DB queries.
        days = max(1, min(request.args.get('days', 14, type=int) or 14, 30))
        competition_filter = request.args.get('competition', '')
        min_confidence = request.args.get('min_confidence', 0, type=float)
        sort_by = request.args.get('sort_by', 'date')
        category = request.args.get('category', '')

        # Fetch upcoming matches from DB
        matches = db.get_upcoming_matches(days)

        results = []
        competitions_set = set()

        for match in matches:
            competitions_set.add(match.competition)

            # Apply competition filter early
            if competition_filter and match.competition != competition_filter:
                continue

            try:
                # Check cache
                date_str = match.date.strftime('%Y-%m-%d') if match.date else None
                cached = prediction_cache.get(match.home_team_id, match.away_team_id, date_str)

                if cached:
                    prediction_data = cached
                else:
                    # Compute features and predict
                    features = compute_features(
                        match.home_team, match.away_team, feature_engineer, model_data,
                        competition=match.competition, match_date=match.date
                    )
                    prediction_data = predict_all_markets(features, model_data, multi_market_models)
                    # Cache with date key
                    prediction_cache.set(
                        match.home_team_id, match.away_team_id, prediction_data, date_str
                    )
                    # Persist the snapshot so we can compute calibration + CLV
                    # against actual outcomes later. Best-effort; failures logged
                    # but don't disrupt the response.
                    _persist_prediction(match, prediction_data)

                # Classify match
                tags, scores = classify_match(prediction_data)

                # Apply category filter
                if category and category not in tags:
                    continue

                # Apply min_confidence filter (documented query param)
                if min_confidence > 0:
                    pred_confidence = prediction_data.get('match_result', {}).get('confidence', 0)
                    if pred_confidence < min_confidence:
                        continue

                results.append({
                    'id': match.id,
                    'date': iso_utc(match.date),
                    'competition': match.competition,
                    'stage': match.stage,
                    'matchday': match.matchday,
                    'home_team': team_dict(match.home_team),
                    'away_team': team_dict(match.away_team),
                    'prediction': prediction_data['match_result'],
                    'double_chance': prediction_data['double_chance'],
                    'markets': prediction_data.get('markets', {}),
                    'combos': prediction_data.get('combos', {}),
                    'match_stats': prediction_data.get('match_stats', {}),
                    'tags': tags,
                    'category_scores': scores,
                })

            except Exception as e:
                logger.warning("Per-match prediction error match_id=%s: %s", match.id, e)
                # Still include the match, just without prediction
                results.append({
                    'id': match.id,
                    'date': iso_utc(match.date),
                    'competition': match.competition,
                    'stage': match.stage,
                    'matchday': match.matchday,
                    'home_team': team_dict(match.home_team),
                    'away_team': team_dict(match.away_team),
                    'prediction': None,
                    'tags': [],
                    'category_scores': {},
                })

        # Sort
        if sort_by == 'confidence':
            results.sort(key=lambda x: (x.get('prediction') or {}).get('confidence', 0), reverse=True)
        elif sort_by == 'competition':
            results.sort(key=lambda x: (x.get('competition', ''), x.get('date', '')))
        else:  # date (default)
            results.sort(key=lambda x: x.get('date', ''))

        # Build summary
        high_conf_count = sum(1 for r in results if 'high_confidence' in r.get('tags', []))
        upset_count = sum(1 for r in results if 'upset' in r.get('tags', []))

        return jsonify({
            'matches': results,
            'meta': {
                'total': len(results),
                'competitions': sorted(competitions_set),
                'high_confidence_count': high_conf_count,
                'upset_count': upset_count,
                'date_range': {
                    'from': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'days': days,
                }
            }
        }), 200

    except Exception as e:
        return _error_response("Failed to load upcoming predictions", 500, e, endpoint="upcoming_predictions")


# ============================================================================
# VALUE BETS — model probabilities vs bookmaker odds
# ============================================================================

@app.route('/api/value-bets', methods=['GET'])
def get_value_bets():
    """
    Find +EV bets across upcoming matches by comparing model probabilities to
    bookmaker odds.

    Query params:
        days (int):       Days ahead to scan (default 7, max 14)
        min_edge (float): Minimum edge threshold (default 0.03 = 3%). Applied
                          to whichever edge flavour `edge_ref` selects.
        books (str):      'sharp' (default) — Pinnacle, exchanges, liquid soft
                          majors. 'all' — every bookmaker (diagnostic; surfaces
                          palp errors as fake 100%+ edges).
        regions (str):    'eu' (default), 'uk', 'us', 'au', or comma-separated.
                          Each extra region doubles The Odds API quota cost,
                          so default stays single-region.
        markets (str):    Comma-separated subset of {h2h, totals, btts}. Default
                          'h2h' to keep quota low. 'h2h,totals,btts' costs 3×
                          per league per fetch.
        edge_ref (str):   'median' (default) — gate on edge vs median trusted
                          price (honest). 'best' — gate on edge vs the single
                          highest-priced book (looser, surfaces outliers).

    Response:
        { enabled, value_bets: [...], meta: {...} }
        Each value_bet has both edge_best and edge_median; the legacy `edge`
        field equals edge_best for backward compat with the old client.
    """
    try:
        if not model_data:
            return jsonify({'error': 'Model not loaded'}), 503

        days = max(1, min(request.args.get('days', 7, type=int) or 7, 14))
        min_edge = max(0.0, request.args.get('min_edge', 0.03, type=float))

        books = request.args.get('books', 'sharp')
        if books not in ('sharp', 'all'):
            books = 'sharp'

        regions = request.args.get('regions', 'eu')
        # Validate regions — comma-separated subset of allowed values
        allowed_regions = {'eu', 'uk', 'us', 'au'}
        regions = ','.join(r for r in regions.split(',') if r in allowed_regions) or 'eu'

        markets_raw = request.args.get('markets', 'h2h')
        # btts is intentionally excluded: The Odds API only offers btts on the
        # per-event endpoint, not the bulk one we use. Supporting it would mean
        # one API request per fixture, which is 73x quota cost on a normal scan.
        # See OddsAPIClient.BULK_SUPPORTED_MARKETS.
        allowed_markets = {'h2h', 'totals'}
        markets = tuple(m for m in markets_raw.split(',') if m in allowed_markets) or ('h2h',)

        edge_ref = request.args.get('edge_ref', 'median')
        if edge_ref not in ('median', 'best'):
            edge_ref = 'median'

        if not odds_client.enabled:
            return jsonify({
                'enabled': False,
                'value_bets': [],
                'meta': {
                    'days': days,
                    'min_edge': min_edge,
                    'count': 0,
                    'message': 'Odds integration not configured. Set ODDS_API_KEY to enable.',
                },
            }), 200

        matches = db.get_upcoming_matches(days)
        results = []
        scanned = 0
        matched = 0

        for match in matches:
            try:
                date_str = match.date.strftime('%Y-%m-%d') if match.date else None
                cached = prediction_cache.get(match.home_team_id, match.away_team_id, date_str)
                if cached:
                    prediction_data = cached
                else:
                    features = compute_features(
                        match.home_team, match.away_team, feature_engineer, model_data,
                        competition=match.competition, match_date=match.date
                    )
                    prediction_data = predict_all_markets(features, model_data, multi_market_models)
                    prediction_cache.set(
                        match.home_team_id, match.away_team_id, prediction_data, date_str
                    )
                    _persist_prediction(match, prediction_data)

                scanned += 1
                odds = odds_client.odds_for_match(
                    match.competition, match.home_team.name, match.away_team.name,
                    books=books, regions=regions, markets=markets,
                )
                if not odds:
                    continue
                matched += 1

                picks = value_picks(
                    prediction_data, odds,
                    min_edge=min_edge,
                    use_median=(edge_ref == 'median'),
                )
                # Extract ensemble agreement + per-market overround once per match
                # (same value applies to every pick within this match).
                ensemble_agreement = (prediction_data.get('match_result') or {}).get('ensemble_agreement')
                # pick['market'] uses our internal label ('totals_2_5') while the
                # odds dict keys mirror the Odds API market keys ('totals'). Translate.
                pick_market_to_odds_key = {'h2h': 'h2h', 'totals_2_5': 'totals', 'btts': 'btts'}
                for pick in picks:
                    market_odds = odds.get(pick_market_to_odds_key.get(pick.get('market'), pick.get('market')), {})
                    results.append({
                        'match_id': match.id,
                        'date': iso_utc(match.date),
                        'competition': match.competition,
                        'home_team': team_dict(match.home_team),
                        'away_team': team_dict(match.away_team),
                        'ensemble_agreement': ensemble_agreement,
                        'overround_best': market_odds.get('overround_best'),
                        'overround_median': market_odds.get('overround_median'),
                        **pick,
                    })
            except Exception as e:
                logger.warning("Value-bet calc failed match_id=%s: %s", match.id, e)

        sort_key = 'edge_median' if edge_ref == 'median' else 'edge_best'
        results.sort(key=lambda x: x.get(sort_key, 0), reverse=True)

        return jsonify({
            'enabled': True,
            'value_bets': results,
            'meta': {
                'days': days,
                'min_edge': min_edge,
                'books': books,
                'regions': regions,
                'markets': list(markets),
                'edge_ref': edge_ref,
                'count': len(results),
                'matches_scanned': scanned,
                'matches_with_odds': matched,
                'quota': odds_client.quota_status(),
            },
        }), 200

    except Exception as e:
        return _error_response("Failed to load value bets", 500, e, endpoint="value_bets")


@app.route('/api/predictions/calibration', methods=['GET'])
def get_prediction_calibration():
    """
    Bucket all evaluated predictions by predicted probability and report the
    actual win rate in each bucket. Used to diagnose model overconfidence —
    perfect calibration is the diagonal y = x.

    Query params:
        outcome (str):       'home_win' | 'draw' | 'away_win' | 'predicted' (default).
                             'predicted' uses the confidence on the predicted outcome.
        bins (int):          Number of buckets (default 10, max 20).
        model_version (str): Optional. Restrict to predictions made by a specific
                             model version. Without this, retrained models pollute
                             the average — v1 + v2 predictions get bucketed together
                             and the plot stops being meaningful per-version.

    Response:
        { buckets: [{lower, upper, count, mean_predicted, actual_rate}, ...],
          summary: { total, brier_score, ece, model_versions: [...] } }

        ECE (Expected Calibration Error): bucket-weighted |actual_rate − mean_pred|.
        Lower is better. Models perfectly calibrated have ECE = 0.
        Brier score: mean squared error between predicted prob and 0/1 outcome.
    """
    try:
        outcome = request.args.get('outcome', 'predicted')
        if outcome not in ('home_win', 'draw', 'away_win', 'predicted'):
            outcome = 'predicted'
        bins = max(2, min(request.args.get('bins', 10, type=int) or 10, 20))
        model_version = request.args.get('model_version', '').strip() or None

        query = db.session.query(Prediction).filter(Prediction.actual_winner.isnot(None))
        if model_version:
            query = query.filter(Prediction.model_version == model_version)
        preds = query.all()
        if not preds:
            return jsonify({
                'buckets': [],
                'summary': {'total': 0, 'note': 'No evaluated predictions yet'},
            }), 200

        # Build (prob, hit) pairs
        pairs: list[tuple[float, int]] = []
        for p in preds:
            if outcome == 'home_win':
                prob = p.home_win_prob
                hit = 1 if p.actual_winner == 'HOME_TEAM' else 0
            elif outcome == 'draw':
                prob = p.draw_prob
                hit = 1 if p.actual_winner == 'DRAW' else 0
            elif outcome == 'away_win':
                prob = p.away_win_prob
                hit = 1 if p.actual_winner == 'AWAY_TEAM' else 0
            else:  # 'predicted' — the probability on the side the model picked
                if p.predicted_winner == 'HOME_TEAM':
                    prob, hit = p.home_win_prob, 1 if p.actual_winner == 'HOME_TEAM' else 0
                elif p.predicted_winner == 'AWAY_TEAM':
                    prob, hit = p.away_win_prob, 1 if p.actual_winner == 'AWAY_TEAM' else 0
                else:
                    prob, hit = p.draw_prob, 1 if p.actual_winner == 'DRAW' else 0
            if prob is not None:
                pairs.append((float(prob), int(hit)))

        # Bucket
        buckets = []
        ece_terms = []
        total = len(pairs)
        for i in range(bins):
            lo = i / bins
            hi = (i + 1) / bins
            # Last bucket inclusive on the upper bound
            in_bucket = [
                (prob, hit) for prob, hit in pairs
                if (lo <= prob < hi) or (i == bins - 1 and prob == 1.0)
            ]
            count = len(in_bucket)
            if count == 0:
                buckets.append({
                    'lower': round(lo, 2), 'upper': round(hi, 2),
                    'count': 0, 'mean_predicted': None, 'actual_rate': None,
                })
                continue
            mean_pred = sum(p for p, _ in in_bucket) / count
            actual = sum(h for _, h in in_bucket) / count
            buckets.append({
                'lower': round(lo, 2), 'upper': round(hi, 2),
                'count': count,
                'mean_predicted': round(mean_pred, 4),
                'actual_rate': round(actual, 4),
            })
            ece_terms.append((count / total) * abs(actual - mean_pred))

        ece = sum(ece_terms)
        brier = sum((prob - hit) ** 2 for prob, hit in pairs) / total

        # Surface distinct model versions in the dataset so the UI can populate
        # a version dropdown without a second roundtrip.
        all_versions = [v[0] for v in
                        db.session.query(distinct(Prediction.model_version))
                        .filter(Prediction.actual_winner.isnot(None))
                        .all() if v[0]]

        # Surface the calibrator state so the UI can label the plot honestly.
        # Note: stored predictions reflect whatever calibrator was active when
        # they were written. The T shown here is the CURRENT loaded calibrator;
        # if you re-fit, old predictions don't update automatically — re-run
        # backfill to align them.
        calibrator = model_data.get('calibrator') if model_data else None
        cal_summary = None
        if calibrator is not None:
            # Both calibrator types expose fit_samples + has a different defining
            # attribute. Probe to figure out which one's active.
            cal_summary = {
                'fit_samples': calibrator.fit_samples,
                'kind': 'isotonic' if hasattr(calibrator, 'models') and calibrator.models
                        else 'temperature',
            }
            if hasattr(calibrator, 'temperature'):
                cal_summary['temperature'] = round(calibrator.temperature, 4)

        return jsonify({
            'outcome': outcome,
            'buckets': buckets,
            'summary': {
                'total': total,
                'ece': round(ece, 4),
                'brier_score': round(brier, 4),
                'model_version': model_version,
                'model_versions_available': sorted(all_versions),
                'calibrator': cal_summary,
            },
        }), 200

    except Exception as e:
        return _error_response("Failed to compute calibration", 500, e, endpoint="calibration")


# ============================================================================
# BETS — paper bet logging + ROI / CLV tracking
# ============================================================================

# Result mapping per market — which actual_winner / score outcomes constitute a win
_BET_OUTCOME_RESOLVERS = {
    'h2h': {
        'home': lambda m: m.winner == 'HOME_TEAM',
        'draw': lambda m: m.winner == 'DRAW',
        'away': lambda m: m.winner == 'AWAY_TEAM',
    },
    'totals_2_5': {
        'over':  lambda m: (m.home_score or 0) + (m.away_score or 0) > 2.5,
        'under': lambda m: (m.home_score or 0) + (m.away_score or 0) < 2.5,
    },
    'btts': {
        'yes': lambda m: (m.home_score or 0) > 0 and (m.away_score or 0) > 0,
        'no':  lambda m: (m.home_score or 0) == 0 or (m.away_score or 0) == 0,
    },
}


def _settle_one_bet(bet: Bet, match: Match) -> bool:
    """
    Resolve a single bet against the finished match. Returns True if a change
    was made (bet became won/lost/void). Idempotent: settled bets are skipped.
    """
    if bet.status != 'pending':
        return False
    if match.status != 'FINISHED' or match.home_score is None or match.away_score is None:
        return False

    resolver = _BET_OUTCOME_RESOLVERS.get(bet.market, {}).get(bet.outcome_key)
    if resolver is None:
        # Unknown market/outcome — mark void so it doesn't stay pending forever
        bet.status = 'void'
        bet.settled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        bet.profit_loss = 0.0
        return True

    won = bool(resolver(match))
    bet.status = 'won' if won else 'lost'
    bet.settled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # P/L = stake * (odds - 1) on win; -stake on loss
    bet.profit_loss = bet.stake * (bet.odds_at_bet - 1.0) if won else -bet.stake
    return True


def _settle_pending_bets(match_ids: list[int] | None = None) -> int:
    """
    Walk all pending bets, settle any whose match is finished. Returns count
    settled. Called by /api/bets/settle and the load-data pipeline.

    If match_ids is provided, restrict to those matches (efficient when called
    after a fixture-refresh that only updated a subset).
    """
    q = db.session.query(Bet).filter(Bet.status == 'pending')
    if match_ids:
        q = q.filter(Bet.match_id.in_(match_ids))
    pending = q.all()
    settled = 0
    for bet in pending:
        match = db.session.query(Match).filter_by(id=bet.match_id).first()
        if match and _settle_one_bet(bet, match):
            settled += 1
    if settled:
        db.session.commit()
    return settled


def _bet_to_dict(bet: Bet) -> dict:
    match = bet.match
    return {
        'id': bet.id,
        'match_id': bet.match_id,
        'match': {
            'home': match.home_team.name if match and match.home_team else None,
            'away': match.away_team.name if match and match.away_team else None,
            'date': iso_utc(match.date) if match else None,
            'competition': match.competition if match else None,
            'status': match.status if match else None,
            'home_score': match.home_score if match else None,
            'away_score': match.away_score if match else None,
            'winner': match.winner if match else None,
        },
        'market': bet.market,
        'outcome_key': bet.outcome_key,
        'outcome_label': bet.outcome_label,
        'odds_at_bet': bet.odds_at_bet,
        'closing_odds': bet.closing_odds,
        'stake': bet.stake,
        'bookmaker': bet.bookmaker,
        'model_prob_at_bet': bet.model_prob_at_bet,
        'edge_at_bet': bet.edge_at_bet,
        'model_version_at_bet': bet.model_version_at_bet,
        'status': bet.status,
        'placed_at': iso_utc(bet.placed_at),
        'settled_at': iso_utc(bet.settled_at),
        'profit_loss': bet.profit_loss,
        'notes': bet.notes,
    }


@app.route('/api/bets', methods=['GET', 'POST'])
def bets_collection():
    """
    GET — list bets, newest first. Query params:
        status: 'pending' | 'won' | 'lost' | 'void' (optional)
        limit:  int (default 100, max 500)

    POST — log a new bet. JSON body:
        {
            "match_id":           int (required),
            "market":             "h2h" | "totals_2_5" | "btts" (required),
            "outcome_key":        "home"/"draw"/"away"/... (required),
            "odds_at_bet":        float (required, > 1.0),
            "stake":              float (required, > 0),
            "bookmaker":          str (optional),
            "outcome_label":      str (optional, free text e.g. "Home Win"),
            "model_prob_at_bet":  float (optional),
            "edge_at_bet":        float (optional),
            "notes":              str (optional),
        }
        Response: created bet dict (201) or {error: ...} (400/404).
    """
    if request.method == 'GET':
        try:
            status = request.args.get('status')
            limit = max(1, min(request.args.get('limit', 100, type=int) or 100, 500))
            # Auto-settle any pending bets whose match has finished — keeps the
            # list view honest without requiring a manual /settle call.
            _settle_pending_bets()
            q = db.session.query(Bet)
            if status in ('pending', 'won', 'lost', 'void'):
                q = q.filter(Bet.status == status)
            bets = q.order_by(desc(Bet.placed_at)).limit(limit).all()
            return jsonify({'bets': [_bet_to_dict(b) for b in bets], 'count': len(bets)}), 200
        except Exception as e:
            return _error_response("Failed to load bets", 500, e, endpoint="bets_list")

    # POST
    try:
        data = request.get_json(silent=True) or {}
        required = ['match_id', 'market', 'outcome_key', 'odds_at_bet', 'stake']
        missing = [k for k in required if k not in data]
        if missing:
            return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

        try:
            match_id = int(data['match_id'])
            odds = float(data['odds_at_bet'])
            stake = float(data['stake'])
        except (TypeError, ValueError):
            return jsonify({'error': 'match_id must be int; odds_at_bet/stake numeric'}), 400

        if odds <= 1.0:
            return jsonify({'error': 'odds_at_bet must be > 1.0'}), 400
        if stake <= 0:
            return jsonify({'error': 'stake must be > 0'}), 400

        market = data['market']
        outcome_key = data['outcome_key']
        if market not in _BET_OUTCOME_RESOLVERS:
            return jsonify({'error': f'Unsupported market: {market}'}), 400
        if outcome_key not in _BET_OUTCOME_RESOLVERS[market]:
            return jsonify({'error': f'Unsupported outcome_key for {market}: {outcome_key}'}), 400

        match = db.session.query(Match).filter_by(id=match_id).first()
        if not match:
            return jsonify({'error': 'Match not found'}), 404

        bet = Bet(
            match_id=match_id,
            market=market,
            outcome_key=outcome_key,
            outcome_label=data.get('outcome_label'),
            odds_at_bet=odds,
            stake=stake,
            bookmaker=data.get('bookmaker'),
            model_prob_at_bet=data.get('model_prob_at_bet'),
            edge_at_bet=data.get('edge_at_bet'),
            model_version_at_bet=str((model_data or {}).get('model_version') or 'unversioned'),
            notes=(data.get('notes') or None),
            placed_via=data.get('placed_via', 'manual'),
        )
        db.session.add(bet)
        db.session.commit()

        # If the match is already finished (logging a historical bet), settle immediately
        _settle_one_bet(bet, match)
        db.session.commit()

        return jsonify(_bet_to_dict(bet)), 201
    except Exception as e:
        db.session.rollback()
        return _error_response("Failed to create bet", 500, e, endpoint="bets_create")


@app.route('/api/bets/<int:bet_id>', methods=['GET', 'DELETE'])
def bet_detail(bet_id):
    """Get or delete a specific bet."""
    bet = db.session.query(Bet).filter_by(id=bet_id).first()
    if not bet:
        return jsonify({'error': 'Bet not found'}), 404
    if request.method == 'DELETE':
        try:
            db.session.delete(bet)
            db.session.commit()
            return jsonify({'deleted': True, 'id': bet_id}), 200
        except Exception as e:
            db.session.rollback()
            return _error_response("Failed to delete bet", 500, e, endpoint="bet_delete")
    return jsonify(_bet_to_dict(bet)), 200


@app.route('/api/bets/settle', methods=['POST'])
def bets_settle():
    """Force-settle all pending bets against currently finished matches."""
    try:
        n = _settle_pending_bets()
        return jsonify({'settled': n}), 200
    except Exception as e:
        return _error_response("Failed to settle bets", 500, e, endpoint="bets_settle")


@app.route('/api/bets/performance', methods=['GET'])
def bets_performance():
    """
    Aggregate ROI, win rate, edge realisation, CLV across all settled bets.

    Query params:
        market:  optional filter ('h2h', 'totals_2_5', 'btts')
        since:   optional ISO date (only bets placed on/after)

    Response includes per-market breakdown and a CLV summary when closing
    odds are available.
    """
    try:
        # Settle pending so stats are current
        _settle_pending_bets()

        q = db.session.query(Bet)
        if (m := request.args.get('market')):
            q = q.filter(Bet.market == m)
        if (since := request.args.get('since')):
            try:
                since_dt = datetime.fromisoformat(since)
                q = q.filter(Bet.placed_at >= since_dt)
            except ValueError:
                return jsonify({'error': 'since must be ISO date'}), 400

        bets = q.all()
        if not bets:
            return jsonify({
                'total_bets': 0,
                'message': 'No bets logged yet. Log paper bets via the Value tab to populate this view.',
            }), 200

        settled = [b for b in bets if b.status in ('won', 'lost')]
        pending = [b for b in bets if b.status == 'pending']
        won = [b for b in settled if b.status == 'won']

        total_stake = sum(b.stake for b in settled)
        total_pl = sum(b.profit_loss or 0 for b in settled)
        roi = (total_pl / total_stake) if total_stake > 0 else 0.0

        # Edge realisation: average edge vs realised win rate
        avg_edge = sum(b.edge_at_bet or 0 for b in settled) / len(settled) if settled else 0
        avg_model_prob = sum(b.model_prob_at_bet or 0 for b in settled) / len(settled) if settled else 0
        win_rate = len(won) / len(settled) if settled else 0

        # CLV: avg of (placed_odds / closing_odds - 1). Positive = bet at better
        # price than the eventual closing line. Only includes bets with closing.
        clv_bets = [b for b in settled if b.closing_odds and b.closing_odds > 1.0]
        avg_clv = (sum(b.odds_at_bet / b.closing_odds - 1 for b in clv_bets) / len(clv_bets)
                   if clv_bets else None)

        # Per-market breakdown
        by_market: dict[str, dict] = {}
        for b in settled:
            m = b.market
            agg = by_market.setdefault(m, {'count': 0, 'stake': 0.0, 'pl': 0.0, 'won': 0})
            agg['count'] += 1
            agg['stake'] += b.stake
            agg['pl'] += b.profit_loss or 0
            if b.status == 'won':
                agg['won'] += 1
        for m, agg in by_market.items():
            agg['roi'] = round(agg['pl'] / agg['stake'], 4) if agg['stake'] > 0 else 0
            agg['win_rate'] = round(agg['won'] / agg['count'], 4) if agg['count'] else 0
            agg['stake'] = round(agg['stake'], 2)
            agg['pl'] = round(agg['pl'], 2)

        return jsonify({
            'total_bets': len(bets),
            'settled_count': len(settled),
            'pending_count': len(pending),
            'won_count': len(won),
            'total_stake': round(total_stake, 2),
            'total_profit_loss': round(total_pl, 2),
            'roi': round(roi, 4),
            'win_rate': round(win_rate, 4),
            'avg_edge_at_bet': round(avg_edge, 4),
            'avg_model_prob_at_bet': round(avg_model_prob, 4),
            'expected_win_rate': round(avg_model_prob, 4),  # alias for clarity
            'avg_clv': round(avg_clv, 4) if avg_clv is not None else None,
            'clv_sample_size': len(clv_bets),
            'by_market': by_market,
        }), 200
    except Exception as e:
        return _error_response("Failed to compute performance", 500, e, endpoint="bets_performance")


# ============================================================================
# COMPETITIONS
# ============================================================================

@app.route('/api/competitions', methods=['GET'])
def get_competitions():
    """Get list of distinct competitions from the database."""
    try:
        comps = db.session.query(distinct(Match.competition)).order_by(Match.competition).all()
        competitions = [c[0] for c in comps if c[0]]
        return jsonify(competitions), 200
    except Exception as e:
        return _error_response("Failed to load competitions", 500, e, endpoint="competitions")


# ============================================================================
# FIXTURE REFRESH
# ============================================================================

@app.route('/api/fixtures/refresh', methods=['POST'])
def refresh_fixtures():
    """
    Fetch upcoming fixtures from football-data.org and upsert into DB.
    This syncs new SCHEDULED/TIMED matches.

    Auth: same X-Reload-Token shared secret as /api/admin/reload-model.
    Without this, anyone could trigger the endpoint and burn our football-data.org
    free-tier quota (10 req/min), churn the DB, and force a full prediction-cache
    rewarm on every call.
    """
    if not _require_admin_token():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        from data_collection import FootballDataCollector

        days = request.args.get('days', 14, type=int)
        # Clamp to free-tier-friendly range — caller can't request a 10-year sync.
        days = max(1, min(days, 60))
        collector = FootballDataCollector()
        fixtures = collector.get_upcoming_fixtures(days=days)

        added = 0
        updated = 0
        skipped = 0

        for fixture in fixtures:
            # Ensure teams exist
            home_team = db.session.query(Team).filter_by(
                api_id=fixture['home_team_api_id']
            ).first()
            away_team = db.session.query(Team).filter_by(
                api_id=fixture['away_team_api_id']
            ).first()

            if not home_team or not away_team:
                skipped += 1
                continue

            # Check if match already exists
            existing = db.session.query(Match).filter_by(
                api_id=fixture['api_id']
            ).first()

            if existing:
                existing.status = fixture['status']
                existing.date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
                updated += 1
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

        # Clear and re-warm prediction cache with new matches
        prediction_cache.clear()
        warm_cache()

        return jsonify({
            'success': True,
            'added': added,
            'updated': updated,
            'skipped': skipped,
            'total_fetched': len(fixtures),
        }), 200

    except Exception as e:
        db.session.rollback()
        return _error_response("Fixture refresh failed", 500, e, endpoint="fixture_refresh")


# ============================================================================
# MATCHES
# ============================================================================

@app.route('/api/matches', methods=['GET'])
def get_matches():
    """Get matches with optional filters"""
    try:
        season = request.args.get('season', type=int)
        team_id = request.args.get('team_id', type=int)
        status = request.args.get('status')
        # Cap to 500 — without this, a single request could pull every row in the table.
        limit = max(1, min(request.args.get('limit', 50, type=int) or 50, 500))

        query = db.session.query(Match)

        if season:
            query = query.filter(Match.season == season)
        if team_id:
            query = query.filter(
                (Match.home_team_id == team_id) | (Match.away_team_id == team_id)
            )
        if status:
            query = query.filter(Match.status == status)

        matches = query.order_by(desc(Match.date)).limit(limit).all()

        matches_list = [{
            'id': match.id,
            'home_team': {'id': match.home_team.id, 'name': match.home_team.name},
            'away_team': {'id': match.away_team.id, 'name': match.away_team.name},
            'date': iso_utc(match.date),
            'season': match.season,
            'matchday': match.matchday,
            'competition': match.competition,
            'stage': match.stage,
            'status': match.status,
            'score': {'home': match.home_score, 'away': match.away_score},
            'winner': match.winner
        } for match in matches]

        return jsonify({
            'matches': matches_list,
            'count': len(matches_list)
        }), 200

    except Exception as e:
        return _error_response("Failed to load matches", 500, e, endpoint="get_matches")

@app.route('/api/matches/<int:match_id>', methods=['GET'])
def get_match(match_id):
    """Get detailed match information including features and prediction"""
    try:
        match = db.session.query(Match).filter_by(id=match_id).first()
        if not match:
            return jsonify({'error': 'Match not found'}), 404

        features = db.session.query(MatchFeatures).filter_by(match_id=match_id).first()
        prediction = db.session.query(Prediction).filter_by(match_id=match_id).first()

        match_data = {
            'id': match.id,
            'home_team': {
                'id': match.home_team.id,
                'name': match.home_team.name,
                'short_name': match.home_team.short_name
            },
            'away_team': {
                'id': match.away_team.id,
                'name': match.away_team.name,
                'short_name': match.away_team.short_name
            },
            'date': iso_utc(match.date),
            'season': match.season,
            'matchday': match.matchday,
            'competition': match.competition,
            'stage': match.stage,
            'status': match.status,
            'score': {'home': match.home_score, 'away': match.away_score},
            'winner': match.winner
        }

        if features:
            match_data['features'] = {
                'home_form_5': features.home_form_5,
                'away_form_5': features.away_form_5,
                'home_goals_scored_avg': features.home_goals_scored_avg,
                'home_goals_conceded_avg': features.home_goals_conceded_avg,
                'away_goals_scored_avg': features.away_goals_scored_avg,
                'away_goals_conceded_avg': features.away_goals_conceded_avg,
                'h2h_home_wins': features.h2h_home_wins,
                'h2h_draws': features.h2h_draws,
                'h2h_away_wins': features.h2h_away_wins,
                'home_win_rate': features.home_win_rate,
                'away_win_rate': features.away_win_rate
            }

        if prediction:
            match_data['prediction'] = {
                'predicted_winner': prediction.predicted_winner,
                'probabilities': {
                    'home_win': prediction.home_win_prob,
                    'draw': prediction.draw_prob,
                    'away_win': prediction.away_win_prob
                },
                'confidence': prediction.confidence,
                'correct': prediction.correct,
                'model_type': prediction.model_type
            }

        return jsonify(match_data), 200

    except Exception as e:
        return _error_response("Failed to load match", 500, e, endpoint="get_match")

@app.route('/api/matches/upcoming', methods=['GET'])
def get_upcoming_matches():
    """Get upcoming scheduled matches with basic predictions"""
    try:
        days = max(1, min(request.args.get('days', 14, type=int) or 14, 30))
        matches = db.get_upcoming_matches(days)

        results = []
        for match in matches:
            match_prediction = None
            if model_data:
                try:
                    features = compute_features(
                        match.home_team, match.away_team, feature_engineer, model_data,
                        competition=match.competition, match_date=match.date
                    )
                    match_prediction = predict_match_result(features, model_data)
                except Exception as e:
                    logger.warning("Per-match prediction error match_id=%s: %s", match.id, e)

            results.append({
                'id': match.id,
                'date': iso_utc(match.date),
                'competition': match.competition,
                'stage': match.stage,
                'matchday': match.matchday,
                'home_team': {
                    'id': match.home_team_id,
                    'name': match.home_team.name,
                    'short_name': match.home_team.short_name
                },
                'away_team': {
                    'id': match.away_team_id,
                    'name': match.away_team.name,
                    'short_name': match.away_team.short_name
                },
                'prediction': match_prediction
            })

        return jsonify(results)

    except Exception as e:
        return _error_response("Failed to load upcoming matches", 500, e, endpoint="upcoming_matches")

# ============================================================================
# STATISTICS
# ============================================================================

@app.route('/api/statistics/overview', methods=['GET'])
def get_statistics_overview():
    """Get overall statistics"""
    try:
        total_matches = db.session.query(Match).filter(Match.status == 'FINISHED').count()
        home_wins = db.session.query(Match).filter(
            and_(Match.status == 'FINISHED', Match.winner == 'HOME_TEAM')
        ).count()
        draws = db.session.query(Match).filter(
            and_(Match.status == 'FINISHED', Match.winner == 'DRAW')
        ).count()
        away_wins = db.session.query(Match).filter(
            and_(Match.status == 'FINISHED', Match.winner == 'AWAY_TEAM')
        ).count()

        matches = db.session.query(Match).filter(Match.status == 'FINISHED').all()
        home_goals = sum(m.home_score or 0 for m in matches)
        away_goals = sum(m.away_score or 0 for m in matches)

        predictions = db.session.query(Prediction).filter(Prediction.actual_winner.isnot(None)).all()
        total_predictions = len(predictions)
        correct_predictions = len([p for p in predictions if p.correct])
        model_accuracy = correct_predictions / total_predictions if total_predictions > 0 else 0

        return jsonify({
            'matches': {
                'total': total_matches,
                'home_wins': home_wins,
                'draws': draws,
                'away_wins': away_wins,
                'home_win_rate': round(home_wins / total_matches, 3) if total_matches > 0 else 0,
                'draw_rate': round(draws / total_matches, 3) if total_matches > 0 else 0,
                'away_win_rate': round(away_wins / total_matches, 3) if total_matches > 0 else 0
            },
            'goals': {
                'total_home_goals': home_goals,
                'total_away_goals': away_goals,
                'avg_home_goals': round(home_goals / total_matches, 2) if total_matches > 0 else 0,
                'avg_away_goals': round(away_goals / total_matches, 2) if total_matches > 0 else 0,
                'avg_total_goals': round((home_goals + away_goals) / total_matches, 2) if total_matches > 0 else 0
            },
            'model': {
                'total_predictions': total_predictions,
                'correct_predictions': correct_predictions,
                'accuracy': round(model_accuracy, 4),
                'model_type': model_data['model_type'] if model_data else None
            }
        }), 200

    except Exception as e:
        return _error_response("Failed to load statistics", 500, e, endpoint="statistics_overview")

@app.route('/api/statistics/head-to-head', methods=['GET'])
def get_head_to_head():
    """Get head-to-head statistics between two teams"""
    try:
        team1_id = request.args.get('team1_id', type=int)
        team2_id = request.args.get('team2_id', type=int)

        if not team1_id or not team2_id:
            return jsonify({'error': 'Missing team1_id or team2_id'}), 400

        matches = db.session.query(Match).filter(
            and_(
                (
                    (Match.home_team_id == team1_id) & (Match.away_team_id == team2_id)
                ) | (
                    (Match.home_team_id == team2_id) & (Match.away_team_id == team1_id)
                ),
                Match.status == 'FINISHED'
            )
        ).order_by(desc(Match.date)).all()

        team1_wins = 0
        team2_wins = 0
        draws_count = 0
        team1_goals = 0
        team2_goals = 0
        recent_matches = []

        for match in matches:
            if match.home_team_id == team1_id:
                team1_goals += match.home_score or 0
                team2_goals += match.away_score or 0
                if match.winner == 'HOME_TEAM':
                    team1_wins += 1
                    result = 'team1_win'
                elif match.winner == 'DRAW':
                    draws_count += 1
                    result = 'draw'
                else:
                    team2_wins += 1
                    result = 'team2_win'
            else:
                team1_goals += match.away_score or 0
                team2_goals += match.home_score or 0
                if match.winner == 'AWAY_TEAM':
                    team1_wins += 1
                    result = 'team1_win'
                elif match.winner == 'DRAW':
                    draws_count += 1
                    result = 'draw'
                else:
                    team2_wins += 1
                    result = 'team2_win'

            recent_matches.append({
                'date': iso_utc(match.date),
                'home_team': match.home_team.name,
                'away_team': match.away_team.name,
                'score': f"{match.home_score}-{match.away_score}",
                'result': result
            })

        return jsonify({
            'team1': {
                'id': team1_id,
                'name': db.session.get(Team, team1_id).name,
                'wins': team1_wins,
                'goals_scored': team1_goals,
                'goals_conceded': team2_goals
            },
            'team2': {
                'id': team2_id,
                'name': db.session.get(Team, team2_id).name,
                'wins': team2_wins,
                'goals_scored': team2_goals,
                'goals_conceded': team1_goals
            },
            'draws': draws_count,
            'total_matches': len(matches),
            'recent_matches': recent_matches[:5]
        }), 200

    except Exception as e:
        return _error_response("Failed to load head-to-head", 500, e, endpoint="head_to_head")

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Scheduled fixture refresh and nightly retraining are handled by the Azure
# Container Apps Job defined in backend/jobs/retrain.py — not by an in-process
# scheduler. The backend container is stateless and scale-to-zero, so a per-process
# cron would either run N times (one per gunicorn worker) or not at all (when scaled
# down). The job runs once nightly at 03:00 UTC regardless of replica state.

# ============================================================================
# RUN APP
# ============================================================================

if __name__ == '__main__':
    print("\n" + "=" * 70)
    print("STARTING FOOTBALL PREDICTOR API")
    print("=" * 70)
    print(f"Model: {model_data['model_type'] if model_data else 'NOT_LOADED'}")
    print(f"Multi-market: {'Loaded' if multi_market_models else 'NOT_LOADED'}")
    print("Database: Connected")
    print(f"CORS: Enabled for {FRONTEND_URL}")
    print("=" * 70 + "\n")

    # Debug mode exposes the Werkzeug interactive debugger — never enable in
    # production. Production uses gunicorn (see gunicorn.conf.py) and never hits
    # this block, but we gate on FLASK_DEBUG anyway so an accidental `python app.py`
    # in prod can't open the debugger pin endpoint to the world.
    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(debug=debug, host='0.0.0.0', port=5000)
