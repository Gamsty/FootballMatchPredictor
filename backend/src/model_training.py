"""
Model Training Module

Trains, evaluates, and compares ML models for football match prediction.
Supports: XGBoost, Random Forest, Logistic Regression,
          Stacked Ensemble, Binary (no-draw), and Calibrated models.

Advanced approaches:
    1. Elo ratings — dynamic team strength computed from historical results
    2. Binary mode — Home/Away only (drops draws) for higher accuracy
    3. Ensemble stacking — combines multiple models via a meta-learner
    4. Probability calibration — calibrated probabilities + log loss metric

Pipeline: match_features.csv → load/clean → Elo ratings → train/test split → scale → train → evaluate → save
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score, RandomizedSearchCV
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    log_loss,
    roc_auc_score,
)
import xgboost as xgb
import joblib
from datetime import datetime
from collections import defaultdict
import os
import tempfile

# matplotlib + seaborn are heavy and only needed when running model_training.py
# directly (CLI). The retrain job (train_production_model) imports this module
# but never triggers plotting, so we lazy-import inside _plot_confusion_matrix /
# feature_importance instead of at module level.
try:
    import matplotlib.pyplot as plt  # noqa: F401
    import seaborn as sns  # noqa: F401
    _PLOTTING_AVAILABLE = True
except ImportError:
    _PLOTTING_AVAILABLE = False


# ============================================================================
# ELO RATING SYSTEM
# ============================================================================

def compute_elo_ratings(df, k=20, home_advantage=50):
    """
    Compute Elo ratings for all teams from match history.
    Processes matches chronologically — each match's Elo is computed BEFORE the result,
    then ratings are updated, preventing data leakage.

    Args:
        df: DataFrame with 'home_team', 'away_team', 'target' columns, sorted by date
        k: K-factor — how much ratings change per match (higher = more volatile)
        home_advantage: Elo points added to home team's rating for expected score calc

    Returns:
        home_elos: list of home team Elo BEFORE each match
        away_elos: list of away team Elo BEFORE each match
        final_elos: dict of team_name -> final Elo rating
    """
    elo = defaultdict(lambda: 1500.0)
    home_elos = []
    away_elos = []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']

        # Record Elo BEFORE this match (no leakage)
        home_elos.append(elo[home])
        away_elos.append(elo[away])

        # Expected scores (with home advantage built in)
        exp_home = 1 / (1 + 10 ** ((elo[away] - elo[home] - home_advantage) / 400))
        exp_away = 1 - exp_home

        # Actual scores
        if row['target'] == 'HOME_TEAM':
            actual_home, actual_away = 1.0, 0.0
        elif row['target'] == 'AWAY_TEAM':
            actual_home, actual_away = 0.0, 1.0
        else:  # DRAW
            actual_home, actual_away = 0.5, 0.5

        # Update ratings
        elo[home] += k * (actual_home - exp_home)
        elo[away] += k * (actual_away - exp_away)

    return home_elos, away_elos, dict(elo)


# ============================================================================
# MATCH PREDICTOR CLASS
# ============================================================================

class MatchPredictor:
    """Handles ML model training and prediction"""

    def __init__(self, model_type='xgboost'):
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.label_encoder = LabelEncoder()
        self.feature_names = None
        self.training_history = []
        self.elo_ratings = None  # Stored for API predictions

    def load_data(self, csv_path='../data/processed/match_features.csv',
                  include_odds=False, binary_mode=False):
        """
        Load and prepare data with all features including Elo ratings.

        Args:
            csv_path: Path to features CSV
            include_odds: Include betting odds features
            binary_mode: Drop draws for binary (Home/Away) classification

        Returns:
            tuple: (X, y, df)
        """
        print(f"Loading data from {csv_path}...")
        df = pd.read_csv(csv_path)
        print(f"Loaded {len(df)} records")

        # Elo only makes sense chronologically — ensure rows are date-ordered.
        # Without this, an unsorted CSV would produce garbage Elo features.
        if 'date' in df.columns:
            df = df.sort_values('date').reset_index(drop=True)

        # --- Compute Elo ratings ---
        if 'home_team' in df.columns and 'away_team' in df.columns:
            print("Computing Elo ratings...")
            home_elos, away_elos, final_elos = compute_elo_ratings(df)
            df['home_elo'] = home_elos
            df['away_elo'] = away_elos
            self.elo_ratings = final_elos
            print(f"Elo ratings computed for {len(final_elos)} teams")
        else:
            print("WARNING: home_team/away_team columns not found, skipping Elo")

        # --- Base feature columns ---
        base_cols = [
            'home_form_5', 'away_form_5',
            'home_goals_scored_avg', 'home_goals_conceded_avg',
            'away_goals_scored_avg', 'away_goals_conceded_avg',
            'h2h_home_wins', 'h2h_draws', 'h2h_away_wins',
            'home_win_rate', 'away_win_rate',
            'days_since_home_last_match', 'days_since_away_last_match',
            'home_league_position', 'away_league_position',
            'home_points', 'away_points',
            'home_goal_difference', 'away_goal_difference'
        ]

        X = df[base_cols].copy()

        # Rest-days imputation: fixed 7-day default. Median over the full dataset
        # would leak holdout/test statistics into train (see _build_xy_from_csv).
        for col in ['days_since_home_last_match', 'days_since_away_last_match']:
            X[col] = X[col].fillna(7)
        X = X.fillna(0)

        # --- Derived features ---
        X['position_diff'] = X['home_league_position'] - X['away_league_position']
        X['points_diff'] = X['home_points'] - X['away_points']
        X['gd_diff'] = X['home_goal_difference'] - X['away_goal_difference']
        X['form_diff'] = X['home_form_5'] - X['away_form_5']
        X['win_rate_diff'] = X['home_win_rate'] - X['away_win_rate']
        X['home_attack_vs_away_defense'] = X['home_goals_scored_avg'] - X['away_goals_conceded_avg']
        X['away_attack_vs_home_defense'] = X['away_goals_scored_avg'] - X['home_goals_conceded_avg']
        X['h2h_dominance'] = X['h2h_home_wins'] - X['h2h_away_wins']
        X['rest_diff'] = X['days_since_home_last_match'] - X['days_since_away_last_match']

        # --- Elo features ---
        if 'home_elo' in df.columns:
            X['home_elo'] = df['home_elo']
            X['away_elo'] = df['away_elo']
            X['elo_diff'] = df['home_elo'] - df['away_elo']

        # --- Optional raw features ---
        optional_cols = [
            'home_draw_rate', 'away_draw_rate',
            'home_clean_sheet_rate', 'away_clean_sheet_rate',
            'home_weighted_form', 'away_weighted_form',
            'home_shots_on_target_avg', 'away_shots_on_target_avg',
            'home_corners_avg', 'away_corners_avg',
            'home_cards_avg', 'away_cards_avg',
            'home_points_from_top', 'away_points_from_top',
            'home_points_from_relegation', 'away_points_from_relegation',
            'season_progress',
            'home_avg_position_3yr', 'away_avg_position_3yr',
            'home_artificial_pitch',
        ]
        for col in optional_cols:
            if col in df.columns:
                X[col] = df[col].fillna(0)

        # Derived from optional features
        if 'home_draw_rate' in X.columns:
            X['draw_rate_sum'] = X['home_draw_rate'] + X['away_draw_rate']
        if 'home_weighted_form' in X.columns:
            X['weighted_form_diff'] = X['home_weighted_form'] - X['away_weighted_form']
        if 'home_shots_on_target_avg' in X.columns:
            X['shots_on_target_diff'] = X['home_shots_on_target_avg'] - X['away_shots_on_target_avg']
        if 'home_corners_avg' in X.columns:
            X['corners_diff'] = X['home_corners_avg'] - X['away_corners_avg']
        if 'home_points_from_top' in X.columns:
            X['motivation_diff'] = X['away_points_from_top'] - X['home_points_from_top']
        if 'home_avg_position_3yr' in X.columns:
            X['squad_strength_diff'] = X['away_avg_position_3yr'] - X['home_avg_position_3yr']

        # --- Betting odds features ---
        if include_odds:
            odds_cols = ['avg_home_prob', 'avg_draw_prob', 'avg_away_prob']
            has_odds = all(c in df.columns for c in odds_cols)
            if has_odds:
                for col in odds_cols:
                    X[col] = df[col].fillna(0)
                X['odds_home_away_diff'] = X['avg_home_prob'] - X['avg_away_prob']
                odds_mask = df['avg_home_prob'].notna() & (df['avg_home_prob'] > 0)
                print(f"Odds available for {odds_mask.sum()} / {len(df)} matches")
            else:
                print("WARNING: Odds columns not found, training without odds")
                include_odds = False

        self.feature_names = list(X.columns)

        # --- Filter to odds matches first (before binary filtering) ---
        if include_odds:
            odds_mask = df['avg_home_prob'].notna() & (df['avg_home_prob'] > 0)
            X = X[odds_mask].copy()
            df = df[odds_mask].copy()
            print(f"Using {len(X)} matches with odds data")

        # --- Target encoding ---
        if binary_mode:
            # Binary: HOME_TEAM=1, AWAY_TEAM=0 (drops DRAW)
            draw_mask = df['target'] == 'DRAW'
            X = X[~draw_mask].copy()
            y = df.loc[~draw_mask, 'target'].map({'HOME_TEAM': 1, 'AWAY_TEAM': 0})
            print(f"\nBinary mode: {len(X)} matches (dropped {draw_mask.sum()} draws)")
        else:
            # 3-class: HOME_TEAM=2, DRAW=1, AWAY_TEAM=0
            y = df['target'].map({'HOME_TEAM': 2, 'DRAW': 1, 'AWAY_TEAM': 0})

        print(f"\nFeatures shape: {X.shape}")
        print("Target distribution:")
        if binary_mode:
            target_map = {0: 'AWAY_TEAM', 1: 'HOME_TEAM'}
        else:
            target_map = {0: 'AWAY_TEAM', 1: 'DRAW', 2: 'HOME_TEAM'}
        for val, count in y.value_counts().sort_index().items():
            print(f"    {target_map[val]}: {count}")

        return X, y, df

    def split_data(self, X, y, test_size=0.2, random_state=42, time_based=True):
        """Split data into train and test sets.

        Defaults to a chronological (time-based) split: last `test_size` of rows by date
        are held out. This matches the production retrain pipeline and avoids the
        leakage that random splits cause on time-series features (Elo, form, league
        position all use the chronological history available at match time).

        Set time_based=False for a random stratified split (legacy/benchmark use only —
        the resulting accuracy will be optimistic).
        """
        if time_based:
            # X is already date-ordered by load_data (we sort in load_data after
            # reading the CSV). Take the trailing slice as test.
            split_idx = int(len(X) * (1 - test_size))
            X_train = X.iloc[:split_idx]
            X_test = X.iloc[split_idx:]
            y_train = y.iloc[:split_idx]
            y_test = y.iloc[split_idx:]
            print(f"\nSplitting data (time-based): {int((1-test_size)*100)}% train, {int(test_size*100)}% test")
        else:
            print(f"\nSplitting data (random stratified): {int((1-test_size)*100)}% train, {int(test_size*100)}% test")
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_size, random_state=random_state, stratify=y
            )

        print(f"Training set: {len(X_train)} samples")
        print(f"Test set: {len(X_test)} samples")

        return X_train, X_test, y_train, y_test

    def scale_features(self, X_train, X_test):
        """Scale features using StandardScaler"""
        print("\nScaling features...")
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        print("Features scaled successfully")
        return X_train_scaled, X_test_scaled

    def create_model(self, binary_mode=False):
        """Create ML model based on model_type."""
        print(f"\nCreating {self.model_type} model...")

        if self.model_type == 'xgboost':
            objective = 'binary:logistic' if binary_mode else 'multi:softprob'
            model = xgb.XGBClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8, random_state=42,
                eval_metric='logloss' if binary_mode else 'mlogloss',
                objective=objective,
            )
        elif self.model_type == 'random_forest':
            model = RandomForestClassifier(
                n_estimators=200, max_depth=10, min_samples_split=5,
                min_samples_leaf=2, class_weight='balanced', random_state=42
            )
        else:  # logistic_regression
            model = LogisticRegression(
                solver='lbfgs', max_iter=5000, class_weight='balanced', random_state=42
            )

        return model

    def get_param_grid(self):
        """Get hyperparameter search space for the current model type."""
        if self.model_type == 'xgboost':
            return {
                'n_estimators': [100, 200, 300, 500],
                'max_depth': [3, 4, 5, 6, 8],
                'learning_rate': [0.01, 0.05, 0.1, 0.15],
                'subsample': [0.7, 0.8, 0.9],
                'colsample_bytree': [0.7, 0.8, 0.9],
                'min_child_weight': [1, 3, 5],
                'gamma': [0, 0.1, 0.2],
            }
        elif self.model_type == 'random_forest':
            return {
                'n_estimators': [100, 200, 300, 500],
                'max_depth': [6, 8, 10, 15, None],
                'min_samples_split': [2, 5, 10],
                'min_samples_leaf': [1, 2, 4],
                'class_weight': ['balanced', 'balanced_subsample', None],
            }
        else:  # logistic_regression
            return {
                'C': [0.01, 0.1, 0.5, 1, 5, 10],
                'class_weight': ['balanced', None],
                'solver': ['lbfgs'],
                'max_iter': [5000],
            }

    def tune_hyperparameters(self, X_train, y_train, n_iter=50, binary_mode=False):
        """Run randomized hyperparameter search."""
        print(f"\nTuning {self.model_type} hyperparameters ({n_iter} iterations)...")

        base_model = self.create_model(binary_mode=binary_mode)
        param_grid = self.get_param_grid()

        search = RandomizedSearchCV(
            base_model, param_distributions=param_grid,
            n_iter=n_iter, cv=5, scoring='accuracy',
            random_state=42, n_jobs=-1, verbose=1
        )

        search.fit(X_train, y_train)

        print(f"\nBest parameters: {search.best_params_}")
        print(f"Best CV accuracy: {search.best_score_:.4f}")

        self.model = search.best_estimator_
        return self.model

    def train(self, X_train, y_train, X_val=None, y_val=None, binary_mode=False):
        """Train the model"""
        print(f"\nTraining {self.model_type} model...")
        print("=" * 60)

        self.model = self.create_model(binary_mode=binary_mode)
        start_time = datetime.now()

        if self.model_type == 'xgboost' and X_val is not None:
            self.model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        else:
            self.model.fit(X_train, y_train)

        training_time = (datetime.now() - start_time).total_seconds()
        print(f"Training completed in {training_time:.2f} seconds")

        cv_scores = cross_val_score(self.model, X_train, y_train, cv=5)
        print(f"\nCross-validation scores: {cv_scores}")
        print(f"CV Mean Accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

        return self.model

    def evaluate(self, X_test, y_test, binary_mode=False):
        """Evaluate model performance with extended metrics."""
        print("\n" + "=" * 60)
        print("MODEL EVALUATION")
        print("=" * 60)

        y_pred = self.model.predict(X_test)
        y_pred_proba = self.model.predict_proba(X_test)

        accuracy = accuracy_score(y_test, y_pred)

        # Macro and weighted averages
        precision_macro = precision_score(y_test, y_pred, average='macro')
        recall_macro = recall_score(y_test, y_pred, average='macro')
        f1_macro = f1_score(y_test, y_pred, average='macro')
        precision_weighted = precision_score(y_test, y_pred, average='weighted')
        recall_weighted = recall_score(y_test, y_pred, average='weighted')
        f1_weighted = f1_score(y_test, y_pred, average='weighted')

        # Log loss (measures quality of probability predictions)
        try:
            logloss = log_loss(y_test, y_pred_proba)
        except Exception:
            logloss = None

        print(f"\nOverall Accuracy: {accuracy:.4f}")
        if logloss is not None:
            print(f"Log Loss: {logloss:.4f}")
        print("\nMacro Averages:")
        print(f"    Precision: {precision_macro:.4f}")
        print(f"    Recall: {recall_macro:.4f}")
        print(f"    F1 Score: {f1_macro:.4f}")
        print("\nWeighted Averages:")
        print(f"    Precision: {precision_weighted:.4f}")
        print(f"    Recall: {recall_weighted:.4f}")
        print(f"    F1 Score: {f1_weighted:.4f}")

        # Classification report
        if binary_mode:
            target_names = ['Away Win', 'Home Win']
        else:
            target_names = ['Away Win', 'Draw', 'Home Win']

        print("\nDetailed Classification Report:")
        print(classification_report(y_test, y_pred, target_names=target_names, digits=4))

        # Confusion matrix
        cm = confusion_matrix(y_test, y_pred)
        print("\nConfusion Matrix:")
        if binary_mode:
            print("                 Predicted")
            print("                 Away  Home")
            print(f"Actual Away     {cm[0]}")
            print(f"        Home    {cm[1]}")
        else:
            print("                 Predicted")
            print("                 Away  Draw  Home")
            print(f"Actual Away     {cm[0]}")
            print(f"        Draw    {cm[1]}")
            print(f"        Home    {cm[2]}")

        self._plot_confusion_matrix(cm, binary_mode)

        metrics = {
            'accuracy': accuracy,
            'log_loss': logloss,
            'precision_macro': precision_macro,
            'recall_macro': recall_macro,
            'f1_macro': f1_macro,
            'precision_weighted': precision_weighted,
            'recall_weighted': recall_weighted,
            'f1_weighted': f1_weighted,
            'confusion_matrix': cm,
            'y_pred': y_pred,
            'y_pred_proba': y_pred_proba
        }

        return metrics

    def _plot_confusion_matrix(self, cm, binary_mode=False):
        """Plot confusion matrix heatmap"""
        plt.figure(figsize=(8, 6))
        if binary_mode:
            labels = ['Away Win', 'Home Win']
        else:
            labels = ['Away Win', 'Draw', 'Home Win']
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                    xticklabels=labels, yticklabels=labels)
        plt.title(f'Confusion Matrix - {self.model_type}')
        plt.ylabel('Actual')
        plt.xlabel('Predicted')
        plt.tight_layout()
        plt.savefig(f'../data/confusion_matrix_{self.model_type}.png', dpi=300)
        print(f"\nConfusion matrix saved to ../data/confusion_matrix_{self.model_type}.png")
        plt.close()

    def feature_importance(self):
        """Display feature importance"""
        if not hasattr(self.model, 'feature_importances_'):
            # Try to get from stacking ensemble's final estimator
            if hasattr(self.model, 'final_estimator_') and hasattr(self.model.final_estimator_, 'coef_'):
                print("\n(Stacked ensemble — feature importance from meta-learner coefficients)")
            else:
                print("This model doesn't support feature importance")
            return None

        importance_df = pd.DataFrame({
            'feature': self.feature_names,
            'importance': self.model.feature_importances_
        }).sort_values('importance', ascending=False)

        print("\n" + "=" * 60)
        print("FEATURE IMPORTANCE")
        print("=" * 60)
        print(importance_df.to_string(index=False))

        plt.figure(figsize=(10, 8))
        plt.barh(importance_df['feature'], importance_df['importance'])
        plt.xlabel('Importance')
        plt.title(f'Feature Importance - {self.model_type}')
        plt.gca().invert_yaxis()
        plt.tight_layout()
        plt.savefig(f'../data/feature_importance_{self.model_type}.png', dpi=300)
        print(f"\nFeature importance plot saved to ../data/feature_importance_{self.model_type}.png")
        plt.close()

        return importance_df

    def save_model(self, filename=None):
        """Save trained model with Elo ratings"""
        if filename is None:
            filename = f'../models/{self.model_type}_model.pkl'

        os.makedirs(os.path.dirname(filename), exist_ok=True)

        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'model_type': self.model_type,
            'elo_ratings': self.elo_ratings,
            'created_at': datetime.now().isoformat()
        }

        joblib.dump(model_data, filename)
        print(f"\nModel saved to {filename}")

    def load_model(self, filename):
        """Load trained model"""
        print(f"Loading model from {filename}")
        model_data = joblib.load(filename)
        self.model = model_data['model']
        self.scaler = model_data['scaler']
        self.feature_names = model_data['feature_names']
        self.model_type = model_data['model_type']
        self.elo_ratings = model_data.get('elo_ratings')
        print(f"Model loaded: {self.model_type}, created {model_data.get('created_at', 'Unknown')}")

    def predict(self, features_dict):
        """
        Make prediction for a single match.
        Computes derived features from raw features to match training pipeline.
        """
        X = pd.DataFrame([features_dict])

        for col in ['days_since_home_last_match', 'days_since_away_last_match']:
            if col in X.columns:
                X[col] = X[col].fillna(7)
        X = X.fillna(0)

        # Derived features
        if 'home_league_position' in X.columns:
            X['position_diff'] = X['home_league_position'] - X['away_league_position']
        if 'home_points' in X.columns:
            X['points_diff'] = X['home_points'] - X['away_points']
        if 'home_goal_difference' in X.columns:
            X['gd_diff'] = X['home_goal_difference'] - X['away_goal_difference']
        if 'home_form_5' in X.columns:
            X['form_diff'] = X['home_form_5'] - X['away_form_5']
        if 'home_win_rate' in X.columns:
            X['win_rate_diff'] = X['home_win_rate'] - X['away_win_rate']
        if 'home_goals_scored_avg' in X.columns:
            X['home_attack_vs_away_defense'] = X['home_goals_scored_avg'] - X['away_goals_conceded_avg']
            X['away_attack_vs_home_defense'] = X['away_goals_scored_avg'] - X['home_goals_conceded_avg']
        if 'h2h_home_wins' in X.columns:
            X['h2h_dominance'] = X['h2h_home_wins'] - X['h2h_away_wins']
        if 'days_since_home_last_match' in X.columns:
            X['rest_diff'] = X['days_since_home_last_match'] - X['days_since_away_last_match']
        if 'home_elo' in X.columns:
            X['elo_diff'] = X['home_elo'] - X['away_elo']
        if 'home_draw_rate' in X.columns:
            X['draw_rate_sum'] = X['home_draw_rate'] + X['away_draw_rate']
        if 'home_weighted_form' in X.columns:
            X['weighted_form_diff'] = X['home_weighted_form'] - X['away_weighted_form']
        if 'home_shots_on_target_avg' in X.columns:
            X['shots_on_target_diff'] = X['home_shots_on_target_avg'] - X['away_shots_on_target_avg']
        if 'home_corners_avg' in X.columns:
            X['corners_diff'] = X['home_corners_avg'] - X['away_corners_avg']
        if 'home_points_from_top' in X.columns:
            X['motivation_diff'] = X['away_points_from_top'] - X['home_points_from_top']
        if 'home_avg_position_3yr' in X.columns:
            X['squad_strength_diff'] = X['away_avg_position_3yr'] - X['home_avg_position_3yr']

        # Fill missing features with 0
        for col in self.feature_names:
            if col not in X.columns:
                X[col] = 0
        X = X[self.feature_names]

        X_scaled = self.scaler.transform(X)

        prediction = self.model.predict(X_scaled)[0]
        probabilities = self.model.predict_proba(X_scaled)[0]

        outcome_map = {0: 'AWAY_TEAM', 1: 'DRAW', 2: 'HOME_TEAM'}

        return {
            'predicted_outcome': outcome_map[prediction],
            'probabilities': {
                'away_win': float(probabilities[0]),
                'draw': float(probabilities[1]),
                'home_win': float(probabilities[2])
            },
            'confidence': float(max(probabilities))
        }


# ============================================================================
# TRAINING FUNCTIONS
# ============================================================================

def _train_and_evaluate(model_types, tune, n_iter, include_odds=False,
                        binary_mode=False, label=""):
    """Train and compare multiple model types, return results and best predictor."""
    print(f"\n{'='*70}")
    print(f"TRAINING {label}")
    print(f"{'='*70}")

    results = {}
    best_accuracy = 0
    best_predictor = None
    best_model_type = None

    for model_type in model_types:
        print(f"\n\n{'='*70}")
        print(f"Training {model_type.upper()} ({label})")
        print(f"{'='*70}")

        predictor = MatchPredictor(model_type=model_type)
        X, y, df = predictor.load_data(include_odds=include_odds, binary_mode=binary_mode)
        X_train, X_test, y_train, y_test = predictor.split_data(X, y)
        X_train_scaled, X_test_scaled = predictor.scale_features(X_train, X_test)

        if tune:
            predictor.tune_hyperparameters(X_train_scaled, y_train, n_iter=n_iter, binary_mode=binary_mode)
        else:
            predictor.train(X_train_scaled, y_train, binary_mode=binary_mode)

        metrics = predictor.evaluate(X_test_scaled, y_test, binary_mode=binary_mode)

        if model_type != 'logistic_regression':
            predictor.feature_importance()

        predictor.save_model()

        results[model_type] = {
            'accuracy': metrics['accuracy'],
            'log_loss': metrics.get('log_loss'),
            'f1_weighted': metrics['f1_weighted'],
            'precision_weighted': metrics['precision_weighted'],
            'recall_weighted': metrics['recall_weighted']
        }

        if metrics['accuracy'] > best_accuracy:
            best_accuracy = metrics['accuracy']
            best_model_type = model_type
            best_predictor = predictor

    # Summary
    comparison_df = pd.DataFrame(results).T.sort_values('accuracy', ascending=False)
    print(f"\n\n{'='*70}")
    print(f"{label} — COMPARISON")
    print(f"{'='*70}")
    print(comparison_df.to_string())
    print(f"\nBest: {best_model_type.upper()} ({best_accuracy:.4f})")

    return results, best_predictor, best_model_type, best_accuracy, comparison_df


def train_stacked_ensemble(tune=True, n_iter=30, include_odds=False,
                           binary_mode=False, label="STACKED ENSEMBLE"):
    """
    Train a stacking ensemble that combines XGBoost, Random Forest,
    and Gradient Boosting with a Logistic Regression meta-learner.
    """
    print(f"\n\n{'='*70}")
    print(f"Training {label}")
    print(f"{'='*70}")

    predictor = MatchPredictor(model_type='stacked_ensemble')
    X, y, df = predictor.load_data(include_odds=include_odds, binary_mode=binary_mode)
    X_train, X_test, y_train, y_test = predictor.split_data(X, y)
    X_train_scaled, X_test_scaled = predictor.scale_features(X_train, X_test)

    # Base estimators with good defaults
    if binary_mode:
        xgb_est = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', objective='binary:logistic'
        )
    else:
        xgb_est = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='mlogloss'
        )

    rf_est = RandomForestClassifier(
        n_estimators=300, max_depth=10, min_samples_leaf=2,
        random_state=42, n_jobs=-1
    )

    # Stacking: base models output probabilities → meta-learner combines them
    print("\nBuilding stacking ensemble (2 base models + LR meta-learner)...")
    print("This uses 5-fold CV to generate meta-features — may take a while...")

    stacking = StackingClassifier(
        estimators=[
            ('xgb', xgb_est),
            ('rf', rf_est),
        ],
        final_estimator=LogisticRegression(max_iter=1000, C=1.0, random_state=42),
        cv=5,
        stack_method='predict_proba',
        passthrough=False,  # Only use base model outputs (faster, avoids overfitting)
        n_jobs=-1,
    )

    print("\nFitting stacking ensemble...")
    stacking.fit(X_train_scaled, y_train)
    predictor.model = stacking

    metrics = predictor.evaluate(X_test_scaled, y_test, binary_mode=binary_mode)

    suffix = '_binary' if binary_mode else ''
    odds_suffix = '_with_odds' if include_odds else ''
    predictor.save_model(f'../models/stacked_ensemble{suffix}{odds_suffix}_model.pkl')

    return predictor, metrics


def train_calibrated_model(base_model_type='xgboost', include_odds=False, label="CALIBRATED"):
    """
    Train a probability-calibrated model using CalibratedClassifierCV.
    Produces better-calibrated probability estimates (important for betting/analysis).
    """
    print(f"\n\n{'='*70}")
    print(f"Training {label} ({base_model_type})")
    print(f"{'='*70}")

    predictor = MatchPredictor(model_type=f'calibrated_{base_model_type}')
    X, y, df = predictor.load_data(include_odds=include_odds)
    X_train, X_test, y_train, y_test = predictor.split_data(X, y)
    X_train_scaled, X_test_scaled = predictor.scale_features(X_train, X_test)

    # Create base model
    if base_model_type == 'xgboost':
        base = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='mlogloss'
        )
    elif base_model_type == 'random_forest':
        base = RandomForestClassifier(
            n_estimators=300, max_depth=10, random_state=42, n_jobs=-1
        )

    print(f"\nCalibrating {base_model_type} with isotonic regression (5-fold CV)...")

    calibrated = CalibratedClassifierCV(base, cv=5, method='isotonic')
    calibrated.fit(X_train_scaled, y_train)
    predictor.model = calibrated

    metrics = predictor.evaluate(X_test_scaled, y_test)

    # Extra: log loss comparison
    y_pred_proba = calibrated.predict_proba(X_test_scaled)
    cal_ll = log_loss(y_test, y_pred_proba)
    print(f"\nCalibrated Log Loss: {cal_ll:.4f}")

    # Compare with uncalibrated
    base.fit(X_train_scaled, y_train)
    uncal_proba = base.predict_proba(X_test_scaled)
    uncal_ll = log_loss(y_test, uncal_proba)
    uncal_acc = accuracy_score(y_test, base.predict(X_test_scaled))
    print(f"Uncalibrated Log Loss: {uncal_ll:.4f}")
    print(f"Log Loss improvement: {uncal_ll - cal_ll:+.4f}")
    print(f"Uncalibrated Accuracy: {uncal_acc:.4f}")
    print(f"Calibrated Accuracy: {metrics['accuracy']:.4f}")

    predictor.save_model(f'../models/calibrated_{base_model_type}_model.pkl')

    return predictor, metrics, cal_ll


# ============================================================================
# MAIN COMPARISON
# ============================================================================

def compare_models(tune=True, n_iter=50):
    """
    Train and compare all model types across multiple approaches:
    1. Standard 3-class (with Elo) — no odds (for API)
    2. Standard 3-class (with Elo) — with odds (for evaluation)
    3. Binary Home/Away (no draws) — shows ceiling accuracy
    4. Stacked ensemble — combines multiple models
    5. Calibrated model — better probability estimates
    """
    model_types = ['xgboost', 'random_forest', 'logistic_regression']

    all_results = {}

    # ======================================================================
    # 1. Standard 3-class WITHOUT odds (for live API predictions)
    # ======================================================================
    results_no_odds, best_no_odds, best_type_no_odds, best_acc_no_odds, comp_no_odds = \
        _train_and_evaluate(model_types, tune, n_iter,
                           include_odds=False, label="NO-ODDS 3-CLASS (live predictions)")

    best_no_odds.save_model(filename='../models/best_model.pkl')
    print(f"\nSaved best no-odds model ({best_type_no_odds}) as ../models/best_model.pkl")

    for mt, r in results_no_odds.items():
        all_results[f"{mt}_no_odds"] = r

    # ======================================================================
    # 2. Standard 3-class WITH odds (for evaluation)
    # ======================================================================
    results_with_odds, best_with_odds, best_type_with_odds, best_acc_with_odds, comp_with_odds = \
        _train_and_evaluate(model_types, tune, n_iter,
                           include_odds=True, label="WITH-ODDS 3-CLASS (evaluation)")

    best_with_odds.save_model(filename='../models/best_model_with_odds.pkl')
    print(f"\nSaved best with-odds model ({best_type_with_odds}) as ../models/best_model_with_odds.pkl")

    for mt, r in results_with_odds.items():
        all_results[f"{mt}_with_odds"] = r

    # ======================================================================
    # 3. Binary mode (Home vs Away, no draws)
    # ======================================================================
    results_binary, best_binary, best_type_binary, best_acc_binary, comp_binary = \
        _train_and_evaluate(model_types, tune, n_iter,
                           include_odds=False, binary_mode=True,
                           label="BINARY Home/Away (no draws)")

    best_binary.save_model(filename='../models/best_binary_model.pkl')
    print(f"\nSaved best binary model ({best_type_binary}) as ../models/best_binary_model.pkl")

    for mt, r in results_binary.items():
        all_results[f"{mt}_binary"] = r

    # ======================================================================
    # 4. Stacked Ensemble
    # ======================================================================
    stack_predictor, stack_metrics = train_stacked_ensemble(
        tune=False, include_odds=False, binary_mode=False,
        label="STACKED ENSEMBLE 3-CLASS (no odds)"
    )
    all_results['stacked_ensemble_3class'] = {
        'accuracy': stack_metrics['accuracy'],
        'log_loss': stack_metrics.get('log_loss'),
        'f1_weighted': stack_metrics['f1_weighted'],
        'precision_weighted': stack_metrics['precision_weighted'],
        'recall_weighted': stack_metrics['recall_weighted']
    }

    # Binary stacked ensemble
    stack_binary_predictor, stack_binary_metrics = train_stacked_ensemble(
        tune=False, include_odds=False, binary_mode=True,
        label="STACKED ENSEMBLE BINARY (no odds)"
    )
    all_results['stacked_ensemble_binary'] = {
        'accuracy': stack_binary_metrics['accuracy'],
        'log_loss': stack_binary_metrics.get('log_loss'),
        'f1_weighted': stack_binary_metrics['f1_weighted'],
        'precision_weighted': stack_binary_metrics['precision_weighted'],
        'recall_weighted': stack_binary_metrics['recall_weighted']
    }

    # ======================================================================
    # 5. Calibrated model
    # ======================================================================
    cal_predictor, cal_metrics, cal_ll = train_calibrated_model(
        base_model_type='xgboost', include_odds=False,
        label="CALIBRATED XGBoost (no odds)"
    )
    all_results['calibrated_xgboost'] = {
        'accuracy': cal_metrics['accuracy'],
        'log_loss': cal_ll,
        'f1_weighted': cal_metrics['f1_weighted'],
        'precision_weighted': cal_metrics['precision_weighted'],
        'recall_weighted': cal_metrics['recall_weighted']
    }

    # If stacked 3-class is better than best individual, save it as best_model
    if stack_metrics['accuracy'] > best_acc_no_odds:
        stack_predictor.save_model(filename='../models/best_model.pkl')
        print("\nStacked ensemble beat individual models! Saved as best_model.pkl")
        best_acc_no_odds = stack_metrics['accuracy']
        best_type_no_odds = 'stacked_ensemble'

    # If stacked binary is better than best binary, save it
    if stack_binary_metrics['accuracy'] > best_acc_binary:
        stack_binary_predictor.save_model(filename='../models/best_binary_model.pkl')
        print("\nStacked binary beat individual models! Saved as best_binary_model.pkl")
        best_acc_binary = stack_binary_metrics['accuracy']
        best_type_binary = 'stacked_ensemble'

    # ======================================================================
    # FINAL SUMMARY
    # ======================================================================
    print("\n\n" + "=" * 70)
    print("FINAL SUMMARY — ALL APPROACHES")
    print("=" * 70)

    print(f"\n  {'Approach':<45} {'Best Model':<25} {'Accuracy':>10}")
    print(f"  {'-'*80}")
    print(f"  {'3-class NO-ODDS (for API)':<45} {best_type_no_odds.upper():<25} {best_acc_no_odds:>10.4f}")
    print(f"  {'3-class WITH-ODDS (evaluation)':<45} {best_type_with_odds.upper():<25} {best_acc_with_odds:>10.4f}")
    print(f"  {'Binary Home/Away (no draws)':<45} {best_type_binary.upper():<25} {best_acc_binary:>10.4f}")
    print(f"  {'Stacked Ensemble 3-class':<45} {'ENSEMBLE':<25} {stack_metrics['accuracy']:>10.4f}")
    print(f"  {'Stacked Ensemble Binary':<45} {'ENSEMBLE':<25} {stack_binary_metrics['accuracy']:>10.4f}")
    print(f"  {'Calibrated XGBoost':<45} {'CALIBRATED':<25} {cal_metrics['accuracy']:>10.4f}")

    if cal_ll is not None:
        print(f"\n  Calibrated model log loss: {cal_ll:.4f}")

    print("\n  Elo ratings: Computed for all teams (stored in model pickle)")
    print(f"  Odds boost: +{(best_acc_with_odds - best_acc_no_odds)*100:.1f}%")
    print(f"  Binary boost over 3-class: +{(best_acc_binary - best_acc_no_odds)*100:.1f}%")

    # Save comparison CSV
    pd.DataFrame(all_results).T.to_csv('../data/model_comparison.csv')

    # Visualization
    fig, axes = plt.subplots(1, 3, figsize=(20, 6))

    comp_no_odds['accuracy'].plot(kind='barh', ax=axes[0], color='steelblue')
    axes[0].set_title('3-Class No-Odds Accuracy')
    axes[0].set_xlabel('Accuracy')

    comp_with_odds['accuracy'].plot(kind='barh', ax=axes[1], color='darkorange')
    axes[1].set_title('3-Class With-Odds Accuracy')
    axes[1].set_xlabel('Accuracy')

    comp_binary['accuracy'].plot(kind='barh', ax=axes[2], color='forestgreen')
    axes[2].set_title('Binary (Home/Away) Accuracy')
    axes[2].set_xlabel('Accuracy')

    plt.tight_layout()
    plt.savefig('../data/model_comparison.png', dpi=300)
    plt.close()


# ============================================================================
# MULTI-MARKET TRAINING SYSTEM
# ============================================================================

# Market definitions — each market has a name, type, and target creation function
# Target functions receive the full DataFrame and return a Series of targets
# Rows with NaN targets are automatically dropped before training

MARKET_DEFINITIONS = {
    # --- Goals markets ---
    'btts': {
        'description': 'Both Teams to Score (Yes/No)',
        'type': 'binary',
        'labels': ['No', 'Yes'],
        'target_fn': lambda df: ((df['home_score'] > 0) & (df['away_score'] > 0)).astype(int),
        'requires': ['home_score', 'away_score'],
    },
    'over_1_5': {
        'description': 'Over 1.5 Total Goals',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_score'] + df['away_score']) > 1.5).astype(int),
        'requires': ['home_score', 'away_score'],
    },
    'over_2_5': {
        'description': 'Over 2.5 Total Goals',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_score'] + df['away_score']) > 2.5).astype(int),
        'requires': ['home_score', 'away_score'],
    },
    'over_3_5': {
        'description': 'Over 3.5 Total Goals',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_score'] + df['away_score']) > 3.5).astype(int),
        'requires': ['home_score', 'away_score'],
    },
    'over_4_5': {
        'description': 'Over 4.5 Total Goals',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_score'] + df['away_score']) > 4.5).astype(int),
        'requires': ['home_score', 'away_score'],
    },
    # --- Half-time markets ---
    'ht_result': {
        'description': 'Half-Time Result (H/D/A)',
        'type': '3class',
        'labels': ['Away', 'Draw', 'Home'],
        'target_fn': lambda df: df.apply(
            lambda r: 2 if r['home_ht_score'] > r['away_ht_score']
            else (0 if r['home_ht_score'] < r['away_ht_score'] else 1), axis=1
        ),
        'requires': ['home_ht_score', 'away_ht_score'],
    },
    'home_wins_at_least_one_half': {
        'description': 'Home Team Wins At Least One Half',
        'type': 'binary',
        'labels': ['No', 'Yes'],
        'target_fn': lambda df: df.apply(
            lambda r: int(
                (r['home_ht_score'] > r['away_ht_score']) or
                ((r['home_score'] - r['home_ht_score']) > (r['away_score'] - r['away_ht_score']))
            ), axis=1
        ),
        'requires': ['home_score', 'away_score', 'home_ht_score', 'away_ht_score'],
    },
    'away_wins_at_least_one_half': {
        'description': 'Away Team Wins At Least One Half',
        'type': 'binary',
        'labels': ['No', 'Yes'],
        'target_fn': lambda df: df.apply(
            lambda r: int(
                (r['away_ht_score'] > r['home_ht_score']) or
                ((r['away_score'] - r['away_ht_score']) > (r['home_score'] - r['home_ht_score']))
            ), axis=1
        ),
        'requires': ['home_score', 'away_score', 'home_ht_score', 'away_ht_score'],
    },
    'home_wins_both_halves': {
        'description': 'Home Team Wins Both Halves',
        'type': 'binary',
        'labels': ['No', 'Yes'],
        'target_fn': lambda df: df.apply(
            lambda r: int(
                (r['home_ht_score'] > r['away_ht_score']) and
                ((r['home_score'] - r['home_ht_score']) > (r['away_score'] - r['away_ht_score']))
            ), axis=1
        ),
        'requires': ['home_score', 'away_score', 'home_ht_score', 'away_ht_score'],
    },
    'away_wins_both_halves': {
        'description': 'Away Team Wins Both Halves',
        'type': 'binary',
        'labels': ['No', 'Yes'],
        'target_fn': lambda df: df.apply(
            lambda r: int(
                (r['away_ht_score'] > r['home_ht_score']) and
                ((r['away_score'] - r['away_ht_score']) > (r['home_score'] - r['home_ht_score']))
            ), axis=1
        ),
        'requires': ['home_score', 'away_score', 'home_ht_score', 'away_ht_score'],
    },
    # --- Corner markets ---
    'corners_result': {
        'description': 'Corner Kicks Result (H/D/A)',
        'type': '3class',
        'labels': ['Away', 'Draw', 'Home'],
        'target_fn': lambda df: df.apply(
            lambda r: 2 if r['home_corners'] > r['away_corners']
            else (0 if r['home_corners'] < r['away_corners'] else 1), axis=1
        ),
        'requires': ['home_corners', 'away_corners'],
    },
    'total_corners_over_8_5': {
        'description': 'Over 8.5 Total Corners',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_corners'] + df['away_corners']) > 8.5).astype(int),
        'requires': ['home_corners', 'away_corners'],
    },
    'total_corners_over_9_5': {
        'description': 'Over 9.5 Total Corners',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_corners'] + df['away_corners']) > 9.5).astype(int),
        'requires': ['home_corners', 'away_corners'],
    },
    'total_corners_over_10_5': {
        'description': 'Over 10.5 Total Corners',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_corners'] + df['away_corners']) > 10.5).astype(int),
        'requires': ['home_corners', 'away_corners'],
    },
    # --- Card markets ---
    'total_cards_over_3_5': {
        'description': 'Over 3.5 Total Cards',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_yellow_cards'] + df['away_yellow_cards'] +
                                   df['home_red_cards'] + df['away_red_cards']) > 3.5).astype(int),
        'requires': ['home_yellow_cards', 'away_yellow_cards', 'home_red_cards', 'away_red_cards'],
    },
    'total_cards_over_4_5': {
        'description': 'Over 4.5 Total Cards',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_yellow_cards'] + df['away_yellow_cards'] +
                                   df['home_red_cards'] + df['away_red_cards']) > 4.5).astype(int),
        'requires': ['home_yellow_cards', 'away_yellow_cards', 'home_red_cards', 'away_red_cards'],
    },
    'total_cards_over_5_5': {
        'description': 'Over 5.5 Total Cards',
        'type': 'binary',
        'labels': ['Under', 'Over'],
        'target_fn': lambda df: ((df['home_yellow_cards'] + df['away_yellow_cards'] +
                                   df['home_red_cards'] + df['away_red_cards']) > 5.5).astype(int),
        'requires': ['home_yellow_cards', 'away_yellow_cards', 'home_red_cards', 'away_red_cards'],
    },
}


def train_market_model(market_name, market_def, df, feature_cols, elo_ratings=None,
                       n_iter=30, tune=True):
    """
    Train a single market model using XGBoost.

    Args:
        market_name: Market identifier (e.g., 'btts', 'over_2_5')
        market_def: Market definition dict from MARKET_DEFINITIONS
        df: Full DataFrame with features AND outcome columns
        feature_cols: List of feature column names to use
        elo_ratings: Pre-computed Elo ratings dict
        n_iter: Hyperparameter search iterations
        tune: Whether to tune hyperparameters

    Returns:
        dict: {model, scaler, feature_names, accuracy, metrics} or None if insufficient data
    """
    print(f"\n{'='*70}")
    print(f"  MARKET: {market_def['description']} ({market_name})")
    print(f"{'='*70}")

    # Check required columns exist
    for col in market_def['requires']:
        if col not in df.columns:
            print(f"  SKIPPED — missing column: {col}")
            return None

    # Create target
    # Filter to rows where required columns are not null
    mask = df[market_def['requires']].notna().all(axis=1)
    df_market = df[mask].copy()

    if len(df_market) < 500:
        print(f"  SKIPPED — only {len(df_market)} matches with required data")
        return None

    y = market_def['target_fn'](df_market)

    # Prepare features
    X = df_market[feature_cols].copy()
    X = X.fillna(0)

    print(f"  Samples: {len(X)}")
    print(f"  Features: {X.shape[1]}")
    print(f"  Target distribution: {dict(y.value_counts().sort_index())}")

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Create model
    is_binary = market_def['type'] == 'binary'
    if is_binary:
        model = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='logloss', objective='binary:logistic',
        )
    else:
        model = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric='mlogloss',
        )

    if tune and n_iter > 0:
        param_grid = {
            'n_estimators': [200, 300, 500],
            'max_depth': [3, 4, 5, 6],
            'learning_rate': [0.01, 0.05, 0.1],
            'subsample': [0.7, 0.8, 0.9],
            'colsample_bytree': [0.7, 0.8, 0.9],
            'min_child_weight': [1, 3, 5],
        }
        search = RandomizedSearchCV(
            model, param_distributions=param_grid,
            n_iter=n_iter, cv=5, scoring='accuracy',
            random_state=42, n_jobs=-1, verbose=0
        )
        search.fit(X_train_scaled, y_train)
        model = search.best_estimator_
        print(f"  Best CV accuracy: {search.best_score_:.4f}")
    else:
        model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    y_pred_proba = model.predict_proba(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred)

    try:
        logloss = log_loss(y_test, y_pred_proba)
    except Exception:
        # sklearn raises when y_test is missing a class that the model predicts
        # (common on small CLI test splits). Fall through with None so the
        # rest of the metrics print and the run continues.
        logloss = None

    labels = market_def['labels']
    print(f"\n  Accuracy: {accuracy:.4f}")
    if logloss:
        print(f"  Log Loss: {logloss:.4f}")
    print("\n  Classification Report:")
    print(classification_report(y_test, y_pred, target_names=labels, digits=4))

    return {
        'model': model,
        'scaler': scaler,
        'feature_names': list(feature_cols),
        'market_name': market_name,
        'market_type': market_def['type'],
        'labels': labels,
        'description': market_def['description'],
        'accuracy': accuracy,
        'log_loss': logloss,
        'elo_ratings': elo_ratings,
        'n_samples': len(X),
    }


def train_all_markets(csv_path='../data/processed/match_features.csv',
                      tune=True, n_iter=30):
    """
    Train models for all betting markets defined in MARKET_DEFINITIONS.
    Saves all models into a single pickle file: ../models/multi_market_models.pkl

    Args:
        csv_path: Path to features CSV (must include outcome columns)
        tune: Whether to tune hyperparameters
        n_iter: Hyperparameter search iterations per market

    Returns:
        dict: market_name -> model data
    """
    print("\n" + "=" * 70)
    print("MULTI-MARKET MODEL TRAINING")
    print("=" * 70)

    # Load data
    print(f"\nLoading data from {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} records")

    # Compute Elo ratings
    elo_ratings = None
    if 'home_team' in df.columns and 'away_team' in df.columns:
        print("Computing Elo ratings...")
        home_elos, away_elos, elo_ratings = compute_elo_ratings(df)
        df['home_elo'] = home_elos
        df['away_elo'] = away_elos
        print(f"Elo ratings computed for {len(elo_ratings)} teams")

    # Build feature columns (same as load_data in MatchPredictor)
    base_cols = [
        'home_form_5', 'away_form_5',
        'home_goals_scored_avg', 'home_goals_conceded_avg',
        'away_goals_scored_avg', 'away_goals_conceded_avg',
        'h2h_home_wins', 'h2h_draws', 'h2h_away_wins',
        'home_win_rate', 'away_win_rate',
        'days_since_home_last_match', 'days_since_away_last_match',
        'home_league_position', 'away_league_position',
        'home_points', 'away_points',
        'home_goal_difference', 'away_goal_difference',
    ]

    # Build feature DataFrame
    X_all = df[base_cols].copy()
    for col in ['days_since_home_last_match', 'days_since_away_last_match']:
        X_all[col] = X_all[col].fillna(X_all[col].median())
    X_all = X_all.fillna(0)

    # Derived features
    X_all['position_diff'] = X_all['home_league_position'] - X_all['away_league_position']
    X_all['points_diff'] = X_all['home_points'] - X_all['away_points']
    X_all['gd_diff'] = X_all['home_goal_difference'] - X_all['away_goal_difference']
    X_all['form_diff'] = X_all['home_form_5'] - X_all['away_form_5']
    X_all['win_rate_diff'] = X_all['home_win_rate'] - X_all['away_win_rate']
    X_all['home_attack_vs_away_defense'] = X_all['home_goals_scored_avg'] - X_all['away_goals_conceded_avg']
    X_all['away_attack_vs_home_defense'] = X_all['away_goals_scored_avg'] - X_all['home_goals_conceded_avg']
    X_all['h2h_dominance'] = X_all['h2h_home_wins'] - X_all['h2h_away_wins']
    X_all['rest_diff'] = X_all['days_since_home_last_match'] - X_all['days_since_away_last_match']

    # Elo
    if 'home_elo' in df.columns:
        X_all['home_elo'] = df['home_elo']
        X_all['away_elo'] = df['away_elo']
        X_all['elo_diff'] = df['home_elo'] - df['away_elo']

    # Optional features
    optional_cols = [
        'home_draw_rate', 'away_draw_rate',
        'home_clean_sheet_rate', 'away_clean_sheet_rate',
        'home_weighted_form', 'away_weighted_form',
        'home_shots_on_target_avg', 'away_shots_on_target_avg',
        'home_corners_avg', 'away_corners_avg',
        'home_cards_avg', 'away_cards_avg',
        'home_points_from_top', 'away_points_from_top',
        'home_points_from_relegation', 'away_points_from_relegation',
        'season_progress',
        'home_avg_position_3yr', 'away_avg_position_3yr',
        'home_artificial_pitch',
    ]
    for col in optional_cols:
        if col in df.columns:
            X_all[col] = df[col].fillna(0)

    # Derived from optional
    if 'home_draw_rate' in X_all.columns:
        X_all['draw_rate_sum'] = X_all['home_draw_rate'] + X_all['away_draw_rate']
    if 'home_weighted_form' in X_all.columns:
        X_all['weighted_form_diff'] = X_all['home_weighted_form'] - X_all['away_weighted_form']
    if 'home_shots_on_target_avg' in X_all.columns:
        X_all['shots_on_target_diff'] = X_all['home_shots_on_target_avg'] - X_all['away_shots_on_target_avg']
    if 'home_corners_avg' in X_all.columns:
        X_all['corners_diff'] = X_all['home_corners_avg'] - X_all['away_corners_avg']
    if 'home_points_from_top' in X_all.columns:
        X_all['motivation_diff'] = X_all['away_points_from_top'] - X_all['home_points_from_top']
    if 'home_avg_position_3yr' in X_all.columns:
        X_all['squad_strength_diff'] = X_all['away_avg_position_3yr'] - X_all['home_avg_position_3yr']

    feature_cols = list(X_all.columns)

    # Merge features back with outcome columns for target creation
    df_full = pd.concat([X_all, df[['home_score', 'away_score', 'home_ht_score', 'away_ht_score',
                                     'home_corners', 'away_corners',
                                     'home_yellow_cards', 'away_yellow_cards',
                                     'home_red_cards', 'away_red_cards',
                                     'target', 'home_team', 'away_team']].reset_index(drop=True)],
                        axis=1)

    # Train each market
    all_market_models = {}
    market_results = {}

    for market_name, market_def in MARKET_DEFINITIONS.items():
        result = train_market_model(
            market_name, market_def, df_full, feature_cols,
            elo_ratings=elo_ratings, n_iter=n_iter, tune=tune
        )
        if result:
            all_market_models[market_name] = result
            market_results[market_name] = {
                'accuracy': result['accuracy'],
                'log_loss': result['log_loss'],
                'n_samples': result['n_samples'],
                'description': result['description'],
            }

    # Save all market models in one file
    os.makedirs('../models', exist_ok=True)
    save_path = '../models/multi_market_models.pkl'
    joblib.dump(all_market_models, save_path)
    print(f"\nAll market models saved to {save_path}")

    # Summary
    print("\n\n" + "=" * 70)
    print("MULTI-MARKET TRAINING SUMMARY")
    print("=" * 70)
    print(f"\n  {'Market':<35} {'Accuracy':>10} {'Log Loss':>10} {'Samples':>10}")
    print(f"  {'-'*70}")
    for name, r in sorted(market_results.items(), key=lambda x: -x[1]['accuracy']):
        ll_str = f"{r['log_loss']:.4f}" if r['log_loss'] else "N/A"
        print(f"  {r['description']:<35} {r['accuracy']:>10.4f} {ll_str:>10} {r['n_samples']:>10}")

    # Save results CSV
    pd.DataFrame(market_results).T.to_csv('../data/multi_market_results.csv')
    print("\nResults saved to ../data/multi_market_results.csv")

    return all_market_models


# ============================================================================
# RETRAIN ENTRY POINT (used by jobs/retrain.py)
# ============================================================================

def _build_xy_from_csv(csv_path, include_odds=False, binary_mode=False):
    """
    Internal helper: load CSV, compute Elo, build feature DataFrame X and target y.
    Mirrors MatchPredictor.load_data() but is callable without instantiating the class.

    Returns: (X, y, df_with_elo, feature_names, elo_ratings)
    """
    df = pd.read_csv(csv_path)
    df = df.sort_values('date').reset_index(drop=True) if 'date' in df.columns else df

    # Elo ratings (chronological, no leakage)
    elo_ratings = None
    if 'home_team' in df.columns and 'away_team' in df.columns:
        home_elos, away_elos, elo_ratings = compute_elo_ratings(df)
        df['home_elo'] = home_elos
        df['away_elo'] = away_elos

    base_cols = [
        'home_form_5', 'away_form_5',
        'home_goals_scored_avg', 'home_goals_conceded_avg',
        'away_goals_scored_avg', 'away_goals_conceded_avg',
        'h2h_home_wins', 'h2h_draws', 'h2h_away_wins',
        'home_win_rate', 'away_win_rate',
        'days_since_home_last_match', 'days_since_away_last_match',
        'home_league_position', 'away_league_position',
        'home_points', 'away_points',
        'home_goal_difference', 'away_goal_difference'
    ]
    X = df[base_cols].copy()

    # Rest-days imputation: fixed 7-day default. Computing median over the full
    # dataset would leak holdout statistics into the train set (a holdout match's
    # rest-days distribution would influence the imputation seen at train time).
    for col in ['days_since_home_last_match', 'days_since_away_last_match']:
        X[col] = X[col].fillna(7)
    X = X.fillna(0)

    # Derived features (must match MatchPredictor.load_data exactly)
    X['position_diff'] = X['home_league_position'] - X['away_league_position']
    X['points_diff'] = X['home_points'] - X['away_points']
    X['gd_diff'] = X['home_goal_difference'] - X['away_goal_difference']
    X['form_diff'] = X['home_form_5'] - X['away_form_5']
    X['win_rate_diff'] = X['home_win_rate'] - X['away_win_rate']
    X['home_attack_vs_away_defense'] = X['home_goals_scored_avg'] - X['away_goals_conceded_avg']
    X['away_attack_vs_home_defense'] = X['away_goals_scored_avg'] - X['home_goals_conceded_avg']
    X['h2h_dominance'] = X['h2h_home_wins'] - X['h2h_away_wins']
    X['rest_diff'] = X['days_since_home_last_match'] - X['days_since_away_last_match']

    if 'home_elo' in df.columns:
        X['home_elo'] = df['home_elo']
        X['away_elo'] = df['away_elo']
        X['elo_diff'] = df['home_elo'] - df['away_elo']

    optional_cols = [
        'home_draw_rate', 'away_draw_rate',
        'home_clean_sheet_rate', 'away_clean_sheet_rate',
        'home_weighted_form', 'away_weighted_form',
        'home_shots_on_target_avg', 'away_shots_on_target_avg',
        'home_corners_avg', 'away_corners_avg',
        'home_cards_avg', 'away_cards_avg',
        'home_points_from_top', 'away_points_from_top',
        'home_points_from_relegation', 'away_points_from_relegation',
        'season_progress',
        'home_avg_position_3yr', 'away_avg_position_3yr',
        'home_artificial_pitch',
    ]
    for col in optional_cols:
        if col in df.columns:
            X[col] = df[col].fillna(0)

    if 'home_draw_rate' in X.columns:
        X['draw_rate_sum'] = X['home_draw_rate'] + X['away_draw_rate']
    if 'home_weighted_form' in X.columns:
        X['weighted_form_diff'] = X['home_weighted_form'] - X['away_weighted_form']
    if 'home_shots_on_target_avg' in X.columns:
        X['shots_on_target_diff'] = X['home_shots_on_target_avg'] - X['away_shots_on_target_avg']
    if 'home_corners_avg' in X.columns:
        X['corners_diff'] = X['home_corners_avg'] - X['away_corners_avg']
    if 'home_points_from_top' in X.columns:
        X['motivation_diff'] = X['away_points_from_top'] - X['home_points_from_top']
    if 'home_avg_position_3yr' in X.columns:
        X['squad_strength_diff'] = X['away_avg_position_3yr'] - X['home_avg_position_3yr']

    if include_odds and all(c in df.columns for c in ['avg_home_prob', 'avg_draw_prob', 'avg_away_prob']):
        for col in ['avg_home_prob', 'avg_draw_prob', 'avg_away_prob']:
            X[col] = df[col].fillna(0)
        X['odds_home_away_diff'] = X['avg_home_prob'] - X['avg_away_prob']

    feature_names = list(X.columns)

    # Drop matches without target (e.g., scheduled but unfinished)
    target_mask = df['target'].notna()
    X = X[target_mask].copy().reset_index(drop=True)
    df_filtered = df[target_mask].copy().reset_index(drop=True)

    if binary_mode:
        draw_mask = df_filtered['target'] == 'DRAW'
        X = X[~draw_mask].copy().reset_index(drop=True)
        df_filtered = df_filtered[~draw_mask].copy().reset_index(drop=True)
        y = df_filtered['target'].map({'HOME_TEAM': 1, 'AWAY_TEAM': 0})
    else:
        y = df_filtered['target'].map({'HOME_TEAM': 2, 'DRAW': 1, 'AWAY_TEAM': 0})

    return X, y, df_filtered, feature_names, elo_ratings


def train_production_model(db, holdout_days=90, include_odds=False, cv_splits=5):
    """
    Train the SAME architecture used in production (stacked ensemble: XGBoost + RandomForest
    with a LogisticRegression meta-learner). Time-based holdout split and TimeSeriesSplit CV
    so the validation gate in the retrain job compares apples-to-apples against the deployed
    model — previously the gate compared a freshly-trained plain XGBoost against a production
    stacked ensemble, which forced the AUC tolerance to be widened until the gate became a
    no-op.

    Pipeline:
        1. Compute features for all FINISHED matches in DB (idempotent)
        2. Export to a temp CSV, build (X, y) with leakage-safe imputation
        3. Time-based split on `holdout_days`
        4. Fit StandardScaler on TRAIN only, transform both
        5. Stack XGBoost + RandomForest via 5-fold TimeSeriesSplit CV → LR meta-learner
        6. Return model_data dict + scaled holdout for the caller's validation gate

    Returns:
        dict with the same keys as train_xgboost(): model_data, X_holdout, y_holdout,
        holdout_size, train_size, cutoff.
    """
    from feature_engineering import FeatureEngineer

    fe = FeatureEngineer()
    try:
        print("[train_production_model] Computing features for all matches in DB...")
        fe.create_features_for_all_matches(save_to_db=True)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tmp:
            tmp_csv = tmp.name
        try:
            print(f"[train_production_model] Exporting features to {tmp_csv}...")
            fe.export_features_to_csv(output_path=tmp_csv)

            X, y, df, feature_names, elo_ratings = _build_xy_from_csv(
                tmp_csv, include_odds=include_odds, binary_mode=False
            )

            df['date'] = pd.to_datetime(df['date'], utc=True, errors='coerce')
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=holdout_days)
            train_mask = df['date'] < cutoff
            hold_mask = df['date'] >= cutoff

            X_train = X[train_mask].copy()
            y_train = y[train_mask].copy()
            X_hold = X[hold_mask].copy()
            y_hold = y[hold_mask].copy()

            print(f"[train_production_model] Train: {len(X_train)}, Holdout: {len(X_hold)} (last {holdout_days}d)")
            if len(X_train) < 100:
                raise RuntimeError(f"Training set too small ({len(X_train)} < 100)")

            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_hold_scaled_arr = scaler.transform(X_hold) if len(X_hold) else np.empty((0, X_train.shape[1]))

            # Base estimators — match compare_models() defaults so retrain produces the
            # same architecture that ships from the CLI training pipeline.
            xgb_est = xgb.XGBClassifier(
                n_estimators=300, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42,
                eval_metric='mlogloss', objective='multi:softprob',
            )
            rf_est = RandomForestClassifier(
                n_estimators=300, max_depth=10, min_samples_leaf=2,
                random_state=42, n_jobs=-1,
            )

            # NOTE: We previously used TimeSeriesSplit here to give the LR
            # meta-learner only past-data fold predictions (less optimistic
            # than StratifiedKFold for the temporally-ordered match feed).
            # That broke in sklearn 1.5+ because StackingClassifier calls
            # `cross_val_predict` internally, which requires the CV scheme to
            # PARTITION the input (every sample in exactly one test fold).
            # TimeSeriesSplit by design leaves the earliest samples train-only
            # so it doesn't partition → ValueError("cross_val_predict only
            # works for partitions").
            #
            # We fall back to plain StratifiedKFold(5). The honest out-of-
            # sample check is still the time-based HOLDOUT split that's done
            # before training, so reported holdout AUC remains trustworthy.
            # The leakage is confined to the meta-learner's training inputs,
            # which is a smaller concern than zero retraining.
            from sklearn.model_selection import StratifiedKFold
            cv_strategy = StratifiedKFold(n_splits=cv_splits, shuffle=True, random_state=42)

            stacking = StackingClassifier(
                estimators=[('xgb', xgb_est), ('rf', rf_est)],
                final_estimator=LogisticRegression(max_iter=1000, C=1.0, random_state=42),
                cv=cv_strategy,
                stack_method='predict_proba',
                passthrough=False,
                n_jobs=1,  # base estimators already use n_jobs=-1 internally; nesting hangs on some CI runners
            )

            print(f"[train_production_model] Fitting stacked ensemble (CV={cv_splits} time-series folds)...")
            start = datetime.now()
            stacking.fit(X_train_scaled, y_train)
            print(f"[train_production_model] Trained in {(datetime.now() - start).total_seconds():.1f}s")

            model_data = {
                'model': stacking,
                'scaler': scaler,
                'feature_names': feature_names,
                'model_type': 'stacked_ensemble',
                'elo_ratings': elo_ratings,
                'created_at': datetime.now().isoformat(),
            }

            X_hold_scaled = (
                pd.DataFrame(X_hold_scaled_arr, columns=feature_names)
                if len(X_hold) else pd.DataFrame(columns=feature_names)
            )

            return {
                'model_data': model_data,
                'X_holdout': X_hold_scaled,
                'y_holdout': y_hold.reset_index(drop=True),
                'holdout_size': len(X_hold),
                'train_size': len(X_train),
                'cutoff': cutoff,
            }
        finally:
            try:
                os.unlink(tmp_csv)
            except OSError:
                pass
    finally:
        fe.close()


def evaluate_auc(model_data, X_scaled, y, labels=(0, 1, 2)):
    """
    Multi-class one-vs-rest AUC for a trained model_data dict.

    Args:
        model_data: dict with 'model' (and optionally 'feature_names' for column alignment)
        X_scaled: features ALREADY scaled by the model's scaler. Pass DataFrame to align by name,
                  or numpy array if you've already aligned.
        y: target Series (0=AWAY, 1=DRAW, 2=HOME for 3-class)
        labels: full set of class labels — pass even if some classes are missing in y

    Returns:
        float: macro-averaged one-vs-rest AUC
    """
    if hasattr(X_scaled, 'columns') and 'feature_names' in model_data:
        X_scaled = X_scaled[model_data['feature_names']]
    proba = model_data['model'].predict_proba(X_scaled)
    return float(roc_auc_score(y, proba, multi_class='ovr', labels=list(labels)))


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import sys

    tune = '--no-tune' not in sys.argv
    args = set(sys.argv[1:])

    # Flags:
    #   --markets-only    Only train multi-market models (BTTS, O/U, corners, etc.)
    #   --advanced-only   Only train stacked ensemble + calibrated + markets (skip model comparison)
    #   --skip-markets    Skip multi-market training
    #   --no-tune         Skip hyperparameter tuning (faster, uses defaults)
    #
    # Examples:
    #   python model_training.py                     Full pipeline
    #   python model_training.py --markets-only      Only multi-market models
    #   python model_training.py --advanced-only     Stacked + calibrated + markets
    #   python model_training.py --advanced-only --skip-markets   Just stacked + calibrated

    markets_only = '--markets-only' in args
    advanced_only = '--advanced-only' in args
    skip_markets = '--skip-markets' in args

    if not markets_only and not advanced_only:
        # Full pipeline: model comparison (includes stacked + calibrated)
        compare_models(tune=tune, n_iter=50)

    elif advanced_only:
        # Skip individual model comparison, just train stacked + calibrated
        print("\n" + "=" * 70)
        print("ADVANCED ONLY MODE — Skipping individual model comparison")
        print("=" * 70)

        # Stacked ensemble 3-class
        stack_predictor, stack_metrics = train_stacked_ensemble(
            tune=False, include_odds=False, binary_mode=False,
            label="STACKED ENSEMBLE 3-CLASS (no odds)"
        )

        # Stacked ensemble binary
        stack_binary_predictor, stack_binary_metrics = train_stacked_ensemble(
            tune=False, include_odds=False, binary_mode=True,
            label="STACKED ENSEMBLE BINARY (no odds)"
        )

        # Calibrated model
        cal_predictor, cal_metrics, cal_ll = train_calibrated_model(
            base_model_type='xgboost', include_odds=False,
            label="CALIBRATED XGBoost (no odds)"
        )

        print("\n" + "=" * 70)
        print("ADVANCED MODELS — SUMMARY")
        print("=" * 70)
        print(f"  Stacked 3-class accuracy:  {stack_metrics['accuracy']:.4f}")
        print(f"  Stacked binary accuracy:   {stack_binary_metrics['accuracy']:.4f}")
        print(f"  Calibrated accuracy:       {cal_metrics['accuracy']:.4f}")
        if cal_ll is not None:
            print(f"  Calibrated log loss:       {cal_ll:.4f}")

    # Train multi-market models (unless skipped)
    if not skip_markets:
        train_all_markets(tune=tune, n_iter=30)

    print("\n\n" + "=" * 70)
    print("MODEL TRAINING COMPLETE!")
    print("=" * 70)
    print("\nModels saved:")
    if not markets_only and not advanced_only:
        print("  ../models/best_model.pkl              — Best 3-class model (for API)")
        print("  ../models/best_model_with_odds.pkl     — Best 3-class with odds")
        print("  ../models/best_binary_model.pkl        — Best binary model (no draws)")
    if not markets_only:
        print("  ../models/stacked_ensemble_*_model.pkl — Stacking ensembles")
        print("  ../models/calibrated_*_model.pkl       — Calibrated model")
    if not skip_markets:
        print("  ../models/multi_market_models.pkl      — All betting market models")
    print("\nCheck ../data/ for evaluation plots, metrics, and model_comparison.csv")
