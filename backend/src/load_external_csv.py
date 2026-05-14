"""
Load External CSV Data — Multi-League Edition

Downloads and loads historical match data from football-data.co.uk
into the database. Supports multiple leagues across 5 countries,
both top division and second division.

Leagues supported:
    England:      Premier League (E0) + Championship (E1)
    Germany:      Bundesliga (D1) + 2. Bundesliga (D2)
    Italy:        Serie A (I1) + Serie B (I2)
    France:       Ligue 1 (F1) + Ligue 2 (F2)
    Spain:        La Liga (SP1) + Segunda (SP2)
    Belgium:      Jupiler Pro League (B1)
    Portugal:     Primeira Liga (P1)
    Turkey:       Süper Lig (T1)
    Scotland:     Premiership (SC0) + Championship (SC1)
    Netherlands:  Eredivisie (N1)

football-data.co.uk CSV columns used:
    - Date: Match date (DD/MM/YYYY)
    - HomeTeam / AwayTeam: Team names
    - FTHG / FTAG: Full-time home/away goals
    - FTR: Full-time result (H = Home win, D = Draw, A = Away win)

Usage:
    python load_external_csv.py              # Load all leagues
    python load_external_csv.py --league E0  # Load only Premier League
"""

import requests
import pandas as pd
import os
import sys
import time
from database import DatabaseManager, Team

# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# All leagues with their competition names and football-data.co.uk codes
# Format: code -> (competition name, country)
LEAGUES = {
    # England
    "E0": ("Premier League", "England"),
    "E1": ("Championship", "England"),
    # Germany
    "D1": ("Bundesliga", "Germany"),
    "D2": ("2. Bundesliga", "Germany"),
    # Italy
    "I1": ("Serie A", "Italy"),
    "I2": ("Serie B", "Italy"),
    # France
    "F1": ("Ligue 1", "France"),
    "F2": ("Ligue 2", "France"),
    # Spain
    "SP1": ("La Liga", "Spain"),
    "SP2": ("Segunda División", "Spain"),
    # Scotland
    "SC0": ("Scottish Premiership", "Scotland"),
    "SC1": ("Scottish Championship", "Scotland"),
    # Belgium
    "B1": ("Jupiler Pro League", "Belgium"),
    # Portugal
    "P1": ("Primeira Liga", "Portugal"),
    # Turkey
    "T1": ("Süper Lig", "Turkey"),
    # Netherlands Eredivisie — football-data.co.uk codes N1 as Dutch top flight.
    # (Was incorrectly labeled as "Eliteserien"/Norway here for a while — fix
    # applied 2026-05; existing rows migrated via one-off UPDATE.)
    "N1": ("Eredivisie", "Netherlands"),
}

# Seasons to download (season label -> URL code)
# Standard European leagues run Aug-May (e.g., 2020 = 2020-21 season)
SEASONS_STANDARD = {
    2018: "1819",
    2019: "1920",
    2020: "2021",
    2021: "2122",
    2022: "2223",
    2023: "2324",
    2024: "2425",
    2025: "2526",  # Current season (Aug 2025 – May 2026)
}

# Offset for synthetic IDs — each league gets its own range to avoid collisions
# API IDs from Football-Data.org are in the 500000+ range
# Format: league_code -> (team_id_offset, match_id_offset)
LEAGUE_ID_OFFSETS = {
    "E0": (100000, 1000000),   # England Premier League
    "E1": (150000, 1500000),   # England Championship
    "D1": (200000, 2000000),   # Germany Bundesliga
    "D2": (250000, 2500000),   # Germany 2. Bundesliga
    "I1": (300000, 3000000),   # Italy Serie A
    "I2": (350000, 3500000),   # Italy Serie B
    "F1": (400000, 4000000),   # France Ligue 1
    "F2": (450000, 4500000),   # France Ligue 2
    "N1": (500000, 5000000),   # Netherlands Eredivisie
    "SP1": (550000, 5500000),  # Spain La Liga
    "SP2": (600000, 6000000),  # Spain Segunda División
    "SC0": (650000, 6500000),  # Scotland Premiership
    "SC1": (700000, 7000000),  # Scotland Championship
    "B1":  (750000, 7500000),  # Belgium Jupiler Pro League
    "P1":  (800000, 8000000),  # Portugal Primeira Liga
    "T1":  (850000, 8500000),  # Turkey Süper Lig
}

# Team name mapping: football-data.co.uk name -> standard name
# Only needed for teams that appear in both CSV and API data (English teams)
# All other teams just get " FC" appended if not already present
TEAM_NAME_MAP = {
    # England — Premier League teams that need mapping to Football-Data.org names
    "Man United": "Manchester United FC",
    "Man City": "Manchester City FC",
    "Tottenham": "Tottenham Hotspur FC",
    "Newcastle": "Newcastle United FC",
    "Wolves": "Wolverhampton Wanderers FC",
    "West Ham": "West Ham United FC",
    "Sheffield United": "Sheffield United FC",
    "Brighton": "Brighton & Hove Albion FC",
    "Leicester": "Leicester City FC",
    "Leeds": "Leeds United FC",
    "Nott'm Forest": "Nottingham Forest FC",
    "Bournemouth": "AFC Bournemouth",
    "West Brom": "West Bromwich Albion FC",
    "Ipswich": "Ipswich Town FC",
    "Norwich": "Norwich City FC",
    "Watford": "Watford FC",
    "Burnley": "Burnley FC",
    "Southampton": "Southampton FC",
    "Brentford": "Brentford FC",
    "Crystal Palace": "Crystal Palace FC",
    "Aston Villa": "Aston Villa FC",
    "Everton": "Everton FC",
    "Fulham": "Fulham FC",
    "Arsenal": "Arsenal FC",
    "Chelsea": "Chelsea FC",
    "Liverpool": "Liverpool FC",
    "Luton": "Luton Town FC",
    # England — Championship teams
    "Birmingham": "Birmingham City FC",
    "Blackburn": "Blackburn Rovers FC",
    "Bolton": "Bolton Wanderers FC",
    "Bristol City": "Bristol City FC",
    "Cardiff": "Cardiff City FC",
    "Coventry": "Coventry City FC",
    "Derby": "Derby County FC",
    "Huddersfield": "Huddersfield Town FC",
    "Hull": "Hull City FC",
    "Middlesbrough": "Middlesbrough FC",
    "Millwall": "Millwall FC",
    "Plymouth": "Plymouth Argyle FC",
    "Portsmouth": "Portsmouth FC",
    "Preston": "Preston North End FC",
    "QPR": "Queens Park Rangers FC",
    "Reading": "Reading FC",
    "Rotherham": "Rotherham United FC",
    "Sheffield Weds": "Sheffield Wednesday FC",
    "Stoke": "Stoke City FC",
    "Sunderland": "Sunderland AFC",
    "Swansea": "Swansea City FC",
    "Wigan": "Wigan Athletic FC",
    # Germany
    "Bayern Munich": "FC Bayern München",
    "Dortmund": "Borussia Dortmund",
    "Leverkusen": "Bayer 04 Leverkusen",
    "RB Leipzig": "RB Leipzig",
    "M'gladbach": "Borussia Mönchengladbach",
    "Wolfsburg": "VfL Wolfsburg",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "Hoffenheim": "TSG 1899 Hoffenheim",
    "Werder Bremen": "SV Werder Bremen",
    "Mainz": "1. FSV Mainz 05",
    "Augsburg": "FC Augsburg",
    "Hertha": "Hertha BSC",
    "FC Koln": "1. FC Köln",
    "Schalke 04": "FC Schalke 04",
    "Freiburg": "SC Freiburg",
    "Fortuna Dusseldorf": "Fortuna Düsseldorf",
    "Paderborn": "SC Paderborn 07",
    "Union Berlin": "1. FC Union Berlin",
    "Stuttgart": "VfB Stuttgart",
    "Bielefeld": "Arminia Bielefeld",
    "Greuther Furth": "SpVgg Greuther Fürth",
    "Bochum": "VfL Bochum 1848",
    "Heidenheim": "1. FC Heidenheim 1846",
    "Darmstadt": "SV Darmstadt 98",
    "St Pauli": "FC St. Pauli",
    "Holstein Kiel": "Holstein Kiel",
    # Italy
    "Inter": "Inter Milan",
    "AC Milan": "AC Milan",
    "Juventus": "Juventus FC",
    "Napoli": "SSC Napoli",
    "Lazio": "SS Lazio",
    "Roma": "AS Roma",
    "Atalanta": "Atalanta BC",
    "Fiorentina": "ACF Fiorentina",
    "Torino": "Torino FC",
    "Monza": "AC Monza",
    "Verona": "Hellas Verona FC",
    "Udinese": "Udinese Calcio",
    "Genoa": "Genoa CFC",
    "Cagliari": "Cagliari Calcio",
    "Lecce": "US Lecce",
    "Empoli": "Empoli FC",
    "Sassuolo": "US Sassuolo Calcio",
    "Salernitana": "US Salernitana 1919",
    "Frosinone": "Frosinone Calcio",
    "Parma": "Parma Calcio 1913",
    "Como": "Como 1907",
    "Venezia": "Venezia FC",
    "Sampdoria": "UC Sampdoria",
    "Spezia": "Spezia Calcio",
    "Cremonese": "US Cremonese",
    "Bologna": "Bologna FC 1909",
    "Benevento": "Benevento Calcio",
    "Crotone": "FC Crotone",
    "Brescia": "Brescia Calcio",
    # France
    "Paris SG": "Paris Saint-Germain FC",
    "Marseille": "Olympique de Marseille",
    "Lyon": "Olympique Lyonnais",
    "Monaco": "AS Monaco FC",
    "Lille": "LOSC Lille",
    "Nice": "OGC Nice",
    "Rennes": "Stade Rennais FC",
    "Lens": "RC Lens",
    "Strasbourg": "RC Strasbourg Alsace",
    "Nantes": "FC Nantes",
    "Toulouse": "Toulouse FC",
    "Montpellier": "Montpellier HSC",
    "Brest": "Stade Brestois 29",
    "Reims": "Stade de Reims",
    "Le Havre": "Le Havre AC",
    "Metz": "FC Metz",
    "Lorient": "FC Lorient",
    "Clermont": "Clermont Foot 63",
    "Auxerre": "AJ Auxerre",
    "Angers": "Angers SCO",
    "St Etienne": "AS Saint-Étienne",
    "Bordeaux": "Girondins de Bordeaux",
    "Dijon": "Dijon FCO",
    "Nimes": "Nîmes Olympique",
    "Amiens": "Amiens SC",
    # Spain
    "Ath Madrid": "Atlético Madrid",
    "Ath Bilbao": "Athletic Club",
    "Espanol": "RCD Espanyol",
    "Betis": "Real Betis Balompié",
    "Sociedad": "Real Sociedad",
    "Vallecano": "Rayo Vallecano",
    "Celta": "RC Celta de Vigo",
    "Osasuna": "CA Osasuna",
    "Villarreal": "Villarreal CF",
    "Sevilla": "Sevilla FC",
    "Mallorca": "RCD Mallorca",
    "Valladolid": "Real Valladolid CF",
    "Alaves": "Deportivo Alavés",
    "Getafe": "Getafe CF",
    "Girona": "Girona FC",
    "Las Palmas": "UD Las Palmas",
    "Leganes": "CD Leganés",
    "Cadiz": "Cádiz CF",
    "Almeria": "UD Almería",
    "Elche": "Elche CF",
    "Levante": "Levante UD",
    "Huesca": "SD Huesca",
    "Eibar": "SD Eibar",
    "Granada": "Granada CF",
    # Scotland
    "Celtic": "Celtic FC",
    "Rangers": "Rangers FC",
    "Hearts": "Heart of Midlothian FC",
    "Hibernian": "Hibernian FC",
    "Aberdeen": "Aberdeen FC",
    "Motherwell": "Motherwell FC",
    "St Mirren": "St Mirren FC",
    "Kilmarnock": "Kilmarnock FC",
    "St Johnstone": "St Johnstone FC",
    "Ross County": "Ross County FC",
    "Dundee": "Dundee FC",
    "Livingston": "Livingston FC",
    # Belgium
    "Club Brugge": "Club Brugge KV",
    "Anderlecht": "RSC Anderlecht",
    "Genk": "KRC Genk",
    "Gent": "KAA Gent",
    "Antwerp": "Royal Antwerp FC",
    "Standard": "Standard Liège",
    "Charleroi": "Sporting Charleroi",
    "Mechelen": "KV Mechelen",
    "Cercle Brugge": "Cercle Brugge KSV",
    "St Truiden": "Sint-Truidense VV",
    "Kortrijk": "KV Kortrijk",
    "Eupen": "KAS Eupen",
    "Westerlo": "KVC Westerlo",
    "Oud-Heverlee Leuven": "OH Leuven",
    # Portugal
    "Benfica": "SL Benfica",
    "Sp Lisbon": "Sporting CP",
    "Porto": "FC Porto",
    "Braga": "SC Braga",
    "Guimaraes": "Vitória SC",
    "Famalicao": "FC Famalicão",
    "Gil Vicente": "Gil Vicente FC",
    "Boavista": "Boavista FC",
    "Santa Clara": "CD Santa Clara",
    "Maritimo": "CS Marítimo",
    "Estoril": "Estoril Praia",
    "Rio Ave": "Rio Ave FC",
    "Arouca": "FC Arouca",
    "Vizela": "FC Vizela",
    "Casa Pia": "Casa Pia AC",
    "Moreirense": "Moreirense FC",
    # Turkey
    "Galatasaray": "Galatasaray SK",
    "Fenerbahce": "Fenerbahçe SK",
    "Besiktas": "Beşiktaş JK",
    "Trabzonspor": "Trabzonspor FK",
    "Istanbul Basaksehir": "İstanbul Başakşehir FK",
    "Antalyaspor": "Antalyaspor",
    "Konyaspor": "Konyaspor FK",
    "Sivasspor": "Sivasspor FK",
    "Alanyaspor": "Alanyaspor FK",
    "Kasimpasa": "Kasımpaşa SK",
    "Kayserispor": "Kayserispor FK",
    "Gaziantep FK": "Gaziantep FK",
    "Hatayspor": "Hatayspor FK",
    "Rizespor": "Çaykur Rizespor",
    "Samsunspor": "Samsunspor FK",
    "Pendikspor": "Pendikspor FK",
}


# ============================================================
# Helper functions
# ============================================================

def download_csv(league_code, season_code, save_dir="../data/external"):
    """
    Download a single league/season CSV from football-data.co.uk

    Args:
        league_code (str): League code (e.g., "E0", "D1")
        season_code (str): Season code (e.g., "2324")
        save_dir (str): Directory to save downloaded files

    Returns:
        str: Path to saved CSV file, or None if download failed
    """
    os.makedirs(save_dir, exist_ok=True)

    url = f"{BASE_URL}/{season_code}/{league_code}.csv"
    save_path = os.path.join(save_dir, f"{league_code}_{season_code}.csv")

    # Skip if already downloaded
    if os.path.exists(save_path):
        print(f"      Already downloaded: {league_code}_{season_code}.csv")
        return save_path

    print(f"      Downloading {league_code} {season_code}...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Check if we got actual CSV data (not an error page)
        if len(response.content) < 100:
            print("      Empty response, skipping")
            return None

        with open(save_path, 'wb') as f:
            f.write(response.content)

        time.sleep(1)  # Be polite to the server
        return save_path

    except requests.exceptions.RequestException as e:
        print(f"      Download failed: {e}")
        return None


def normalize_team_name(name):
    """Map football-data.co.uk team name to our standard name format"""
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    # If name doesn't end with common suffixes, append FC
    suffixes = ('FC', 'SC', 'AC', 'BC', 'CF', 'SV', 'FK', 'IF', 'BK', 'IL')
    if not any(name.endswith(s) for s in suffixes):
        return f"{name} FC"
    return name


def generate_team_id(team_name, league_code):
    """Generate a consistent synthetic API ID from team name and league"""
    team_offset = LEAGUE_ID_OFFSETS[league_code][0]
    # Use abs(hash()) to get a positive consistent ID
    return team_offset + (abs(hash(team_name)) % 49000)


def generate_match_id(league_code, season, idx):
    """Generate unique match ID from league, season, and row index"""
    match_offset = LEAGUE_ID_OFFSETS[league_code][1]
    return match_offset + (season * 1000) + idx


def process_csv(csv_path, league_code, competition_name, season):
    """
    Read a football-data.co.uk CSV and convert to our standard format.

    Args:
        csv_path (str): Path to downloaded CSV
        league_code (str): League code (e.g., "E0")
        competition_name (str): Full competition name (e.g., "Premier League")
        season (int): Season start year

    Returns:
        pd.DataFrame: Processed matches in our standard format
    """
    try:
        df = pd.read_csv(csv_path, encoding='utf-8')
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding='latin-1')

    # Some CSVs use 'Home' and 'Away' instead of 'HomeTeam' and 'AwayTeam'
    if 'Home' in df.columns and 'HomeTeam' not in df.columns:
        df = df.rename(columns={'Home': 'HomeTeam', 'Away': 'AwayTeam'})

    # Check required columns exist
    required_cols = ['Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'FTR']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"      Missing columns: {missing}")
        return pd.DataFrame()

    processed = []

    for idx, row in df.iterrows():
        # Skip rows with missing essential data
        if pd.isna(row['FTHG']) or pd.isna(row['FTAG']) or pd.isna(row['HomeTeam']):
            continue

        home_name = normalize_team_name(str(row['HomeTeam']).strip())
        away_name = normalize_team_name(str(row['AwayTeam']).strip())

        # Map FTR (H/D/A) to our winner format
        ftr_map = {'H': 'HOME_TEAM', 'D': 'DRAW', 'A': 'AWAY_TEAM'}
        winner = ftr_map.get(row['FTR'])
        if not winner:
            continue

        # Parse date — football-data.co.uk uses DD/MM/YYYY or DD/MM/YY
        try:
            match_date = pd.to_datetime(row['Date'], dayfirst=True)
        except Exception:
            continue

        entry = {
            'match_id': generate_match_id(league_code, season, idx),
            'competition': competition_name,
            'season': season,
            'matchday': None,
            'date': match_date.isoformat(),
            'status': 'FINISHED',
            'home_team_id': generate_team_id(home_name, league_code),
            'home_team_name': home_name,
            'home_team_short': str(row['HomeTeam']).strip(),
            'away_team_id': generate_team_id(away_name, league_code),
            'away_team_name': away_name,
            'away_team_short': str(row['AwayTeam']).strip(),
            'home_score': int(row['FTHG']),
            'away_score': int(row['FTAG']),
            'winner': winner,
        }

        # Half-time scores (if available in CSV)
        if 'HTHG' in df.columns and pd.notna(row.get('HTHG')) and pd.notna(row.get('HTAG')):
            entry['home_ht_score'] = int(row['HTHG'])
            entry['away_ht_score'] = int(row['HTAG'])

        # Match statistics (if available in CSV)
        stat_cols = {
            'HS': 'home_shots', 'AS': 'away_shots',
            'HST': 'home_shots_on_target', 'AST': 'away_shots_on_target',
            'HC': 'home_corners', 'AC': 'away_corners',
            'HF': 'home_fouls', 'AF': 'away_fouls',
            'HY': 'home_yellow_cards', 'AY': 'away_yellow_cards',
            'HR': 'home_red_cards', 'AR': 'away_red_cards',
        }
        for csv_col, db_col in stat_cols.items():
            if csv_col in row and pd.notna(row[csv_col]):
                entry[db_col] = int(row[csv_col])

        # Betting odds — use market average (AvgH/D/A), convert to implied probabilities
        avg_h = row.get('AvgH') if 'AvgH' in df.columns else None
        avg_d = row.get('AvgD') if 'AvgD' in df.columns else None
        avg_a = row.get('AvgA') if 'AvgA' in df.columns else None

        if pd.notna(avg_h) and pd.notna(avg_d) and pd.notna(avg_a):
            avg_h, avg_d, avg_a = float(avg_h), float(avg_d), float(avg_a)
            if avg_h > 0 and avg_d > 0 and avg_a > 0:
                # Convert decimal odds to implied probabilities, then normalize (remove vig)
                raw_h, raw_d, raw_a = 1/avg_h, 1/avg_d, 1/avg_a
                total = raw_h + raw_d + raw_a
                entry['avg_home_prob'] = round(raw_h / total, 4)
                entry['avg_draw_prob'] = round(raw_d / total, 4)
                entry['avg_away_prob'] = round(raw_a / total, 4)

        # Raw Bet365 odds (for reference)
        b365_h = row.get('B365H') if 'B365H' in df.columns else None
        b365_d = row.get('B365D') if 'B365D' in df.columns else None
        b365_a = row.get('B365A') if 'B365A' in df.columns else None
        if pd.notna(b365_h):
            entry['b365_home'] = float(b365_h)
        if pd.notna(b365_d):
            entry['b365_draw'] = float(b365_d)
        if pd.notna(b365_a):
            entry['b365_away'] = float(b365_a)

        processed.append(entry)

    return pd.DataFrame(processed)


def compute_standings_from_matches(df, season):
    """
    Compute league standings from match results for a given season.

    Args:
        df (pd.DataFrame): Matches for one season
        season (int): Season year

    Returns:
        list: Standing entries ready for database insertion
    """
    teams = {}

    for _, row in df.iterrows():
        home = row['home_team_name']
        away = row['away_team_name']

        for team in [home, away]:
            if team not in teams:
                teams[team] = {
                    'played': 0, 'won': 0, 'drawn': 0, 'lost': 0,
                    'goals_for': 0, 'goals_against': 0, 'points': 0,
                    'team_id': row['home_team_id'] if team == home else row['away_team_id']
                }

        h_goals = int(row['home_score'])
        a_goals = int(row['away_score'])

        # Home team
        teams[home]['played'] += 1
        teams[home]['goals_for'] += h_goals
        teams[home]['goals_against'] += a_goals

        # Away team
        teams[away]['played'] += 1
        teams[away]['goals_for'] += a_goals
        teams[away]['goals_against'] += h_goals

        if row['winner'] == 'HOME_TEAM':
            teams[home]['won'] += 1
            teams[home]['points'] += 3
            teams[away]['lost'] += 1
        elif row['winner'] == 'AWAY_TEAM':
            teams[away]['won'] += 1
            teams[away]['points'] += 3
            teams[home]['lost'] += 1
        else:
            teams[home]['drawn'] += 1
            teams[home]['points'] += 1
            teams[away]['drawn'] += 1
            teams[away]['points'] += 1

    sorted_teams = sorted(
        teams.items(),
        key=lambda x: (x[1]['points'], x[1]['goals_for'] - x[1]['goals_against']),
        reverse=True
    )

    standings = []
    for position, (name, stats) in enumerate(sorted_teams, 1):
        standings.append({
            'team_api_id': stats['team_id'],
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

    return standings


def load_league_data(db, league_code, competition_name, seasons_dict):
    """
    Download, process, and load one league's data for all seasons.

    Args:
        db (DatabaseManager): Database connection
        league_code (str): League code (e.g., "E0")
        competition_name (str): Full name (e.g., "Premier League")
        seasons_dict (dict): Season year -> season code mapping

    Returns:
        tuple: (total_matches, total_standings) loaded
    """
    total_matches = 0
    total_standings = 0

    for season, code in seasons_dict.items():
        # Download CSV
        csv_path = download_csv(league_code, code)
        if not csv_path:
            continue

        # Process into standard format
        season_df = process_csv(csv_path, league_code, competition_name, season)
        if season_df.empty:
            continue

        # Add teams
        teams_seen = set()
        for _, row in season_df.iterrows():
            for prefix in ['home', 'away']:
                team_key = (row[f'{prefix}_team_id'], row[f'{prefix}_team_name'])
                if team_key not in teams_seen:
                    db.add_team(
                        api_id=int(row[f'{prefix}_team_id']),
                        name=row[f'{prefix}_team_name'],
                        short_name=row[f'{prefix}_team_short'],
                        competition=competition_name
                    )
                    teams_seen.add(team_key)

        # Add matches (including stats + odds if present)
        extra_cols = [
            'home_ht_score', 'away_ht_score',
            'home_shots', 'away_shots', 'home_shots_on_target', 'away_shots_on_target',
            'home_corners', 'away_corners', 'home_fouls', 'away_fouls',
            'home_yellow_cards', 'away_yellow_cards', 'home_red_cards', 'away_red_cards',
            'b365_home', 'b365_draw', 'b365_away',
            'avg_home_prob', 'avg_draw_prob', 'avg_away_prob',
        ]
        for _, row in season_df.iterrows():
            match_data = {
                'api_id': int(row['match_id']),
                'home_team_api_id': int(row['home_team_id']),
                'away_team_api_id': int(row['away_team_id']),
                'season': int(row['season']),
                'matchday': None,
                'competition': row['competition'],
                'stage': 'REGULAR_SEASON',
                'date': pd.to_datetime(row['date']),
                'status': 'FINISHED',
                'home_score': int(row['home_score']),
                'away_score': int(row['away_score']),
                'winner': row['winner'],
            }
            # Pass through extra columns (stats + odds)
            for col in extra_cols:
                if col in row and pd.notna(row[col]):
                    match_data[col] = row[col]
            db.add_match(match_data)

        total_matches += len(season_df)

        # Compute and load standings
        standings = compute_standings_from_matches(season_df, season)
        for entry in standings:
            team = db.session.query(Team).filter_by(
                api_id=entry['team_api_id']
            ).first()

            if team:
                db.add_standing(team.id, season, competition_name, {
                    'position': entry['position'],
                    'played': entry['played'],
                    'won': entry['won'],
                    'drawn': entry['drawn'],
                    'lost': entry['lost'],
                    'goals_for': entry['goals_for'],
                    'goals_against': entry['goals_against'],
                    'goal_difference': entry['goal_difference'],
                    'points': entry['points'],
                })
                total_standings += 1

        print(f"      Season {season}: {len(season_df)} matches, {len(standings)} standings")

    return total_matches, total_standings


def load_all_external_data(only_league=None):
    """
    Main function: download, process, and load all leagues and seasons.

    Args:
        only_league (str): If set, only load this league code (e.g., "E0")
    """
    print("=" * 60)
    print("LOADING EXTERNAL CSV DATA (football-data.co.uk)")
    print("=" * 60)

    db = DatabaseManager()
    grand_total_matches = 0
    grand_total_standings = 0

    for league_code, (competition_name, country) in LEAGUES.items():
        # Skip if filtering to a specific league
        if only_league and league_code != only_league:
            continue

        print(f"\n{'='*60}")
        print(f"  {competition_name} ({country}) [{league_code}]")
        print(f"{'='*60}")

        # N1 uses the standard August–May calendar like the rest of the
        # European leagues (it's Eredivisie, not Norway as previously mislabeled).
        seasons = SEASONS_STANDARD

        matches, standings = load_league_data(
            db, league_code, competition_name, seasons
        )

        grand_total_matches += matches
        grand_total_standings += standings

        print(f"  Total: {matches} matches, {standings} standings")

    db.close()

    print("\n" + "=" * 60)
    print("EXTERNAL DATA LOADING COMPLETE")
    print("=" * 60)
    print(f"    Total matches loaded: {grand_total_matches}")
    print(f"    Total standing entries: {grand_total_standings}")
    print("\nNext steps:")
    print("    1. Run: python feature_engineering.py")
    print("    2. Run: python model_training.py")


if __name__ == "__main__":
    # Optional: pass --league E0 to load only one league
    only_league = None
    if "--league" in sys.argv:
        idx = sys.argv.index("--league")
        if idx + 1 < len(sys.argv):
            only_league = sys.argv[idx + 1].upper()
            print(f"Loading only league: {only_league}")

    load_all_external_data(only_league=only_league)
