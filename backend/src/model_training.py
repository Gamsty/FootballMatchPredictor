"""
Model Training Module

Trains, evaluates, and compares ML models for football match prediction.
Supports XGBoost, Random Forest, Gradient Boosting, and Logistic Regression.

Pipeline: match_features.csv → load/clean → train/test split → scale → train → evaluate → save
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score, GridSearchCV
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score
)
import xgboost as xgb
import joblib
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
import os

class MatchPredictor:
    """Handles ML model training and prediction"""

    def __init__(self, model_type='xgboost'):
        """
        Initialize the predictor

        Args:
            model_type (str): Type of model ('xgboost', 'random_forest' etc.)
        """
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.label_encoder = LabelEncoder()
        self.feature_names = None
        self.training_history = []

    def load_data(self, csv_path='../data/processed/match_features.csv'):
        """
        Load and prepare data

        Args:
            csv_path (str): Path to features csv

        Returns:
            tuple: (X, y, df) - features, target, full dataframe
        """
        print(f"Loading data from {csv_path}...")
        df = pd.read_csv(csv_path)

        print(f"Loaded {len(df)} records")

        # Feature columns
        feature_cols = [
            'home_form_5', 'away_form_5',
            'home_goals_scored_avg', 'home_goals_conceded_avg',
            'away_goals_scored_avg', 'away_goals_conceded_avg',
            'h2h_home_wins', 'h2h_draws', 'h2h_away_wins',
            'home_win_rate', 'away_win_rate',
            'days_since_home_last_match', 'days_since_away_last_match'
        ]

        # Store feature names
        self.feature_names = feature_cols

        # Prepare features (X) and target (y)
        X = df[feature_cols].copy()

        # Fill missing rest days with median (matchday 1 has no previous match)
        # Other missing values filled with 0 (e.g., form/goals for early matches)
        for col in ['days_since_home_last_match', 'days_since_away_last_match']:
            X[col] = X[col].fillna(X[col].median())
        X = X.fillna(0)

        # Encode target: HOME_TEAM=2, DRAW=1, AWAY_TEAM=0
        # Higher value = better outcome for home team
        y = df['target'].map({
            'HOME_TEAM': 2,
            'DRAW': 1,
            'AWAY_TEAM': 0
        })

        print(f"\nFeatures shape: {X.shape}")
        print(f"Target distribution:")
        print(df['target'].value_counts())
        print(f"\nEncoded target distributions:")
        print(y.value_counts().sort_index())

        return X, y, df
    
    def split_data(self, X, y, test_size=0.2, random_state=42):
        """
        Split data into train and test sets

        Args:
            X: Features
            y: Target
            test_size (float): Proportion of test data
            random_state (int): Random seed

        Returns:
            tuple: X_train, X_test, y_train, y_test
        """
        print(f"\nSplitting data: {int((1-test_size)*100)}% train, {int(test_size*100)}% test")

        X_train, X_test, y_train, y_test = train_test_split(
            X, y,
            test_size=test_size,
            random_state=random_state,
            stratify=y # Maintain class distribution
        )

        print(f"Training set: {len(X_train)} samples")
        print(f"Test set: {len(X_test)} samples")

        return X_train, X_test, y_train, y_test
    
    def scale_features(self, X_train, X_test):
        """
        Scale features using StandardScaler

        Args:
            X_train: Training features
            X_test: Test features

        Returns:
            tuple: Scaled X_train, X_test
        """
        print("\nScaling features...")

        # Fit scaler on training data only
        X_train_scaled = self.scaler.fit_transform(X_train)

        # Transform test data using the same scaler
        X_test_scaled = self.scaler.transform(X_test)

        print("Features scaled successfully")

        return X_train_scaled, X_test_scaled
    
    def create_model(self):
        """
        Create ML model based on model_type

        Returns:
            model: Sklearn/XGBoost model
        """
        print(f"\nCreating {self.model_type} model...")

        if self.model_type == 'xgboost':
            model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=6,
                learning_rate=0.1,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                eval_metric='mlogloss'
            )
        elif self.model_type == 'random_forest':
            model = RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=42
            )
        elif self.model_type == 'gradient_boosting':
            model = GradientBoostingClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42
            )
        else:  # logistic_regression
            model = LogisticRegression(
                solver='lbfgs',
                max_iter=1000,
                random_state=42
            )
        
        return model
    
    def train(self, X_train, y_train, X_val=None, y_val=None):
        """
        Train the model

        Args:
            X_train: Training features (scaled)
            y_train: Training target
            X_val: Validation features (optional)
            y_val: Validation target (optional)

        Returns:
            model: Trained model
        """
        print(f"\nTraining {self.model_type} model...")
        print("=" * 60)

        # Create model
        self.model = self.create_model()

        # Train
        start_time = datetime.now()

        if self.model_type == 'xgboost' and X_val is not None:
            # XGBoost supports validation set for early stopping
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False   
            )
        else:
            self.model.fit(X_train, y_train)

        training_time = (datetime.now() - start_time).total_seconds()

        print(f"Training completed in {training_time:.2f} seconds")

        # Cross-validation score
        cv_scores = cross_val_score(self.model, X_train, y_train, cv=5)
        print(f"\nCross-validation scores: {cv_scores}")
        print(f"CV Mean Accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

        return self.model
    
    def evaluate(self, X_test, y_test):
        """
        Evaluate model performance

        Args:
            X_test: Test features (scaled)
            y_test: Test target

        Returns:
            dict: Evaluation metrics
        """
        print("\n" + "=" * 60)
        print("MODEL EVALUATION")
        print("=" * 60)


        # Make predictions
        y_pred = self.model.predict(X_test)
        y_pred_proba = self.model.predict_proba(X_test)

        # Calculate metrics
        accuracy = accuracy_score(y_test, y_pred)

        # Macro and weighted averages
        precision_macro = precision_score(y_test, y_pred, average='macro')
        recall_macro = recall_score(y_test, y_pred, average='macro')
        f1_macro = f1_score(y_test, y_pred, average='macro')

        precision_weighted = precision_score(y_test, y_pred, average='weighted')
        recall_weighted = recall_score(y_test, y_pred, average='weighted')
        f1_weighted = f1_score(y_test, y_pred, average='weighted')

        print(f"\nOverall Accuracy: {accuracy:.4f}")
        print(f"\nMacro Averages:")
        print(f"    Precision: {precision_macro:.4f}")
        print(f"    Recall: {recall_macro:.4f}")
        print(f"    F1 Score: {f1_macro:.4f}")
        print(f"\nWeighted Averages:")
        print(f"    Precision: {precision_weighted:.4f}")
        print(f"    Recall: {recall_weighted:.4f}")
        print(f"    F1 Score: {f1_weighted:.4f}")

        # Detailed classification report
        print("\nDetailed Classification Report:")
        print(classification_report(
            y_test, y_pred,
            target_names=['Away Win', 'Draw', 'Home Win'],
            digits=4
        ))

        # Confusion matrix
        cm = confusion_matrix(y_test, y_pred)
        print("\nConfusion Matrix:")
        print("                 Predicted")
        print("                 Away  Draw  Home")
        print(f"Actual Away     {cm[0]}")
        print(f"        Draw    {cm[1]}")
        print(f"        Home    {cm[2]}")

        # Visualize confusion matrix
        self._plot_confusion_matrix(cm)

        # Store metrics
        metrics = {
            'accuracy': accuracy,
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
    
    def _plot_confusion_matrix(self, cm):
        """Plot confusion matrix heatmap"""
        plt.figure(figsize=(8, 6))
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                    xticklabels=['Away Win', 'Draw', 'Home Win'],
                    yticklabels=['Away Win', 'Draw', 'Home Win'])
        plt.title(f'Confusion Matrix - {self.model_type}')
        plt.ylabel('Actual')
        plt.xlabel('Predicted')
        plt.tight_layout()
        plt.savefig(f'../data/confusion_matrix_{self.model_type}.png', dpi=300)
        print(f"\nConfusion matrix saved to ../data/confusion_matrix_{self.model_type}.png")
        plt.close()

    def feature_importance(self):
        """
        Display feature importance

        Returns:
            pd.DataFrame: Feature importance
        """
        if not hasattr(self.model, 'feature_importances_'):
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

        # Plot
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
        """
        Save trained model

        Args:
            filename (str): Path to save model
        """
        if filename is None:
            filename = f'../models/{self.model_type}_model.pkl'

        # Create models directory if it doesn't exists
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        # Save model and scaler
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'model_type': self.model_type,
            'created_at': datetime.now().isoformat()
        }

        joblib.dump(model_data, filename)
        print(f"\nModel saved to {filename}")

    def load_model(self, filename):
        """
        Load trained model

        Args:
            filename (str): Path to model file
        """
        print(f"Loading model from {filename}")

        model_data = joblib.load(filename)

        self.model = model_data['model']
        self.scaler = model_data['scaler']
        self.feature_names = model_data['feature_names']
        self.model_type = model_data['model_type']

        print("Model loaded successfully!")
        print(f"Model type: {self.model_type}")
        print(f"Created at: {model_data.get('created_at', 'Unknown')}")

    def predict(self, features_dict):
        """
        Make prediction for a single match

        Args:
            features_dict (dict): Dictionary of features

        Returns:
            dict: Prediction results
        """
        # Create DataFrame from features
        X = pd.DataFrame([features_dict])[self.feature_names]

        # Scale features
        X_scaled = self.scaler.transform(X)

        # Predict
        prediction = self.model.predict(X_scaled)[0]
        probabilities = self.model.predict_proba(X_scaled)[0]

        # Map prediction back to labels
        outcome_map = {0: 'AWAY_TEAM', 1: 'DRAW', 2: 'HOME_TEAM'}

        result = {
            'predicted_outcome': outcome_map[prediction],
            'probabilities': {
                'away_win': float(probabilities[0]),
                'draw': float(probabilities[1]),
                'home_win': float(probabilities[2])
            },
            'confidence': float(max(probabilities))
        }

        return result
    
def compare_models():
    """
    Train and compare all model types, evaluate each, and save the best.
    Standalone function — not a class method.
    """
    print("=" * 70)
    print("MODEL COMPARISON")
    print("=" * 70)

    # Model types to compare
    model_types = ['xgboost', 'random_forest', 'gradient_boosting', 'logistic_regression']

    results = {}

    for model_type in model_types:
        print(f"\n\n{'='*70}")
        print(f"Training {model_type.upper()}")
        print(f"{'='*70}")

        # Initialize predictor
        predictor = MatchPredictor(model_type=model_type)

        # Load data
        X, y, df = predictor.load_data()

        # Split data
        X_train, X_test, y_train, y_test = predictor.split_data(X, y)

        # Scale features
        X_train_scaled, X_test_scaled = predictor.scale_features(X_train, X_test)

        # Train model
        predictor.train(X_train_scaled, y_train)

        # Evaluate
        metrics = predictor.evaluate(X_test_scaled, y_test)

        # Feature importance (if available)
        if model_type != 'logistic_regression':
            predictor.feature_importance()

        # Save model
        predictor.save_model()

        # Store results for comparison table
        results[model_type] = {
            'accuracy': metrics['accuracy'],
            'f1_weighted': metrics['f1_weighted'],
            'precision_weighted': metrics['precision_weighted'],
            'recall_weighted': metrics['recall_weighted']
        }

    # Compare results
    print("\n\n" + "=" * 70)
    print("FINAL COMPARISON")
    print("=" * 70)

    comparison_df = pd.DataFrame(results).T
    comparison_df = comparison_df.sort_values('accuracy', ascending=False)

    print("\nModel Performance Summary:")
    print(comparison_df.to_string())

    # Save comparison
    comparison_df.to_csv('../data/model_comparison.csv')
    print("\nComparison saved to ../data/model_comparison.csv")

    # Visualize comparison — bar chart for each metric
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    metric_to_plot = ['accuracy', 'f1_weighted', 'precision_weighted', 'recall_weighted']

    for idx, metric in enumerate(metric_to_plot):
        ax = axes[idx // 2, idx % 2]
        comparison_df[metric].plot(kind='barh', ax=ax, color='steelblue')
        ax.set_xlabel(metric.replace('_', ' ').title())
        ax.set_title(f'{metric.replace("_", " ").title()} by Model')
        ax.grid(axis='x', alpha=0.3)

    plt.tight_layout()
    plt.savefig('../data/model_comparison.png', dpi=300)
    print("Comparison plot saved to ../data/model_comparison.png")
    plt.close()

# Main execution
if __name__ == "__main__":
    # Train and compare all models
    compare_models()
    
    print("\n\n" + "=" * 70)
    print("MODEL TRAINING COMPLETE!")
    print("=" * 70)
    print("\nBest model saved and ready for use.")
    print("Check ../models/ for saved models")
    print("Check ../data/ for evaluation plots and metrics")

