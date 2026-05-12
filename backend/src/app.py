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

from database import DatabaseManager, Match, Team, Prediction, MatchFeatures, init_db
from feature_engineering import FeatureEngineer
from prediction_service import (
    compute_features, predict_match_result, predict_all_markets, classify_match
)
from cache import PredictionCache
from model_storage import load_model_bytes
from telemetry import setup_telemetry
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
    Auth: shared secret in X-Reload-Token header.
    """
    if not _require_admin_token():
        return jsonify({"error": "Unauthorized"}), 401
    load_model()
    prediction_cache.clear()
    return jsonify({
        "reloaded": True,
        "model_type": model_data["model_type"] if model_data else None,
    }), 200

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
                    'date': match.date.isoformat(),
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
                'created_at': pred.created_at.isoformat()
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
                    'date': match.date.isoformat(),
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
                    'date': match.date.isoformat(),
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
            'date': match.date.isoformat(),
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
            'date': match.date.isoformat(),
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
                'date': match.date.isoformat(),
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
                'date': match.date.isoformat(),
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
