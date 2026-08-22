"""
Prediction Service Module

Centralizes all prediction logic so it can be shared across API endpoints.

Key functions:
    - compute_features(): Build feature dict for a match (Elo, form, goals, etc.)
    - predict_match_result(): H/D/A prediction with probabilities and odds
    - predict_all_markets(): Full multi-market prediction (result, double chance,
                             BTTS, over/under, combos) + match_stats for bet descriptions
    - classify_match(): Tag a match as high_confidence, upset, or banker using Elo ratings

Flow: compute_features() → predict_all_markets() → classify_match()
"""

import pandas as pd
from datetime import datetime, timezone


# Top leagues for tagging
TOP_LEAGUES = {
    'Premier League', 'La Liga', 'Bundesliga', 'Serie A', 'Ligue 1',
    'Primeira Liga', 'UEFA Champions League'
}


class TempMatch:
    """Mimics the Match ORM model for feature calculation on upcoming matches."""
    def __init__(self, home_id, away_id, competition=None, date=None):
        self.id = None
        self.home_team_id = home_id
        self.away_team_id = away_id
        # datetime.utcnow() is deprecated in Python 3.12+; use tz-aware UTC.
        # The downstream feature pipeline only reads year/month/day so naive vs tz-aware
        # doesn't matter here, but using the recommended API silences warnings.
        self.date = date or datetime.now(timezone.utc)
        self.season = self.date.year if self.date.month >= 8 else self.date.year - 1
        self.competition = competition
        self.stage = 'REGULAR_SEASON'


def compute_features(home_team, away_team, feature_engineer, model_data,
                     competition=None, match_date=None):
    """
    Compute full feature dict for a match, including Elo and derived features.

    Args:
        home_team: Team ORM object
        away_team: Team ORM object
        feature_engineer: FeatureEngineer instance
        model_data: Loaded model dict with 'feature_names', 'scaler', 'model', 'elo_ratings'
        competition: Optional competition name
        match_date: Optional datetime for the match

    Returns:
        dict: Feature dictionary ready for prediction
    """
    temp_match = TempMatch(home_team.id, away_team.id, competition, match_date)
    features = feature_engineer.create_match_features(temp_match)
    features.pop('match_id', None)

    # Save raw stats before None-replacement (for bet descriptions)
    STAT_KEYS = [
        'home_form_5', 'away_form_5', 'home_win_rate', 'away_win_rate',
        'home_goals_scored_avg', 'away_goals_scored_avg',
        'home_goals_conceded_avg', 'away_goals_conceded_avg',
        'home_league_position', 'away_league_position',
        'h2h_home_wins', 'h2h_away_wins', 'h2h_draws',
        'home_clean_sheet_rate', 'away_clean_sheet_rate',
    ]
    raw_stats = {}
    for k in STAT_KEYS:
        val = features.get(k)
        raw_stats[k] = round(val, 2) if val is not None else None

    # Rest days must be imputed BEFORE the blanket zero-fill below. Training fills
    # a missing gap with 7 days (_build_xy_from_csv), deliberately — a constant so
    # no holdout statistics leak into the train set. Serving used to let the
    # zero-fill win, so a club with no prior fixture in the DB (season openers,
    # promoted sides, any coverage gap) was served rest_days=0 against a model
    # that learned on 7. The `g(..., 7)` defaults in _compute_derived_features
    # never fired either, because by then the value was 0 rather than None.
    for key in ('days_since_home_last_match', 'days_since_away_last_match'):
        if features.get(key) is None:
            features[key] = 7

    # Replace remaining None values with 0 (upcoming matches often have None for
    # many features)
    for key in list(features.keys()):
        if features[key] is None:
            features[key] = 0

    # Add Elo ratings
    elo_ratings = model_data.get('elo_ratings')
    if elo_ratings and 'home_elo' in model_data.get('feature_names', []):
        features['home_elo'] = elo_ratings.get(home_team.name, 1500.0)
        features['away_elo'] = elo_ratings.get(away_team.name, 1500.0)

    # Compute derived features
    _compute_derived_features(features, model_data['feature_names'])

    # Attach raw stats for descriptions
    features['_raw_stats'] = raw_stats

    return features


def _compute_derived_features(features_dict, feature_names):
    """Compute derived features to match training pipeline. Modifies dict in-place."""
    fn = feature_names

    # Helper: get value, treating None as the default (dict.get returns None if key exists with None value)
    def g(key, default=0):
        val = features_dict.get(key)
        return val if val is not None else default

    if 'position_diff' in fn:
        features_dict['position_diff'] = g('home_league_position') - g('away_league_position')
    if 'points_diff' in fn:
        features_dict['points_diff'] = g('home_points') - g('away_points')
    if 'gd_diff' in fn:
        features_dict['gd_diff'] = g('home_goal_difference') - g('away_goal_difference')
    if 'form_diff' in fn:
        features_dict['form_diff'] = g('home_form_5') - g('away_form_5')
    if 'win_rate_diff' in fn:
        features_dict['win_rate_diff'] = g('home_win_rate') - g('away_win_rate')
    if 'home_attack_vs_away_defense' in fn:
        features_dict['home_attack_vs_away_defense'] = g('home_goals_scored_avg') - g('away_goals_conceded_avg')
    if 'away_attack_vs_home_defense' in fn:
        features_dict['away_attack_vs_home_defense'] = g('away_goals_scored_avg') - g('home_goals_conceded_avg')
    if 'h2h_dominance' in fn:
        features_dict['h2h_dominance'] = g('h2h_home_wins') - g('h2h_away_wins')
    if 'rest_diff' in fn:
        features_dict['rest_diff'] = g('days_since_home_last_match', 7) - g('days_since_away_last_match', 7)
    if 'elo_diff' in fn:
        features_dict['elo_diff'] = g('home_elo', 1500) - g('away_elo', 1500)
    if 'draw_rate_sum' in fn:
        features_dict['draw_rate_sum'] = g('home_draw_rate') + g('away_draw_rate')
    if 'weighted_form_diff' in fn:
        features_dict['weighted_form_diff'] = g('home_weighted_form') - g('away_weighted_form')
    if 'shots_on_target_diff' in fn:
        features_dict['shots_on_target_diff'] = g('home_shots_on_target_avg') - g('away_shots_on_target_avg')
    if 'corners_diff' in fn:
        features_dict['corners_diff'] = g('home_corners_avg') - g('away_corners_avg')
    if 'motivation_diff' in fn:
        features_dict['motivation_diff'] = g('away_points_from_top') - g('home_points_from_top')
    if 'squad_strength_diff' in fn:
        features_dict['squad_strength_diff'] = g('away_avg_position_3yr') - g('home_avg_position_3yr')

    # Fill missing or None values with 0
    for f in fn:
        if features_dict.get(f) is None:
            features_dict[f] = 0


def _ensemble_agreement(model, X_scaled) -> float | None:
    """
    Compute a confidence proxy from the disagreement between base learners
    inside a StackingClassifier.

    Each base estimator (XGBoost, RandomForest) outputs its own probability
    distribution. We compute the total variation distance between each pair
    of distributions, average them, and report 1 − that as "agreement". Range:
      1.0 → all base models give exactly the same distribution → high confidence
      0.0 → maximum possible disagreement → low confidence

    Returns None when the model isn't a fitted StackingClassifier (e.g. legacy
    plain XGBoost) — caller should treat as "confidence unknown" and not show
    the indicator.

    Why this instead of bootstrap CIs: full bootstrap would require N retrainings
    of the stacked ensemble — minutes per prediction, not viable online. This
    uses information that's already computed during the stacking fit and only
    costs two extra predict_proba calls.
    """
    base = getattr(model, 'named_estimators_', None)
    if not base or len(base) < 2:
        return None
    try:
        dists = []
        for est in base.values():
            p = est.predict_proba(X_scaled)[0]
            dists.append(p)
        if len(dists) < 2:
            return None
        # Average total variation distance across all unique base-model pairs.
        n_pairs = 0
        total_tvd = 0.0
        for i in range(len(dists)):
            for j in range(i + 1, len(dists)):
                # TVD = 0.5 * Σ |p_i − q_i|. Range [0, 1].
                tvd = 0.5 * sum(abs(a - b) for a, b in zip(dists[i], dists[j]))
                total_tvd += tvd
                n_pairs += 1
        avg_tvd = total_tvd / n_pairs if n_pairs else 0.0
        return round(1.0 - avg_tvd, 4)
    except Exception:
        # Anything unexpected → don't crash inference, just hide the indicator
        return None


def predict_match_result(features_dict, model_data, apply_calibration: bool = True):
    """
    Predict H/D/A from features using the main model.

    Args:
        apply_calibration: If True (default) and model_data has a fitted
            TemperatureCalibrator under 'calibrator', apply it to the raw
            softmax probabilities before returning. Set False for fit jobs
            that need the model's uncalibrated output (otherwise fitting T
            on already-calibrated probs trivially returns T=1).

    Returns dict with:
        outcome, probabilities, raw_probabilities, confidence,
        ensemble_agreement, odds, calibrated (bool)
    """
    import numpy as np

    fn = model_data['feature_names']
    X = pd.DataFrame([{f: features_dict.get(f, 0) for f in fn}])
    X_scaled = model_data['scaler'].transform(X)

    model = model_data['model']
    proba = model.predict_proba(X_scaled)[0]

    # Class order from the trained model: [AWAY_WIN=0, DRAW=1, HOME_WIN=2]
    raw_home = float(proba[2])
    raw_draw = float(proba[1])
    raw_away = float(proba[0])

    # Apply calibration if available. Temperature scaling is monotone, so the
    # ranking survives it. An isotonic calibrator is fitted per class and then
    # renormalised, which can reorder outcomes — so nothing downstream may
    # assume the calibrated argmax matches the raw one.
    calibrator = model_data.get('calibrator') if apply_calibration else None
    if calibrator is not None:
        # Calibrator expects same class order as `proba`
        scaled = calibrator.transform(np.asarray(proba))
        home_prob = float(scaled[2])
        draw_prob = float(scaled[1])
        away_prob = float(scaled[0])
        is_calibrated = True
    else:
        home_prob, draw_prob, away_prob = raw_home, raw_draw, raw_away
        is_calibrated = False

    # Recomputed from the CALIBRATED probabilities. This was previously
    # justified with "argmax doesn't change under temperature scaling" — true of
    # temperature, false of the isotonic calibrator that was actually in
    # service, which flipped the pick on two of six fixtures sampled.
    pred_idx = int(np.argmax([away_prob, draw_prob, home_prob]))
    outcome_map = {0: 'AWAY_WIN', 1: 'DRAW', 2: 'HOME_WIN'}

    return {
        'outcome': outcome_map.get(pred_idx, 'UNKNOWN'),
        'probabilities': {
            'home_win': round(home_prob, 4),
            'draw': round(draw_prob, 4),
            'away_win': round(away_prob, 4),
        },
        # Always surface the raw (pre-calibration) probs alongside so consumers
        # that want to compare calibrated vs raw (e.g. /api/predictions/calibration
        # in 'raw' mode) can do so without re-running inference.
        'raw_probabilities': {
            'home_win': round(raw_home, 4),
            'draw': round(raw_draw, 4),
            'away_win': round(raw_away, 4),
        },
        'calibrated': is_calibrated,
        'confidence': round(max(home_prob, draw_prob, away_prob), 4),
        # Ensemble agreement is a proxy for prediction stability. High agreement
        # (>0.85) means XGBoost and RandomForest produced near-identical
        # distributions; low agreement (<0.7) means they materially disagree
        # and the stacked prediction is averaging over uncertainty.
        'ensemble_agreement': _ensemble_agreement(model, X_scaled),
        'odds': {
            'home_win': round(1 / home_prob, 2) if home_prob > 0.01 else None,
            'draw': round(1 / draw_prob, 2) if draw_prob > 0.01 else None,
            'away_win': round(1 / away_prob, 2) if away_prob > 0.01 else None,
        }
    }


def predict_market(features_dict, market_model):
    """Run prediction for a single market model."""
    fn = market_model['feature_names']
    _compute_derived_features(features_dict, fn)
    X = pd.DataFrame([{f: features_dict.get(f, 0) for f in fn}])
    X_scaled = market_model['scaler'].transform(X)
    proba = market_model['model'].predict_proba(X_scaled)[0]
    pred = market_model['model'].predict(X_scaled)[0]

    labels = market_model['labels']
    return {
        'prediction': labels[int(pred)],
        'probabilities': {labels[i]: round(float(proba[i]), 4) for i in range(len(labels))},
        'confidence': round(float(max(proba)), 4),
    }


def predict_all_markets(features_dict, model_data, multi_market_models):
    """
    Run full multi-market prediction: match result, double chance, markets, combos.

    Returns:
        dict with match_result, double_chance, markets, combos, elo
    """
    # 1. Match result
    result = predict_match_result(features_dict, model_data)
    home_prob = result['probabilities']['home_win']
    draw_prob = result['probabilities']['draw']
    away_prob = result['probabilities']['away_win']

    # 2. Double chance
    double_chance = {
        '1X': {
            'description': 'Home or Draw',
            'probability': round(home_prob + draw_prob, 4),
            'odds': round(1 / (home_prob + draw_prob), 2) if (home_prob + draw_prob) > 0 else None,
        },
        'X2': {
            'description': 'Draw or Away',
            'probability': round(draw_prob + away_prob, 4),
            'odds': round(1 / (draw_prob + away_prob), 2) if (draw_prob + away_prob) > 0 else None,
        },
        '12': {
            'description': 'Home or Away',
            'probability': round(home_prob + away_prob, 4),
            'odds': round(1 / (home_prob + away_prob), 2) if (home_prob + away_prob) > 0 else None,
        },
    }

    # 3. Multi-market predictions
    markets = {}
    if multi_market_models:
        for market_name, market_model in multi_market_models.items():
            try:
                markets[market_name] = predict_market(features_dict, market_model)
            except Exception as e:
                markets[market_name] = {'error': str(e)}

    # 4. Combo bets
    combos = _build_combos(home_prob, draw_prob, away_prob, markets)

    # Extract key stats from raw (pre-None-replacement) values for bet descriptions
    raw = features_dict.get('_raw_stats', {})
    def rs(key, default=0):
        val = raw.get(key)
        return val if val is not None else default

    home_elo = features_dict.get('home_elo', 1500)
    away_elo = features_dict.get('away_elo', 1500)

    match_stats = {
        'home_elo': round(home_elo, 1),
        'away_elo': round(away_elo, 1),
        'home_form': rs('home_form_5'),
        'away_form': rs('away_form_5'),
        'home_win_rate': rs('home_win_rate'),
        'away_win_rate': rs('away_win_rate'),
        'home_goals_scored_avg': rs('home_goals_scored_avg'),
        'away_goals_scored_avg': rs('away_goals_scored_avg'),
        'home_goals_conceded_avg': rs('home_goals_conceded_avg'),
        'away_goals_conceded_avg': rs('away_goals_conceded_avg'),
        'home_league_position': rs('home_league_position'),
        'away_league_position': rs('away_league_position'),
        'h2h_home_wins': rs('h2h_home_wins'),
        'h2h_away_wins': rs('h2h_away_wins'),
        'h2h_draws': rs('h2h_draws'),
        'home_clean_sheets': rs('home_clean_sheet_rate'),
        'away_clean_sheets': rs('away_clean_sheet_rate'),
    }

    return {
        'match_result': result,
        'double_chance': double_chance,
        'markets': markets,
        'combos': combos,
        'elo': {
            'home': features_dict.get('home_elo', 1500),
            'away': features_dict.get('away_elo', 1500),
        },
        'match_stats': match_stats,
    }


# Combo probabilities are products of marginals, i.e. they assume the two
# markets are independent. They are not: a home win co-occurs with "no BTTS"
# far more often than P(home) x P(no BTTS) implies, and a draw with over 2.5
# far less. The error has a sign that varies by combination, so it cannot be
# corrected with a single fudge factor — pricing these properly needs a joint
# scoreline model (bivariate Poisson / Dixon-Coles) that yields mutually
# consistent 1X2, BTTS and totals probabilities.
#
# Until then every combo carries `independence_assumed: True` so consumers can
# label the number honestly rather than treating it as a modelled joint
# probability. Edges computed from these are indicative, not measured.
COMBO_INDEPENDENCE_NOTE = (
    "Combined probability is the product of two market probabilities and assumes "
    "they are independent. Real football outcomes are correlated, so treat combo "
    "edges as indicative only."
)


def _build_combos(home_prob, draw_prob, away_prob, markets):
    """Build all combo bet probabilities.

    See COMBO_INDEPENDENCE_NOTE — these are products of marginals, not modelled
    joint probabilities.
    """
    combos = {}

    def get_prob(market, label):
        if market in markets and 'probabilities' in markets[market]:
            return markets[market]['probabilities'].get(label, 0)
        return None

    # Result + BTTS
    btts_yes = get_prob('btts', 'Yes')
    btts_no = get_prob('btts', 'No')
    if btts_yes is not None:
        for key, label, prob in [('H', 'Home Win', home_prob), ('D', 'Draw', draw_prob), ('A', 'Away Win', away_prob)]:
            for suffix, btts_p, desc in [('btts_yes', btts_yes, 'Both Teams Score'), ('btts_no', btts_no, 'Clean Sheet')]:
                cp = round(prob * btts_p, 4)
                combos[f'{key}_{suffix}'] = {
                    'description': f'{label} & {desc}',
                    'probability': cp,
                    'odds': round(1 / cp, 2) if cp > 0 else None,
                    'independence_assumed': True,
                }

    # Result + Over/Under
    for ou in ['over_2_5', 'over_3_5']:
        over_p = get_prob(ou, 'Over')
        under_p = get_prob(ou, 'Under')
        if over_p is not None:
            line = ou.replace('over_', '').replace('_', '.')
            for key, label, prob in [('H', 'Home Win', home_prob), ('D', 'Draw', draw_prob), ('A', 'Away Win', away_prob)]:
                co = round(prob * over_p, 4)
                cu = round(prob * under_p, 4)
                combos[f'{key}_over_{ou[-3:]}'] = {
                    'description': f'{label} & Over {line}',
                    'probability': co,
                    'odds': round(1 / co, 2) if co > 0 else None,
                    'independence_assumed': True,
                }
                combos[f'{key}_under_{ou[-3:]}'] = {
                    'description': f'{label} & Under {line}',
                    'probability': cu,
                    'odds': round(1 / cu, 2) if cu > 0 else None,
                    'independence_assumed': True,
                }

    # BTTS + Over/Under
    if btts_yes is not None:
        for ou in ['over_2_5', 'over_3_5']:
            over_p = get_prob(ou, 'Over')
            under_p = get_prob(ou, 'Under')
            if over_p is not None:
                line = ou.replace('over_', '').replace('_', '.')
                cbo = round(btts_yes * over_p, 4)
                cbu = round(btts_yes * under_p, 4)
                combos[f'btts_yes_over_{ou[-3:]}'] = {
                    'description': f'Both Score & Over {line}',
                    'probability': cbo,
                    'odds': round(1 / cbo, 2) if cbo > 0 else None,
                    'independence_assumed': True,
                }
                combos[f'btts_yes_under_{ou[-3:]}'] = {
                    'description': f'Both Score & Under {line}',
                    'probability': cbu,
                    'odds': round(1 / cbu, 2) if cbu > 0 else None,
                    'independence_assumed': True,
                }

    return combos


def classify_match(prediction_data):
    """
    Add tags and category scores to a prediction using Elo-based logic.

    Tags:
        - high_confidence: predicted outcome has >= 60% probability
        - upset: predicted winner has LOWER Elo by 30+ (weaker team expected to win)
        - banker: predicted winner has >= 50% prob AND HIGHER Elo by 30+ (strong favorite)

    Args:
        prediction_data: dict from predict_all_markets()

    Returns:
        tuple: (list of tag strings, dict of category scores)
    """
    tags = []
    scores = {}

    result = prediction_data['match_result']
    confidence = result['confidence']
    outcome = result['outcome']

    # High confidence
    if confidence >= 0.60:
        tags.append('high_confidence')

    # Upset potential: the predicted winner has a LOWER Elo than the opponent
    # This means a genuinely weaker team is expected to win (not just "away team favored")
    elo = prediction_data.get('elo', {})
    home_elo = elo.get('home', 1500)
    away_elo = elo.get('away', 1500)

    if outcome == 'HOME_WIN' and home_elo < away_elo - 30:
        # Home team predicted to win but has significantly lower Elo
        tags.append('upset')
        scores['upset_potential'] = round(away_elo - home_elo, 1)
    elif outcome == 'AWAY_WIN' and away_elo < home_elo - 30:
        # Away team predicted to win but has significantly lower Elo
        tags.append('upset')
        scores['upset_potential'] = round(home_elo - away_elo, 1)
    else:
        scores['upset_potential'] = 0

    # Banker bet: predicted winner has high probability AND higher Elo (genuinely stronger team)
    predicted_prob = confidence  # confidence = probability of predicted outcome
    if outcome == 'HOME_WIN' and predicted_prob >= 0.50 and home_elo > away_elo + 30:
        tags.append('banker')
    elif outcome == 'AWAY_WIN' and predicted_prob >= 0.50 and away_elo > home_elo + 30:
        tags.append('banker')

    return tags, scores
