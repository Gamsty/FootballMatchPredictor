"""
Data Collection Module

Fetches football match data from the Football-Data.org API (v4).
Free tier allows 10 requests/minute, so we enforce a 6-second delay between calls.

Data pipeline:
    1. Fetch raw match data from API (nested JSON) -> save to data/raw/
    2. Flatten nested structure into tabular format -> save to data/processed/
    3. Processed CSV is used by feature engineering and model training
"""

import requests
import pandas as pd
import time
from dotenv import load_dotenv
from datetime import datetime
import os

# Load environment variables (reads FOOTBALL_API_KEY from .env)
load_dotenv()

class FootballDataCollector:
    """Handles data collection from the Football-Data.org API."""

    def __init__(self, api_key=None):
        """
        Initialize the data collector.

        Args:
            api_key (str): API key for Football-Data.org
        """
        self.api_key = api_key or os.getenv('FOOTBALL_API_KEY')
        self.base_url = 'https://api.football-data.org/v4'
        self.headers = {'X-Auth-Token': self.api_key}
        # Free tier: 10 requests/min, so we wait 6 seconds between API calls
        self.rate_limit_delay = 6

    def _make_requests(self, endpoint, params=None):
        """
        Make API request with error handling and rate limiting

        Args:
            endpoint (str): API endpoint (e.g., '/competitions')
            params (dict): Query parameters

        Returns:
            dict: JSON response
        """
        url = f"{self.base_url}{endpoint}"

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status() # Raise exception for bad status codes

            # Rate limiting
            time.sleep(self.rate_limit_delay)

            return response.json()

        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e}")
            print(f"Response: {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Request Error: {e}")
            return None
        
    def get_competitions(self):
        """
        Get list of available competitions

        Returns:
            pd.DataFrame: Competitions data
        """
        data = self._make_requests('/competitions')
        if data and 'competitions' in data:
            return pd.DataFrame(data['competitions'])
        return pd.DataFrame()
    
    def get_matches(self, competition_id, season):
        """
        Get all matches for a specific competition and season

        Args:
            competition_id (int): Competition ID (e.g., 2021 for premier League)
            season (int): Season year (e.g., 2023)

        Returns:
            pd.DataFrame: Matches data
        """
        endpoint = f'/competitions/{competition_id}/matches'
        params = {'seasons': season}

        data = self._make_requests(endpoint, params)

        if data and 'matches' in data:
            return pd.DataFrame(data['matches'])
        return pd.DataFrame()
    
    def get_team(self, team_id):
        """
        Get team details

        Args:
            team_id (int): Team ID

        Returns:
            dict: Team data
        """
        endpoint = f'/teams/{team_id}'
        return self._make_requests(endpoint)
    
    def get_standings(self, competition_id, season):
        """
        Get league standings

        Args:
            competition_id (int): Competition ID
            season (int): Season year

        Returns:
            dict: Standings data
        """

        endpoint = f'/competitions/{competition_id}/standings'
        params = {'season': season}
        return self._make_requests(endpoint, params)
    
    def collect_historical_data(self, competition_id, seasons, output_file=None):
        """
        Collect multiple seasons of historical data

        Args:
            competition_id (int): Competition ID
            seasons (list): List of season years
            output_file (str): Path to save CSV file

        Returns:
            pd.DataFrame: Combined matches data
        """
        all_matches = []

        print(f"Collecting data for competition {competition_id}...")

        for season in seasons:
            print(f"    Fetching season {season}...")

            matches_df = self.get_matches(competition_id, season)

            if not matches_df.empty:
                # Add season column
                matches_df['season'] = season
                all_matches.append(matches_df)
                print(f"    Retrieved {len(matches_df)} matches")
            else:
                print(f"    No data for season {season}")

        # Combine all seasons
        if all_matches:
            combined_df = pd.concat(all_matches, ignore_index=True)

            # Filter only finished matches (we only need completed games with final scores)
            finished_matches = combined_df[combined_df['status'] == 'FINISHED'].copy()

            print(f"\nTotal matches: {len(combined_df)}")
            print(f"Finished matches: {len(finished_matches)}")

            # Save to file if specified
            if output_file:
                finished_matches.to_csv(output_file, index=False)
                print(f"Data saved to {output_file}")

            return finished_matches
        
        return pd.DataFrame()
    
    def flatten_match_data(self, matches_df):
        """
        Flatten nested JSON structure into flat columns for ML processing.

        The API returns deeply nested dicts (e.g., match['homeTeam']['name']).
        This extracts all relevant fields into a single-level dictionary per match,
        making it suitable for DataFrame operations and model training.

        Args:
            matches_df (pd.DataFrame): Raw matches with nested JSON columns

        Returns:
            pd.DataFrame: Flattened dataframe with one column per field
        """
        flattened = []

        for _, match in matches_df.iterrows():
            try:
                flat_match = {
                    'match_id': match['id'],
                    'competition': match['competition']['name'],
                    'season': match['season'],
                    'matchday': match.get('matchday', None),
                    'date': match['utcDate'],
                    'status': match['status'],

                    # Home team
                    'home_team_id': match['homeTeam']['id'],
                    'home_team_name': match['homeTeam']['name'],
                    'home_team_short': match['homeTeam'].get('shortName', ''),

                    # Away team
                    'away_team_id': match['awayTeam']['id'],
                    'away_team_name': match['awayTeam']['name'],
                    'away_team_short': match['awayTeam'].get('shortName', ''),

                    # Score
                    'home_score': match['score']['fullTime']['home'],
                    'away_score': match['score']['fullTime']['away'],
                    'winner': match['score']['winner'],

                    # Additional info
                    'duration': match['score'].get('duration', 'REGULAR'),
                }

                flattened.append(flat_match)
            
            except (KeyError, TypeError) as e:
                print(f"Error processing match {match.get('id', 'unknown')}: {e}")
                continue
            
        return pd.DataFrame(flattened)
    
# Main execution — runs the full data pipeline:
# 1. Fetch raw match data from API for specified seasons
# 2. Save raw data to CSV (data/raw/)
# 3. Flatten nested JSON into tabular format
# 4. Save processed data to CSV (data/processed/)
if __name__ == "__main__":
    # Initialize collector (reads API key from .env)
    collector = FootballDataCollector()

    # Competition IDs (from Football-Data.org API documentation)
    PREMIER_LEAGUE = 2021
    LA_LIGA = 2014
    BUNDESLIGA = 2002
    SERIE_A = 2019
    LIGUE_1 = 2015

    # Collect Premier League data for last 3 seasons
    print("=" * 60)
    print("FOOTBALL DATA COLLECTION")
    print("=" * 60)

    # Dynamically get current + last 2 seasons
    # Football seasons start in August — before August, current season = previous year
    current_year = datetime.now().year
    current_season = current_year if datetime.now().month >= 8 else current_year - 1
    seasons = list(range(current_season - 2, current_season + 1))
    print(f"Collecting seasons: {seasons}")

    raw_data = collector.collect_historical_data(
        competition_id=PREMIER_LEAGUE,
        seasons=seasons,
        output_file='../data/raw/premier_league_raw.csv'
    )

    print("\n" + "=" * 60)
    print("FLATTENING DATA")
    print("=" * 60)

    # Flatten the data
    flattened_data = collector.flatten_match_data(raw_data)

    # Save flattened data
    output_path = '../data/processed/premier_league_matches.csv'
    flattened_data.to_csv(output_path, index=False)

    print(f"\nFlattened data saved to {output_path}")
    print(f"Total records: {len(flattened_data)}")

    # Show sample
    print("\nSample data:")
    print(flattened_data.head())

    print("\nData collection complete!")
