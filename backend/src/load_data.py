"""
Load Data Module

Reads the processed CSV file (from data_collection.py) and loads it into
the PostgreSQL database. Teams are inserted first (to satisfy foreign key
constraints), then matches are inserted with references to team IDs.
"""

import pandas as pd
from database import DatabaseManager

def load_csv_to_database(csv_path):
    """
    Load processed match CSV into the PostgreSQL database.

    Steps:
        1. Read the CSV into a DataFrame
        2. Extract unique teams and insert them (upsert by api_id)
        3. Insert each match row, linking to the team records via api_id

    Args:
        csv_path (str): Path to the processed CSV file
    """
    print(f"Reading data from {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} matches")

    # Initialize database manager
    db = DatabaseManager()

    # Track statistics
    teams_added = 0
    matches_added = 0

    # Step 1: Collect all unique teams from both home and away columns
    # Teams must be inserted before matches (foreign key dependency)
    print('\nAdding teams...')
    teams = set()

    for _, row in df.iterrows():
        teams.add((row['home_team_id'], row['home_team_name'], row['home_team_short']))
        teams.add((row['away_team_id'], row['away_team_name'], row['away_team_short']))

    for team_id, team_name, team_short in teams:
        db.add_team(
            api_id=int(team_id),
            name=team_name,
            short_name=team_short,
            competition=df['competition'].iloc[0]
        )
        teams_added += 1
    
    print(f"Added {teams_added} teams")

    # Step 2: Insert each match, mapping CSV columns to the Match model fields
    print("\nAdding matches...")
    for idx, row in df.iterrows():

        # Build match dict — handle NaN values with pd.notna() checks
        match_data = {
            'api_id': int(row['match_id']),
            'home_team_api_id': int(row['home_team_id']),
            'away_team_api_id': int(row['away_team_id']),
            'season': int(row['season']),
            'matchday': int(row['matchday']) if pd.notna(row['matchday']) else None,
            'competition': row['competition'],
            'date': pd.to_datetime(row['date']), # Parsing the date
            'status': row['status'],
            'home_score': int(row['home_score']) if pd.notna(row['home_score']) else None,
            'away_score': int(row['away_score']) if pd.notna(row['away_score']) else None,
            'winner': row['winner'] if pd.notna(row['winner']) else None
        }

        db.add_match(match_data)
        matches_added += 1

        # Progress log every 100 matches
        if (idx + 1) % 100 == 0:
            print(f"    Processed {idx + 1}/{len(df)} matches...")
        
    print(f"\nAdded {matches_added} matches")

    # Close database connection
    db.close()

    print("\nData loading complete!")
    print(f"    Teams: {teams_added}")
    print(f"    Matches: {matches_added}")

# Main execution — loads the Premier League processed CSV into the database
if __name__ == "__main__":
    csv_path = '../data/processed/premier_league_matches.csv'
    load_csv_to_database(csv_path)