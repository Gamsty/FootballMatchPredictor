"""
Flask API for Football Match Predictor

Endpoints:
    - /api/health          — API health check and model status
    - /api/teams           — List all teams
    - /api/teams/<id>      — Team details with statistics and recent form
    - /api/predict         — Predict match outcome using ML model
    - /api/predictions/history — Past predictions with accuracy stats
    - /api/matches         — List matches with optional filters (season, team, status)
    - /api/matches/<id>    — Single match detail with features and prediction
    - /api/statistics/overview     — Overall match and goal statistics
    - /api/statistics/head-to-head — Head-to-head record between two teams
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import pandas as pd
import joblib
from dotenv import load_dotenv
import os

from database import DatabaseManager, Match, Team, Prediction, MatchFeatures
from feature_engineering import FeatureEngineer
from sqlalchemy import and_, desc

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')

# Configure CORS — allows React frontend to call the API
CORS(app, resources={
    r"/api/*": {
    "origins": [
        FRONTEND_URL,
        "http://localhost:5173",
        "http://localhost:3000",
        "https://football-match-predictor-pearl.vercel.app",
    ],
    "methods": ["GET", "POST", "PUT", "DELETE"],
    "allow_headers": ["Content-Type"]
    }
})

# Load ML model (resolve path relative to this file, not the working directory)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, '..', 'models', 'xgboost_model.pkl')
model_data = None

def load_model():
    """Load ML model at startup"""
    global model_data
    try:
        model_data = joblib.load(MODEL_PATH)
        print(f"✓ Model loaded successfully from {MODEL_PATH}")
        print(f"    Model type: {model_data['model_type']}")
        print(f"    Features: {len(model_data['feature_names'])}")
    except Exception as e:
        print(f"✗ Error loading model: {e}")
        model_data = None

# Load model on startup
load_model()

# Database manager
db = DatabaseManager()
feature_engineer = FeatureEngineer()

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Check API health and model status"""
    return jsonify({
        'status': 'healthy',
        'model_loaded': model_data is not None,
        'model_type': model_data['model_type'] if model_data else None,
        'timestamp': datetime.utcnow().isoformat()
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
        return jsonify({'error': str(e)}), 500
    
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
                # Team played at home
                goals_scored += match.home_score or 0
                goals_conceded += match.away_score or 0

                if match.winner == 'HOME_TEAM':
                    wins += 1
                elif match.winner == 'DRAW':
                    draws += 1
                else:
                    losses += 1
            else:
                # Team played away
                goals_scored += match.away_score or 0
                goals_conceded += match.home_score or 0

                if match.winner == 'AWAY_TEAM':
                    wins += 1
                elif match.winner == 'DRAW':
                    draws += 1
                else:
                    losses += 1

        # Recent form (last 5 matches)
        recent_matches = matches[:5]
        recent_results = []

        for match in recent_matches:
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
        return jsonify({'error': str(e)}), 500
    
# ============================================================================
# PREDICTIONS
# ============================================================================

@app.route('/api/predict', methods=['POST'])
def predict_match():
    """
    Predict match outcome

    Request body:
    {
        "home_team_id": 1,
        "away_team_id": 2
    }
    """
    try:
        if not model_data:
            return jsonify({'error': 'Model not loaded'}), 500
        
        data = request.json

        # Validate input
        if 'home_team_id' not in data or 'away_team_id' not in data:
            return jsonify({'error': 'Missing home_team_id or away_team_id'}), 400
        
        home_team_id = int(data['home_team_id'])
        away_team_id = int(data['away_team_id'])

        # Check teams exists
        home_team = db.session.query(Team).filter_by(id=home_team_id).first()
        away_team = db.session.query(Team).filter_by(id=away_team_id).first()

        if not home_team or not away_team:
            return jsonify({'error': 'Invalid team ID'}), 404
        
        # Create a temporary match object for feature calculation
        # Mimics the Match ORM model so feature_engineer.create_match_features() works
        class TempMatch:
            def __init__(self, home_id, away_id):
                self.id = None
                self.home_team_id = home_id
                self.away_team_id = away_id
                self.date = datetime.utcnow()

        temp_match = TempMatch(home_team_id, away_team_id)

        # Calculate features
        features_dict = feature_engineer.create_match_features(temp_match)

        # Remove match_id (not needed for prediction)
        features_dict.pop('match_id', None)

        # Prepare features for model
        feature_names = model_data['feature_names']
        X = pd.DataFrame([features_dict])[feature_names]

        # Scale features
        scaler = model_data['scaler']
        X_scaled = scaler.transform(X)

        # Make prediction
        model = model_data['model']
        prediction = model.predict(X_scaled)[0]
        probabilities = model.predict_proba(X_scaled)[0]

        # Map prediction to labels
        outcome_map = {0: 'AWAY_WIN', 1: 'DRAW', 2: 'HOME_WIN'}
        predicted_outcome = outcome_map[prediction]

        # Prepare response
        response = {
            'home_team': {
                'id': home_team.id,
                'name': home_team.name
            },
            'away_team': {
                'id': away_team.id,
                'name': away_team.name
            },
            # Map probability indices to class labels from the encoder
            'prediction': {
                'outcome': predicted_outcome,
                'probabilities': {
                    'home_win': float(probabilities[2]),
                    'draw': float(probabilities[1]),
                    'away_win': float(probabilities[0])
                },
                'confidence': float(max(probabilities))
            },
            'features_used': features_dict,
            'model_type': model_data['model_type'],
            'timestamp': datetime.utcnow().isoformat()
        }

        return jsonify(response), 200
    
    except Exception as e:
        print(f"Prediction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/predictions/history', methods=['GET'])
def get_prediction_history():
    """Get historical predictions with accuracy"""
    try:
        # Get predictions from database
        predictions = db.session.query(Prediction).join(Match).order_by(
            desc(Match.date)
        ).limit(100).all()

        predictions_list = []

        for pred in predictions:
            match: Match = pred.match

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

        # Calculate accuracy
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
        return jsonify({'error': str(e)}), 500
    
# ============================================================================
# MATCHES
# ============================================================================

@app.route('/api/matches', methods=['GET'])
def get_matches():
    """Get matches with optional filters"""
    try:
        # Query parameters
        season = request.args.get('season', type=int)
        team_id = request.args.get('team_id', type=int)
        status = request.args.get('status')
        limit = request.args.get('limit', 50, type=int)

        # Build query
        query = db.session.query(Match)

        if season:
            query = query.filter(Match.season == season)

        if team_id:
            query = query.filter(
                (Match.home_team_id == team_id) | (Match.away_team_id == team_id)
            )

        if status:
            query = query.filter(Match.status == status)

        # Order by date descending and limit
        matches = query.order_by(desc(Match.date)).limit(limit).all()

        matches_list = []

        for match in matches:
            matches_list.append({
                'id': match.id,
                'home_team': {
                    'id': match.home_team.id,
                    'name': match.home_team.name
                },
                'away_team': {
                    'id': match.away_team.id,
                    'name': match.away_team.name
                },
                'date': match.date.isoformat(),
                'season': match.season,
                'matchday': match.matchday,
                'competition': match.competition,
                'status': match.status,
                'score': {
                    'home': match.home_score,
                    'away': match.away_score
                },
                'winner': match.winner
            })

        return jsonify({
            'matches': matches_list,
            'count': len(matches_list)
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/matches/<int:match_id>', methods=['GET'])
def get_match(match_id):
    """Get detailed match information including features and prediction"""
    try:
        match = db.session.query(Match).filter_by(id=match_id).first()

        if not match:
            return jsonify({'error': 'Match not found'}), 404
        
        # Get features if available
        features = db.session.query(MatchFeatures).filter_by(match_id=match_id).first()

        # Get prediction if available
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
            'status': match.status,
            'score': {
                'home': match.home_score,
                'away': match.away_score
            },
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
        return jsonify({'error': str(e)}), 500
    
# ============================================================================
# STATISTICS
# ============================================================================
@app.route('/api/statistics/overview', methods=['GET'])
def get_statistics_overview():
    """Get overall statistics"""
    try:
        # Count matches
        total_matches = db.session.query(Match).filter(Match.status == 'FINISHED').count()

        # Count by winner
        home_wins = db.session.query(Match).filter(
            and_(
                Match.status == 'FINISHED', Match.winner == 'HOME_TEAM'
            )
        ).count()

        draws = db.session.query(Match).filter(
            and_(
                Match.status == 'FINISHED', Match.winner == 'DRAW'
            )
        ).count()

        away_wins = db.session.query(Match).filter(
            and_(
                Match.status == 'FINISHED', Match.winner == 'AWAY_TEAM'
            )
        ).count()

        # Average goals
        matches = db.session.query(Match).filter(Match.status == 'FINISHED').all()

        home_goals = sum(m.home_score or 0 for m in matches)
        away_goals = sum(m.away_score or 0 for m in matches)

        # Model accuracy
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
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/statistics/head-to-head', methods=['GET'])
def get_head_to_head():
    """Get head-to-head statistics between two teams"""
    try:
        team1_id = request.args.get('team1_id', type=int)
        team2_id = request.args.get('team2_id', type=int)

        if not team1_id or not team2_id:
            return jsonify({'error': 'Missing team1_id or team2_id'}), 400
        
        # Get matches between these teams
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

        # Calculate statistics
        team1_wins = 0
        team2_wins = 0
        draws = 0
        team1_goals = 0
        team2_goals = 0

        recent_matches = []

        for match in matches:
            if match.home_team_id == team1_id:
                # Team 1 home
                team1_goals += match.home_score or 0
                team2_goals += match.away_score or 0

                if match.winner == 'HOME_TEAM':
                    team1_wins += 1
                    result = 'team1_win'
                elif match.winner == 'DRAW':
                    draws += 1
                    result = 'draw'
                else:
                    team2_wins += 1
                    result = 'team2_win'
            else:
                # Team 1 away
                team1_goals += match.away_score or 0
                team2_goals += match.home_score or 0

                if match.winner == 'AWAY_TEAM':
                    team1_wins += 1
                    result = 'team1_win'
                elif match.winner == 'DRAW':
                    draws += 1
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
            'draws': draws,
            'total_matches': len(matches),
            'recent_matches': recent_matches[:5]  # Last 5 matches
        }), 200
 
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# ============================================================================
# RUN APP
# ============================================================================

if __name__ == '__main__':
    print("\n" + "=" * 70)
    print("STARTING FOOTBALL PREDICTOR API")
    print("=" * 70)
    print(f"Model: {model_data['model_type'] if model_data else 'NOT_LOADED'}")
    print(f"Database: Connected")
    print(f"CORS: Enabled for http://localhost:5173")
    print("=" * 70 + "\n")

    app.run(debug=True, host='0.0.0.0', port=5000)



