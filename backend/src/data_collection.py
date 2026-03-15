"""
Data Collection Module — Multi-League Edition

Fetches football match data from the Football-Data.org API (v4).
Free tier allows 10 requests/minute, so we enforce a 6-second delay between calls.

Supported competitions (free tier):
    - Premier League (2021)
    - Championship (2016)
    - Bundesliga (2002)
    - Serie A (2019)
    - La Liga (2014)
    - Ligue 1 (2015)
    - Primeira Liga / Portugal (2017)

Data pipeline:
    1. Fetch raw match data from API (nested JSON) for each competition
    2. Flatten nested structure into tabular format
    3. Save combined processed CSV + standings JSON
    4. load_data.py loads these into the database
"""

import requests
import pandas as pd
import time
from dotenv import load_dotenv
from datetime import datetime
import json
import os

# Load environment variables (reads FOOTBALL_API_KEY from .env)
load_dotenv()

# ============================================================
# Competition IDs — Football-Data.org API free tier
# ============================================================

COMPETITIONS = {
    # Domestic leagues (free tier)
    2021: "Premier League",
    2016: "Championship",
    2002: "Bundesliga",
    2019: "Serie A",
    2014: "La Liga",
    2015: "Ligue 1",
    2017: "Primeira Liga",
    2003: "Eredivisie",
    # European competitions (free tier)
    2001: "UEFA Champions League",
}

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

            # Save to file if specified
            if output_file:
                combined_df.to_csv(output_file, index=False)
                print(f"Data saved to {output_file}")
            return combined_df

        return pd.DataFrame()

    def collect_standings_data(self, competition_id, season):
        """
        Fetch league standings for a season

        Returns:
            list: Standing entries from API (position ,team, points, etc.)
        """
        data = self._make_requests(
            f'/competitions/{competition_id}/standings',
            {'season': season}
        )
        if data and 'standings' in data:
            # First entry is 'TOTAL' standings (not home/away split)
            return data['standings'][0]['table']
        return []

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
                # Determine stage — domestic leagues use REGULAR_SEASON
                # European competitions use GROUP_STAGE, LEAGUE_STAGE, LAST_16, etc.
                stage = match.get('stage', 'REGULAR_SEASON')

                flat_match = {
                    'match_id': match['id'],
                    'competition': match['competition']['name'],
                    'season': match['season'],
                    'matchday': match.get('matchday', None),
                    'stage': stage,
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

                    # Score — None for SCHEDULED/TIMED matches that haven't been played yet
                    'home_score': match['score']['fullTime']['home'] if match['score']['fullTime'] else None,
                    'away_score': match['score']['fullTime']['away'] if match['score']['fullTime'] else None,
                    'winner': match['score']['winner'] if match['score'] else None,

                    # Additional info
                    'duration': match['score'].get('duration', 'REGULAR'),
                }

                flattened.append(flat_match)

            except (KeyError, TypeError) as e:
                print(f"Error processing match {match.get('id', 'unknown')}: {e}")
                continue

        return pd.DataFrame(flattened)

    def get_upcoming_fixtures(self, days=7):
        """
        Fetch upcoming fixtures across all free-tier competitions.

        Uses the /v4/matches endpoint with date range filter.
        Returns a list of flattened match dicts ready for db.add_match().

        Args:
            days: Number of days ahead to fetch (default 7)

        Returns:
            list[dict]: Flattened fixture dicts
        """
        from datetime import timedelta

        # API allows max 10-day window per request, so split into chunks
        all_matches = []
        chunk_size = 10
        start = datetime.now()
        remaining = days

        while remaining > 0:
            chunk_days = min(remaining, chunk_size)
            date_from = start.strftime('%Y-%m-%d')
            date_to = (start + timedelta(days=chunk_days)).strftime('%Y-%m-%d')

            data = self._make_requests('/matches', {
                'dateFrom': date_from,
                'dateTo': date_to,
            })

            if data and 'matches' in data:
                all_matches.extend(data['matches'])

            start += timedelta(days=chunk_days)
            remaining -= chunk_days

        if not all_matches:
            return []

        fixtures = []
        for match in all_matches:
            try:
                comp_name = match['competition']['name']
                # Only include free-tier competitions
                comp_id = match['competition']['id']
                if comp_id not in COMPETITIONS:
                    continue

                season_year = match.get('season', {}).get('startDate', '')[:4]
                if season_year:
                    season_year = int(season_year)
                else:
                    now = datetime.now()
                    season_year = now.year if now.month >= 8 else now.year - 1

                fixtures.append({
                    'api_id': match['id'],
                    'competition': comp_name,
                    'season': season_year,
                    'matchday': match.get('matchday'),
                    'stage': match.get('stage', 'REGULAR_SEASON'),
                    'date': match['utcDate'],
                    'status': match['status'],
                    'home_team_api_id': match['homeTeam']['id'],
                    'home_team_name': match['homeTeam']['name'],
                    'home_team_short': match['homeTeam'].get('shortName', ''),
                    'away_team_api_id': match['awayTeam']['id'],
                    'away_team_name': match['awayTeam']['name'],
                    'away_team_short': match['awayTeam'].get('shortName', ''),
                    'home_score': None,
                    'away_score': None,
                    'winner': None,
                })
            except (KeyError, TypeError) as e:
                print(f"Error processing fixture {match.get('id', '?')}: {e}")
                continue

        print(f"Fetched {len(fixtures)} upcoming fixtures ({date_from} to {date_to})")
        return fixtures


# ============================================================
# Main execution — multi-league data pipeline
# ============================================================
if __name__ == "__main__":
    # Initialize collector (reads API key from .env)
    collector = FootballDataCollector()

    # Football seasons start in August — before August, current season = previous year
    current_year = datetime.now().year
    current_season = current_year if datetime.now().month >= 8 else current_year - 1

    # Fetch current season for each competition (free tier returns current season data)
    seasons = [current_season]

    # Ensure output directories exist
    os.makedirs('../data/raw', exist_ok=True)
    os.makedirs('../data/processed', exist_ok=True)

    all_flattened = []       # Combined flattened matches across all leagues
    all_standings = {}       # Combined standings: { "comp_id-season": [...] }

    for comp_id, comp_name in COMPETITIONS.items():
        print("\n" + "=" * 60)
        print(f"  {comp_name} (ID: {comp_id})")
        print("=" * 60)

        # Step 1: Fetch matches
        raw_data = collector.collect_historical_data(
            competition_id=comp_id,
            seasons=seasons,
            output_file=f'../data/raw/{comp_name.lower().replace(" ", "_")}_raw.csv'
        )

        if raw_data.empty:
            print(f"  No data retrieved for {comp_name}, skipping...")
            continue

        # Step 2: Flatten match data
        flattened = collector.flatten_match_data(raw_data)
        all_flattened.append(flattened)
        print(f"  Flattened: {len(flattened)} matches")

        # Step 3: Fetch standings
        for season in seasons:
            print(f"  Fetching standings for {season}...")
            table = collector.collect_standings_data(comp_id, season)
            if table:
                # Key by "competition_name-season" for load_data.py
                key = f"{comp_name}|{season}"
                all_standings[key] = table
                print(f"  Got {len(table)} teams in standings")

    # Step 4: Save combined flattened CSV
    if all_flattened:
        combined_df = pd.concat(all_flattened, ignore_index=True)
        output_path = '../data/processed/all_matches.csv'
        combined_df.to_csv(output_path, index=False)

        # Count by status
        finished = len(combined_df[combined_df['status'] == 'FINISHED'])
        scheduled = len(combined_df[combined_df['status'].isin(['SCHEDULED', 'TIMED'])])

        print("\n" + "=" * 60)
        print("COMBINED RESULTS")
        print("=" * 60)
        print(f"  Total matches: {len(combined_df)}")
        print(f"  FINISHED: {finished}")
        print(f"  SCHEDULED/TIMED: {scheduled}")
        print(f"  Competitions: {combined_df['competition'].nunique()}")
        print(f"  Saved to: {output_path}")

        # Show per-league breakdown
        print("\n  Per league:")
        for comp, count in combined_df.groupby('competition').size().items():
            sched = len(combined_df[(combined_df['competition'] == comp) &
                                     (combined_df['status'].isin(['SCHEDULED', 'TIMED']))])
            print(f"    {comp}: {count} matches ({sched} upcoming)")

    # Step 5: Save combined standings JSON
    if all_standings:
        standings_path = '../data/processed/standings.json'
        with open(standings_path, 'w') as f:
            json.dump(all_standings, f, indent=2)
        print(f"\n  Standings saved to {standings_path}")
        print(f"  Total entries: {len(all_standings)} league-seasons")

    print("\n" + "=" * 60)
    print("DATA COLLECTION COMPLETE!")
    print("=" * 60)
