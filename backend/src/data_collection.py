"""
Data Collection Module

Fetches football match data from Football-Data.org API
"""

import requests
import pandas as pd
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

# Load enviroment variables
load_dotenv()

class FootballDataCollecor:
    # Handles data colection from Football-Data API

    def __init__(self, api_key=None):
        """
        Initialize the data colector

        Args:
            api_key (str): API key for Football-Data.org
        """
        self.api_key = api_key or os.getenv('FOOTBALL_API_KEY')
        self.base_url = 'https://api.football-data.org/v4'
        self.headers = {'X-Auth-Token': self.api_key}
        self.rate_limit_delay = 6 # seconds (10 request/min = 6s between)

    def _make_requests(self, endpoint, params=None):
        """
        Make API request with error handling and rate limiting

        Args:
            endpoint (str): API endpoint (e.g., '/competitions)
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
    
    def get_tea(self, team_id):
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
            season (list): List of season years
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
                print(f"    Retrived {len(matches_df)} matches")
            else:
                print(f"    No data for season {season}")

        # Combine all seasons
        if all_matches:
            combined_df = pd.concat(all_matches, ignore_index=True)

            # Filter only finished matched (only need the scores)
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
        Flatten nested JSON structure for easier analysis

        Args:
            matches_df (pd.DataFrame): Raw matches structure

        Returns:
            pd.DataFrame: Flattened dataframe
        """
        # Create a new dataframe with flattened structure
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

                    # Awey team
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
    
# Main execution
if __name__ == "__main__":
    # Initalize collector
    collector = FootballDataCollecor()

    # Competitions IDs (from API documentation)
    PREMIER_LEAGE = 2021
    LA_LIGA = 2014
    BUNDESLIGA = 2002
    SERIE_A = 2019
    LIGUE_1 = 2015

    # Collect Premier League data for last 3 seasons
    print("=" * 60)
    print("FOOTBALL DATA COLLECTION")
    print("=" * 60)

    seasons = [2021, 2022, 2023] # Seasons

    raw_data = collector.collect_historical_data(
        competition_id=PREMIER_LEAGE,
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
    print(f"Total recors: {len(flattened_data)}")

    # Show sample
    print("\nSample data:")
    print(flattened_data.head())

    print("\nData collection complete!")
