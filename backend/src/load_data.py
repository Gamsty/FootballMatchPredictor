"""
Load Data Module — Multi-League Edition

Reads the processed CSV file (from data_collection.py) and loads it into
the PostgreSQL database. Handles multiple competitions in a single CSV.
Teams are inserted first (to satisfy foreign key constraints),
then matches are inserted with references to team IDs.
"""

import pandas as pd
import json
import os
from database import DatabaseManager, Team, Match

def load_csv_to_database(csv_path):
    """
    Load processed match CSV into the PostgreSQL database.
    Supports multi-league CSVs where each row has its own competition.

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

    # Show per-league breakdown
    if 'competition' in df.columns:
        print("\nPer league:")
        for comp, count in df.groupby('competition').size().items():
            print(f"    {comp}: {count} matches")

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
        # Skip rows with missing team data (e.g., TBD knockout matches)
        if pd.isna(row['home_team_id']) or pd.isna(row['away_team_id']):
            continue
        # Use per-row competition so each team gets the correct league
        comp = row['competition'] if pd.notna(row.get('competition')) else 'Unknown'
        teams.add((row['home_team_id'], row['home_team_name'], row['home_team_short'], comp))
        teams.add((row['away_team_id'], row['away_team_name'], row['away_team_short'], comp))

    # commit=False throughout: one transaction per batch instead of one per row.
    # At tens of thousands of rows the per-row commit *was* the load time.
    for team_id, team_name, team_short, competition in teams:
        db.add_team(
            api_id=int(team_id),
            name=team_name,
            short_name=team_short,
            competition=competition,
            commit=False,
        )
        teams_added += 1

    # Matches FK to teams, so this has to land before the match loop starts.
    db.session.commit()

    print(f"Added {teams_added} teams")

    # Step 2: Insert each match, mapping CSV columns to the Match model fields
    print("\nAdding matches...")
    for idx, row in df.iterrows():
        # Skip rows with missing team data (e.g., TBD knockout matches)
        if pd.isna(row['home_team_id']) or pd.isna(row['away_team_id']):
            continue

        # Build match dict — handle NaN values with pd.notna() checks
        match_data = {
            'api_id': int(row['match_id']),
            'home_team_api_id': int(row['home_team_id']),
            'away_team_api_id': int(row['away_team_id']),
            'season': int(row['season']),
            'matchday': int(row['matchday']) if pd.notna(row['matchday']) else None,
            'competition': row['competition'],
            'stage': row['stage'] if 'stage' in row and pd.notna(row.get('stage')) else 'REGULAR_SEASON',
            'date': pd.to_datetime(row['date']), # Parsing the date
            'status': row['status'],
            'home_score': int(row['home_score']) if pd.notna(row['home_score']) else None,
            'away_score': int(row['away_score']) if pd.notna(row['away_score']) else None,
            'winner': row['winner'] if pd.notna(row['winner']) else None
        }

        db.add_match(match_data, commit=False)
        matches_added += 1

        # Progress log every 100 matches; commit in chunks so a long load never
        # holds one enormous transaction open, and progress survives a crash.
        if (idx + 1) % 100 == 0:
            db.session.commit()
            print(f"    Processed {idx + 1}/{len(df)} matches...")

    print(f"\nAdded {matches_added} matches")

    db.session.commit()  # trailing partial chunk

    # Close database connection
    db.close()

    print("\nData loading complete!")
    print(f"    Teams: {teams_added}")
    print(f"    Matches: {matches_added}")

def load_standings_to_database(standings_path):
    """
    Load standings JSON into the database.
    Supports two formats:
        - New multi-league: keys are "Competition Name|season" (e.g., "Premier League|2025")
        - Old single-league: keys are just "season" (e.g., "2025")
    """
    print(f"Reading standings from {standings_path}...")

    with open(standings_path, 'r') as f:
        all_standings = json.load(f)

    db = DatabaseManager()
    count = 0

    for key, table in all_standings.items():
        # Parse key — new format: "Competition Name|season", old format: "season"
        if '|' in key:
            competition, season_str = key.split('|', 1)
            season = int(season_str)
        else:
            competition = 'Premier League'
            season = int(key)

        print(f"    Loading standings for {competition} {season}...")

        for entry in table:
            # Find team in database by api_id
            team = db.session.query(Team).filter_by(
                api_id=entry['team']['id']
            ).first()

            if team:
                db.add_standing(team.id, season, competition, commit=False, data={
                    'position': entry['position'],
                    'played': entry['playedGames'],
                    'won': entry['won'],
                    'drawn': entry['draw'],
                    'lost': entry['lost'],
                    'goals_for': entry['goalsFor'],
                    'goals_against': entry['goalsAgainst'],
                    'goal_difference': entry['goalDifference'],
                    'points': entry['points'],
                })
                count += 1
    db.session.commit()
    db.close()
    print(f"Loaded {count} standing entries")


def compute_cl_standings_from_matches():
    """
    Compute Champions League league phase standings from finished CL matches
    already in the database. The API doesn't provide CL standings, so we
    calculate them from match results (same approach as load_external_csv.py).
    """
    db = DatabaseManager()

    # Get all finished CL league phase matches
    cl_matches = db.session.query(Match).filter(
        Match.competition == 'UEFA Champions League',
        Match.stage == 'LEAGUE_STAGE',
        Match.status == 'FINISHED'
    ).all()

    if not cl_matches:
        print("No CL league phase matches found, skipping CL standings.")
        db.close()
        return

    print(f"\nComputing CL standings from {len(cl_matches)} league phase matches...")

    # Group by season
    seasons = {}
    for m in cl_matches:
        if m.season not in seasons:
            seasons[m.season] = []
        seasons[m.season].append(m)

    total = 0
    for season, matches in seasons.items():
        teams = {}
        for m in matches:
            for team_id in [m.home_team_id, m.away_team_id]:
                if team_id not in teams:
                    teams[team_id] = {
                        'played': 0, 'won': 0, 'drawn': 0, 'lost': 0,
                        'goals_for': 0, 'goals_against': 0, 'points': 0
                    }

            # Home team stats
            teams[m.home_team_id]['played'] += 1
            teams[m.home_team_id]['goals_for'] += m.home_score or 0
            teams[m.home_team_id]['goals_against'] += m.away_score or 0

            # Away team stats
            teams[m.away_team_id]['played'] += 1
            teams[m.away_team_id]['goals_for'] += m.away_score or 0
            teams[m.away_team_id]['goals_against'] += m.home_score or 0

            if m.winner == 'HOME_TEAM':
                teams[m.home_team_id]['won'] += 1
                teams[m.home_team_id]['points'] += 3
                teams[m.away_team_id]['lost'] += 1
            elif m.winner == 'AWAY_TEAM':
                teams[m.away_team_id]['won'] += 1
                teams[m.away_team_id]['points'] += 3
                teams[m.home_team_id]['lost'] += 1
            else:
                teams[m.home_team_id]['drawn'] += 1
                teams[m.home_team_id]['points'] += 1
                teams[m.away_team_id]['drawn'] += 1
                teams[m.away_team_id]['points'] += 1

        # Sort by points, then goal difference
        sorted_teams = sorted(
            teams.items(),
            key=lambda x: (x[1]['points'], x[1]['goals_for'] - x[1]['goals_against']),
            reverse=True
        )

        for position, (team_id, stats) in enumerate(sorted_teams, 1):
            db.add_standing(team_id, season, 'UEFA Champions League', commit=False, data={
                'position': position,
                'played': stats['played'],
                'won': stats['won'],
                'drawn': stats['drawn'],
                'lost': stats['lost'],
                'goals_for': stats['goals_for'],
                'goals_against': stats['goals_against'],
                'goal_difference': stats['goals_for'] - stats['goals_against'],
                'points': stats['points'],
            })
            total += 1

        db.session.commit()
        print(f"    CL {season}: {len(sorted_teams)} teams, top 3: ", end="")
        for pos, (tid, s) in enumerate(sorted_teams[:3], 1):
            team = db.session.query(Team).filter_by(id=tid).first()
            name = team.name if team else f"ID:{tid}"
            print(f"{pos}. {name} ({s['points']}pts)", end="  ")
        print()

    db.close()
    print(f"Computed {total} CL standing entries")


# Main execution — loads all processed CSVs into the database
if __name__ == "__main__":
    # Try the combined multi-league CSV first, fall back to single-league
    csv_path = '../data/processed/all_matches.csv'
    if not os.path.exists(csv_path):
        csv_path = '../data/processed/premier_league_matches.csv'

    load_csv_to_database(csv_path)

    # Load standings
    standings_path = '../data/processed/standings.json'
    if os.path.exists(standings_path):
        load_standings_to_database(standings_path)
    else:
        print("No standings file found, skipping.")

    # Compute CL standings from match results (API doesn't provide them)
    compute_cl_standings_from_matches()
