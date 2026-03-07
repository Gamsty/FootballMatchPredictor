"""
Feature Engineering Module

Transforms raw match data into ML-ready features.
Each feature is computed using only past data (before the match date)
to prevent information leakage during model training.

Features computed per match:
    - Team form: points per game over last 5 matches
    - Goal averages: goals scored/conceded over last 5 home/away matches
    - Head-to-head: wins/draws/losses between the two teams (last 5 meetings)
    - Win rates: home/away win rate over last 10 matches
    - Rest days: days since each team's last match

Pipeline: database (matches table) → compute features → match_features table + CSV
"""

import pandas as pd
import numpy as np
from datetime import datetime
from database import DatabaseManager, Match, Team, MatchFeatures
from sqlalchemy import and_, or_

class FeatureEngineer:
    """
    Creates ML features from raw match data.
    All features are computed using only historical data (no future leakage).
    """

    def __init__(self):
        # DatabaseManager provides access to teams, matches, and match_features tables
        self.db = DatabaseManager()

    def close(self):
        """Close database connection"""
        self.db.close()

    def calculate_team_form(self, team_id, before_date, last_n=5):
        """
        Calculate team form (points from last N matches)

        Args:
            team_id (int): Team database ID
            before_date (datetime): Calculate from before this date
            last_n (int): Number of recent matches to consider

        Returns:
            float: Total points from last N matches
        """
        # Get team's most recent N matches before the specified date
        # Uses both home and away matches to capture overall form
        matches = self.db.session.query(Match).filter(
            and_(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id
                ),
                Match.date < before_date,
                Match.status == 'FINISHED'
            )
        ).order_by(Match.date.desc()).limit(last_n).all()

        # Win = 3 pts, Draw = 1 pt, Loss = 0 pts (standard football scoring)
        points = 0

        for match in matches:
            if match.home_team_id == team_id:
                # Team played at home
                if match.winner == 'HOME_TEAM':
                    points += 3
                elif match.winner == 'DRAW':
                    points += 1
            else:
                # Team played away
                if match.winner == 'AWAY_TEAM':
                    points += 3
                elif match.winner == 'DRAW':
                    points += 1

        return float(points)
    
    def calculate_avg_goals_scored(self, team_id, before_date, home=True, last_n=5):
        """
        Calculate average goals scored by team

        Args:
            team_id (int): Team database ID
            before_date (datetime): Calculate from before this date
            home (bool): True for home matches, False for away
            last_n (int): Number of recent matches to consider

        Returns:
            float: Average goals scored
        """
        if home:
            # Get home matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.home_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            goals = [m.home_score for m in matches if m.home_score is not None]
        else:
            # Get away matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.away_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            goals = [m.away_score for m in matches if m.away_score is not None]

        return float(np.mean(goals)) if goals else 0.0
    
    def calculate_avg_goals_conceded(self, team_id, before_date, home=True, last_n=5):
        """
        Calculate average goals conceded by team

        Args:
            team_id (int): Team database ID
            before_date (datetime): Calculate from before this date
            home (bool): True for home matches, False for away
            last_n (int): Number of recent matches to consider

        Returns:
            float: Average goals conceded
        """
        if home:
            # Get home matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.home_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            # Conceded = opponent's goals (away_score when team is home)
            goals = [m.away_score for m in matches if m.away_score is not None]

        else:
            # Get away matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.away_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            # Conceded = opponent's goals (home_score when team is away)
            goals = [m.home_score for m in matches if m.home_score is not None]

        return float(np.mean(goals)) if goals else 0.0
    
    def calculate_head_to_head(self, home_team_id, away_team_id, before_date, last_n=5):
        """
        Calculate head-to-head record between two teams

        Args:
            home_team_id (int): Home team database ID
            away_team_id (int): Away team database ID
            before_date (datetime): Calculate from before this date
            last_n (int): Number of recent matches to consider

        Returns:
            tuple: (home_wins, draws, away_wins)
        """
        # Get matches between these two teams (in either home/away order)
        matches = self.db.session.query(Match).filter(
            and_(
                or_(
                    and_(
                        Match.home_team_id == home_team_id,
                        Match.away_team_id == away_team_id
                    ),
                    and_(
                        Match.home_team_id == away_team_id,
                        Match.away_team_id == home_team_id
                    )
                ),
                Match.date < before_date,
                Match.status == 'FINISHED'
            )
        ).order_by(Match.date.desc()).limit(last_n).all()

        home_wins = 0
        draws = 0
        away_wins = 0
        
        # Count wins relative to the current match's home/away assignment
        # If the teams played in reversed roles, flip the result accordingly
        for match in matches:
            if match.home_team_id == home_team_id:
                # Same home/away arrangement as current match
                if match.winner == 'HOME_TEAM':
                    home_wins += 1
                elif match.winner == 'AWAY_TEAM':
                    away_wins += 1
                else:
                    draws += 1
            else:
                # Reversed arrangement — flip the result
                if match.winner == 'AWAY_TEAM':
                    home_wins += 1
                elif match.winner == 'HOME_TEAM':
                    away_wins += 1
                else:
                    draws += 1
    
        return home_wins, draws, away_wins
    
    def calculate_win_rate(self, team_id, before_date, home=True, last_n=10):
        """
        Calculate win rate for team

        Args:
            team_id (int): Team database ID
            before_date (datetime): Calculate from before this date
            home (bool): True for home matches, False for away
            last_n (int): Number of recent matches to consider

        Returns:
            float: Win rate (0.0 to 1.0)
        """
        if home:
            # Get home matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.home_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            wins = sum(1 for m in matches if m.winner == 'HOME_TEAM')
        else:
            # Get away matches
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.away_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED'
                )
            ).order_by(Match.date.desc()).limit(last_n).all()

            wins = sum(1 for m in matches if m.winner == 'AWAY_TEAM')

        total = len(matches)
        return float(wins / total) if total > 0 else 0.0
    
    def calculate_days_since_last_match(self, team_id, current_date):
        """
        Calculate days since team's last match (rest days)

        Args:
            team_id (int): Team database ID
            current_date (datetime): Current match date

        Returns:
            int: Days since last match
        """
        last_match = self.db.session.query(Match).filter(
            and_(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id
                ),
                Match.date < current_date,
                Match.status == 'FINISHED'
            )
        ).order_by(Match.date.desc()).first()

        if last_match:
            days = (current_date - last_match.date).days
            return days
        
        return None  # No previous match found
    
    def create_match_features(self, match):
        """
        Create all features for a single match

        Args:
            match (Match): Match object from database

        Returns:
            dict: Dictionary of features
        """
        # Extract match info for convenience
        home_team_id = match.home_team_id
        away_team_id = match.away_team_id
        match_date = match.date

        # Calculate all features using only data from before this match
        features = {
            'match_id': match.id,

            # Form (last 5 matches)
            'home_form_5': self.calculate_team_form(home_team_id, match_date, 5),
            'away_form_5': self.calculate_team_form(away_team_id, match_date, 5),

            # Goals scored (last 5)
            'home_goals_scored_avg': self.calculate_avg_goals_scored(
                home_team_id, match_date, True, 5
            ),
            'away_goals_scored_avg': self.calculate_avg_goals_scored(
                away_team_id, match_date, False, 5
            ),

            # Goals conceded (last 5)
            'home_goals_conceded_avg': self.calculate_avg_goals_conceded(
                home_team_id, match_date, True, 5
            ),
            'away_goals_conceded_avg': self.calculate_avg_goals_conceded(
                away_team_id, match_date, False, 5
            ),

            # Win rate (last 10)
            'home_win_rate': self.calculate_win_rate(home_team_id, match_date, True, 10),
            'away_win_rate': self.calculate_win_rate(away_team_id, match_date, False, 10),

            # Head-to-head
            'h2h_home_wins': 0,
            'h2h_away_wins': 0,
            'h2h_draws': 0,

            # Rest days
            'days_since_home_last_match': self.calculate_days_since_last_match(home_team_id, match_date),
            'days_since_away_last_match': self.calculate_days_since_last_match(away_team_id, match_date),
        }

        # Calculate head-to-head (returns: home_wins, draws, away_wins)
        h2h_home, h2h_draws, h2h_away = self.calculate_head_to_head(
            home_team_id, away_team_id, match_date, 5
        )
        features['h2h_home_wins'] = h2h_home
        features['h2h_away_wins'] = h2h_away
        features['h2h_draws'] = h2h_draws

        return features
    
    def create_features_for_all_matches(self, save_to_db=True):
        """
        Create features for all matches in database

        Args:
            save_to_db (bool): Whether to save features to database

        Returns:
            pd.DataFrame: Features dataframe
        """
        # Get all finished matches ordered by date (oldest first)
        # Oldest first ensures early matches have fewer past games (features = None), which is expected
        matches = self.db.session.query(Match).filter(
            Match.status == 'FINISHED'
        ).order_by(Match.date).all()

        print(f"Creating features for {len(matches)} matches...")

        all_features = []

        for idx, match in enumerate(matches):
            # Create features
            features = self.create_match_features(match)

            # Add target variable — this is what the ML model will learn to predict
            # Values: 'HOME_TEAM', 'AWAY_TEAM', or 'DRAW'
            features['target'] = match.winner

            all_features.append(features)

            # Save to database if requested
            if save_to_db:
                # Check if features already exists
                existing = self.db.session.query(MatchFeatures).filter_by(
                    match_id=match.id
                ).first()

                if existing:
                    # Update existing features
                    for key, value in features.items():
                        if key != 'match_id' and key != 'target' and hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    # Create new features
                    match_features = MatchFeatures(
                        match_id=features['match_id'],
                        home_form_5=features['home_form_5'],
                        away_form_5=features['away_form_5'],
                        home_goals_scored_avg=features['home_goals_scored_avg'],
                        away_goals_scored_avg=features['away_goals_scored_avg'],
                        home_goals_conceded_avg=features['home_goals_conceded_avg'],
                        away_goals_conceded_avg=features['away_goals_conceded_avg'],
                        h2h_home_wins=features['h2h_home_wins'],
                        h2h_away_wins=features['h2h_away_wins'],
                        h2h_draws=features['h2h_draws'],
                        home_win_rate=features['home_win_rate'],
                        away_win_rate=features['away_win_rate'],
                        days_since_home_last_match=features['days_since_home_last_match'],
                        days_since_away_last_match=features['days_since_away_last_match']
                    )
                    self.db.session.add(match_features)

                # Commit every 100 matches
                if (idx + 1) % 100 == 0:
                    self.db.session.commit()
                    print(f"    Processed {idx + 1}/{len(matches)} matches...")

                
        # Final commit
        if save_to_db:
            self.db.session.commit()
            print("Features saved to database!")

        # Convert to DataFrame
        df = pd.DataFrame(all_features)

        print(f"\nFeatures created: {len(df)} rows, {len(df.columns)} columns")
        print(f"Feature columns: {list(df.columns)}")

        return df
    
    def export_features_to_csv(self, output_path='../data/processed/match_features.csv'):
        """
        Export features to CSV file by joining match_features with match and team data.

        Args:
            output_path (str): Path to save CSV
        """
        # SQL query joins features with match info and team names for a complete export
        query = """
        SELECT
            mf.match_id,
            mf.home_form_5,
            mf.away_form_5,
            mf.home_goals_scored_avg,
            mf.away_goals_scored_avg,
            mf.home_goals_conceded_avg,
            mf.away_goals_conceded_avg,
            mf.h2h_home_wins,
            mf.h2h_away_wins,
            mf.h2h_draws,
            mf.home_win_rate,
            mf.away_win_rate,
            mf.days_since_home_last_match,
            mf.days_since_away_last_match,
            m.winner as target,
            m.date,
            m.season,
            ht.name as home_team,
            at.name as away_team
        FROM match_features mf
        JOIN matches m ON mf.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        ORDER BY m.date
        """

        df = pd.read_sql(query, self.db.session.bind)
        df.to_csv(output_path, index=False)
        
        print(f"Features exported to {output_path}")
        print(f"Total records: {len(df)}")

        return df
    
# Main execution — runs the full feature engineering pipeline:
# 1. Compute features for every match using only historical data
# 2. Save features to the match_features database table
# 3. Export features + match info to CSV for model training
if __name__ == "__main__":
    print("=" * 70)
    print("FEATURE ENGINEERING")
    print("=" * 70)

    # Initialize feature engineer
    engineer = FeatureEngineer()

    # Create features for all matches
    feature_df = engineer.create_features_for_all_matches(save_to_db=True)

    # Export to CSV
    engineer.export_features_to_csv()
    
    # Show sample
    print("\nSample features:")
    print(feature_df.head(10))

    # Show statistics
    print("\nFeature Statistics:")
    print(feature_df.describe())

    # Close database connection
    engineer.close()

    print("\nFeature engineering complete!")
    

