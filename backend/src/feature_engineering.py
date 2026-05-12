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
from collections import defaultdict
from datetime import datetime
from database import DatabaseManager, Match, Team, MatchFeatures, Standing
from sqlalchemy import and_, or_

# League configuration — teams per league and relegation zone start position
LEAGUE_CONFIG = {
    "Premier League": {"teams": 20, "relegation_start": 18},
    "Championship": {"teams": 24, "relegation_start": 22},
    "Bundesliga": {"teams": 18, "relegation_start": 16},
    "2. Bundesliga": {"teams": 18, "relegation_start": 16},
    "Serie A": {"teams": 20, "relegation_start": 18},
    "Serie B": {"teams": 20, "relegation_start": 18},
    "Ligue 1": {"teams": 18, "relegation_start": 16},
    "Ligue 2": {"teams": 20, "relegation_start": 18},
    "La Liga": {"teams": 20, "relegation_start": 18},
    "Segunda División": {"teams": 22, "relegation_start": 19},
    "Scottish Premiership": {"teams": 12, "relegation_start": 11},
    "Scottish Championship": {"teams": 10, "relegation_start": 9},
    "Jupiler Pro League": {"teams": 16, "relegation_start": 15},
    "Primeira Liga": {"teams": 18, "relegation_start": 16},
    "Eredivisie": {"teams": 18, "relegation_start": 16},
    "Süper Lig": {"teams": 18, "relegation_start": 16},
    "Eliteserien": {"teams": 16, "relegation_start": 14},
    "UEFA Champions League": {"teams": 36, "relegation_start": 25},
}

# Teams that play on artificial turf (home advantage)
ARTIFICIAL_PITCH_TEAMS = {
    # Norway
    "FK Bodo/Glimt FC", "Bodø/Glimt FC", "Bodo/Glimt FC",
    "Kristiansund FC", "Kristiansund BK",
    "Sarpsborg 08 FC", "Sarpsborg 08 FF",
    # Scotland
    "Kilmarnock FC", "Livingston FC", "Hamilton Academical FC",
    # Switzerland (if added later)
    "BSC Young Boys",
}

class FeatureEngineer:
    """
    Creates ML features from raw match data.
    All features are computed using only historical data (no future leakage).
    """

    def __init__(self):
        # DatabaseManager provides access to teams, matches, and match_features tables
        self.db = DatabaseManager()
        # In-memory match cache for bulk processing (populated by _build_match_cache)
        self._cache_built = False
        self._team_home_matches = defaultdict(list)  # team_id -> [(date, match)] sorted by date
        self._team_away_matches = defaultdict(list)
        self._team_all_matches = defaultdict(list)
        self._h2h_matches = defaultdict(list)  # (team1, team2) sorted key -> [(date, match)]
        self._standings_cache = {}  # (team_id, season, competition) -> Standing

    def close(self):
        """Close database connection"""
        self.db.close()

    def _build_match_cache(self):
        """Load all finished matches into memory and build per-team indexes.
        This eliminates ~10 DB queries per match during bulk feature engineering."""
        if self._cache_built:
            return

        print("Building in-memory match cache...")
        all_matches = self.db.session.query(Match).filter(
            Match.status == 'FINISHED'
        ).order_by(Match.date).all()

        for m in all_matches:
            self._team_home_matches[m.home_team_id].append(m)
            self._team_away_matches[m.away_team_id].append(m)
            self._team_all_matches[m.home_team_id].append(m)
            self._team_all_matches[m.away_team_id].append(m)
            # H2H key: sorted tuple so (A,B) and (B,A) map to same list
            h2h_key = tuple(sorted([m.home_team_id, m.away_team_id]))
            self._h2h_matches[h2h_key].append(m)

        # Sort all lists by date (should already be sorted, but ensure it)
        for tid in self._team_all_matches:
            self._team_all_matches[tid].sort(key=lambda m: m.date)
        for tid in self._team_home_matches:
            self._team_home_matches[tid].sort(key=lambda m: m.date)
        for tid in self._team_away_matches:
            self._team_away_matches[tid].sort(key=lambda m: m.date)
        for key in self._h2h_matches:
            self._h2h_matches[key].sort(key=lambda m: m.date)

        # Cache all standings
        all_standings = self.db.session.query(Standing).all()
        for s in all_standings:
            self._standings_cache[(s.team_id, s.season, s.competition)] = s

        print(f"Cache built: {len(all_matches)} matches, {len(all_standings)} standings")
        self._cache_built = True

    def _get_before(self, match_list, before_date, last_n):
        """Get last N matches before a date from a sorted list (binary search)."""
        # Find insertion point using binary search
        import bisect
        # Create a key list for bisect (dates only)
        idx = bisect.bisect_left([m.date for m in match_list], before_date)
        # Take last_n items before idx
        start = max(0, idx - last_n)
        return match_list[start:idx]

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
    
    def calculate_draw_rate(self, team_id, before_date, last_n=10):
        """Calculate how often a team draws (last N matches)."""
        matches = self.db.session.query(Match).filter(
            and_(
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                Match.date < before_date,
                Match.status == 'FINISHED'
            )
        ).order_by(Match.date.desc()).limit(last_n).all()

        if not matches:
            return 0.0
        draws = sum(1 for m in matches if m.winner == 'DRAW')
        return float(draws / len(matches))

    def calculate_clean_sheet_rate(self, team_id, before_date, home=True, last_n=5):
        """Calculate clean sheet rate (matches where team conceded 0 goals)."""
        if home:
            matches = self.db.session.query(Match).filter(
                and_(Match.home_team_id == team_id, Match.date < before_date, Match.status == 'FINISHED')
            ).order_by(Match.date.desc()).limit(last_n).all()
            clean = sum(1 for m in matches if m.away_score == 0)
        else:
            matches = self.db.session.query(Match).filter(
                and_(Match.away_team_id == team_id, Match.date < before_date, Match.status == 'FINISHED')
            ).order_by(Match.date.desc()).limit(last_n).all()
            clean = sum(1 for m in matches if m.home_score == 0)

        return float(clean / len(matches)) if matches else 0.0

    def calculate_weighted_form(self, team_id, before_date, last_n=5):
        """Calculate exponentially weighted form — recent matches count more."""
        matches = self.db.session.query(Match).filter(
            and_(
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                Match.date < before_date,
                Match.status == 'FINISHED'
            )
        ).order_by(Match.date.desc()).limit(last_n).all()

        if not matches:
            return 0.0

        total = 0.0
        weight_sum = 0.0
        for i, m in enumerate(matches):
            weight = 0.9 ** i  # Most recent = 1.0, next = 0.9, then 0.81, etc.
            if m.home_team_id == team_id:
                pts = 3 if m.winner == 'HOME_TEAM' else (1 if m.winner == 'DRAW' else 0)
            else:
                pts = 3 if m.winner == 'AWAY_TEAM' else (1 if m.winner == 'DRAW' else 0)
            total += pts * weight
            weight_sum += weight

        return float(total / weight_sum) if weight_sum > 0 else 0.0

    def is_european_competition(self, match):
        """Check if a match is a European competition (CL"""
        european_comps = [
            'UEFA Champions League'
        ]
        competition = getattr(match, 'competition', None)
        return competition in european_comps

    def is_knockout_stage(self, match):
        """Check if a match is in a knockout stage (no group/league standings)"""
        knockout_stages = [
            'LAST_16', 'QUARTER_FINALS', 'SEMI_FINALS', 'FINAL',
            'ROUND_OF_16', 'PRELIMINARY_ROUND', 'PRELIMINARY_SEMI_FINALS',
            'PRELIMINARY_FINAL', 'PLAY_OFF_ROUND',
        ]
        stage = getattr(match, 'stage', None)
        return stage in knockout_stages

    def _calc_rolling_stat_db(self, team_id, before_date, home, stat_type, last_n=5):
        """Calculate rolling average of match statistics from DB (fallback path)."""
        if home:
            matches = self.db.session.query(Match).filter(
                and_(Match.home_team_id == team_id, Match.date < before_date, Match.status == 'FINISHED')
            ).order_by(Match.date.desc()).limit(last_n).all()
        else:
            matches = self.db.session.query(Match).filter(
                and_(Match.away_team_id == team_id, Match.date < before_date, Match.status == 'FINISHED')
            ).order_by(Match.date.desc()).limit(last_n).all()

        if not matches:
            return None

        if stat_type == 'shots_on_target':
            vals = [m.home_shots_on_target if home else m.away_shots_on_target for m in matches]
        elif stat_type == 'corners':
            vals = [m.home_corners if home else m.away_corners for m in matches]
        elif stat_type == 'cards':
            vals = [(m.home_yellow_cards or 0) + (m.home_red_cards or 0) if home
                    else (m.away_yellow_cards or 0) + (m.away_red_cards or 0) for m in matches]
        else:
            return None

        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else None

    def _calc_points_from_top(self, standing, season, competition):
        """Calculate points gap to league leader."""
        config = LEAGUE_CONFIG.get(competition)
        if not config or standing['points'] == 0:
            return None
        # Find max points in this competition/season from standings cache or DB
        max_pts = standing['points']  # default to own points
        if self._cache_built:
            for key, s in self._standings_cache.items():
                if key[1] == season and key[2] == competition and s.points > max_pts:
                    max_pts = s.points
        else:
            top = self.db.session.query(Standing).filter_by(
                season=season, competition=competition, position=1
            ).first()
            if top:
                max_pts = top.points
        return max_pts - standing['points']

    def _calc_points_from_relegation(self, standing, season, competition):
        """Calculate points above the relegation zone."""
        config = LEAGUE_CONFIG.get(competition)
        if not config or standing['points'] == 0:
            return None
        rel_pos = config['relegation_start']
        # Find points at relegation position
        if self._cache_built:
            for key, s in self._standings_cache.items():
                if key[1] == season and key[2] == competition and s.position == rel_pos:
                    return standing['points'] - s.points
        else:
            rel = self.db.session.query(Standing).filter_by(
                season=season, competition=competition, position=rel_pos
            ).first()
            if rel:
                return standing['points'] - rel.points
        return None

    def _calc_season_progress(self, match):
        """Calculate season progress (0-1) based on matchday or date within season."""
        matchday = getattr(match, 'matchday', None)
        competition = getattr(match, 'competition', '')
        config = LEAGUE_CONFIG.get(competition)
        if matchday and config:
            total_matchdays = (config['teams'] - 1) * 2
            return round(min(matchday / total_matchdays, 1.0), 3)
        # Fallback: estimate from date within season (Aug-May)
        month = match.date.month
        if month >= 8:  # Aug=0.0, Dec=0.4
            return round((month - 8) / 10, 3)
        else:  # Jan=0.5, May=0.9
            return round((month + 4) / 10, 3)

    def _calc_avg_position(self, team_id, current_season):
        """Calculate average league position over prior 3 seasons (squad strength proxy)."""
        positions = []
        for s in range(current_season - 3, current_season):
            if self._cache_built:
                for key, st in self._standings_cache.items():
                    if key[0] == team_id and key[1] == s and key[2] != 'UEFA Champions League':
                        positions.append(st.position)
                        break
            else:
                st = self.db.session.query(Standing).filter(
                    Standing.team_id == team_id,
                    Standing.season == s,
                    Standing.competition != 'UEFA Champions League'
                ).first()
                if st:
                    positions.append(st.position)
        return round(float(np.mean(positions)), 1) if positions else None

    def _is_artificial_pitch(self, team_id):
        """Check if home team plays on artificial pitch."""
        if self._cache_built:
            # Look up team name from any match in cache
            home_matches = self._team_home_matches.get(team_id, [])
            if home_matches:
                team = self.db.session.query(Team).filter_by(id=team_id).first()
                if team and team.name in ARTIFICIAL_PITCH_TEAMS:
                    return 1
        else:
            team = self.db.session.query(Team).filter_by(id=team_id).first()
            if team and team.name in ARTIFICIAL_PITCH_TEAMS:
                return 1
        return 0

    def create_match_features(self, match):
        """
        Create all features for a single match.
        Uses in-memory cache if available (bulk mode), falls back to DB queries (single prediction).

        Args:
            match (Match): Match object from database

        Returns:
            dict: Dictionary of features
        """
        home_team_id = match.home_team_id
        away_team_id = match.away_team_id
        match_date = match.date
        season = match.season

        if self._cache_built:
            return self._create_features_from_cache(match)

        # DB query fallback (used for single predictions via API)
        if self.is_european_competition(match):
            home_standing = self.get_cl_standing_features(home_team_id, season)
            away_standing = self.get_cl_standing_features(away_team_id, season)
        else:
            home_standing = self.get_standing_features(home_team_id, season)
            away_standing = self.get_standing_features(away_team_id, season)

        h2h_home, h2h_draws_val, h2h_away = self.calculate_head_to_head(home_team_id, away_team_id, match_date, 5)

        features = {
            'match_id': match.id,
            'home_form_5': self.calculate_team_form(home_team_id, match_date, 5),
            'away_form_5': self.calculate_team_form(away_team_id, match_date, 5),
            'home_goals_scored_avg': self.calculate_avg_goals_scored(home_team_id, match_date, True, 5),
            'away_goals_scored_avg': self.calculate_avg_goals_scored(away_team_id, match_date, False, 5),
            'home_goals_conceded_avg': self.calculate_avg_goals_conceded(home_team_id, match_date, True, 5),
            'away_goals_conceded_avg': self.calculate_avg_goals_conceded(away_team_id, match_date, False, 5),
            'home_win_rate': self.calculate_win_rate(home_team_id, match_date, True, 10),
            'away_win_rate': self.calculate_win_rate(away_team_id, match_date, False, 10),
            'h2h_home_wins': h2h_home,
            'h2h_away_wins': h2h_away,
            'h2h_draws': h2h_draws_val,
            'days_since_home_last_match': self.calculate_days_since_last_match(home_team_id, match_date),
            'days_since_away_last_match': self.calculate_days_since_last_match(away_team_id, match_date),
            'home_league_position': home_standing['league_position'],
            'away_league_position': away_standing['league_position'],
            'home_points': home_standing['points'],
            'away_points': away_standing['points'],
            'home_goal_difference': home_standing['goal_difference'],
            'away_goal_difference': away_standing['goal_difference'],
            'home_draw_rate': self.calculate_draw_rate(home_team_id, match_date, 10),
            'away_draw_rate': self.calculate_draw_rate(away_team_id, match_date, 10),
            'home_clean_sheet_rate': self.calculate_clean_sheet_rate(home_team_id, match_date, True, 5),
            'away_clean_sheet_rate': self.calculate_clean_sheet_rate(away_team_id, match_date, False, 5),
            'home_weighted_form': self.calculate_weighted_form(home_team_id, match_date, 5),
            'away_weighted_form': self.calculate_weighted_form(away_team_id, match_date, 5),
            # Rolling match stats (DB fallback — query based)
            'home_shots_on_target_avg': self._calc_rolling_stat_db(home_team_id, match_date, True, 'shots_on_target', 5),
            'away_shots_on_target_avg': self._calc_rolling_stat_db(away_team_id, match_date, False, 'shots_on_target', 5),
            'home_corners_avg': self._calc_rolling_stat_db(home_team_id, match_date, True, 'corners', 5),
            'away_corners_avg': self._calc_rolling_stat_db(away_team_id, match_date, False, 'corners', 5),
            'home_cards_avg': self._calc_rolling_stat_db(home_team_id, match_date, True, 'cards', 5),
            'away_cards_avg': self._calc_rolling_stat_db(away_team_id, match_date, False, 'cards', 5),
            # Betting odds (from match itself — NULL for API matches)
            'avg_home_prob': getattr(match, 'avg_home_prob', None),
            'avg_draw_prob': getattr(match, 'avg_draw_prob', None),
            'avg_away_prob': getattr(match, 'avg_away_prob', None),
            # Motivation
            'home_points_from_top': self._calc_points_from_top(home_standing, season, getattr(match, 'competition', '')),
            'away_points_from_top': self._calc_points_from_top(away_standing, season, getattr(match, 'competition', '')),
            'home_points_from_relegation': self._calc_points_from_relegation(home_standing, season, getattr(match, 'competition', '')),
            'away_points_from_relegation': self._calc_points_from_relegation(away_standing, season, getattr(match, 'competition', '')),
            'season_progress': self._calc_season_progress(match),
            # Squad strength proxy
            'home_avg_position_3yr': self._calc_avg_position(home_team_id, season),
            'away_avg_position_3yr': self._calc_avg_position(away_team_id, season),
            # Pitch type
            'home_artificial_pitch': self._is_artificial_pitch(home_team_id),
        }

        return features

    def _create_features_from_cache(self, match):
        """Create features using in-memory cache — no DB queries."""
        home_id = match.home_team_id
        away_id = match.away_team_id
        match_date = match.date
        season = match.season

        # Form (last 5 matches, any venue)
        home_recent = self._get_before(self._team_all_matches[home_id], match_date, 5)
        away_recent = self._get_before(self._team_all_matches[away_id], match_date, 5)

        def calc_form(team_id, matches):
            pts = 0
            for m in matches:
                if m.home_team_id == team_id:
                    if m.winner == 'HOME_TEAM': pts += 3
                    elif m.winner == 'DRAW': pts += 1
                else:
                    if m.winner == 'AWAY_TEAM': pts += 3
                    elif m.winner == 'DRAW': pts += 1
            return float(pts)

        # Goals scored/conceded avg (last 5 home/away matches)
        home_home = self._get_before(self._team_home_matches[home_id], match_date, 5)
        away_away = self._get_before(self._team_away_matches[away_id], match_date, 5)
        home_home_conceded = [m.away_score for m in home_home if m.away_score is not None]
        away_away_conceded = [m.home_score for m in away_away if m.home_score is not None]
        home_home_scored = [m.home_score for m in home_home if m.home_score is not None]
        away_away_scored = [m.away_score for m in away_away if m.away_score is not None]

        # Win rate (last 10 home/away)
        home_home_10 = self._get_before(self._team_home_matches[home_id], match_date, 10)
        away_away_10 = self._get_before(self._team_away_matches[away_id], match_date, 10)
        home_wr = sum(1 for m in home_home_10 if m.winner == 'HOME_TEAM') / len(home_home_10) if home_home_10 else 0.0
        away_wr = sum(1 for m in away_away_10 if m.winner == 'AWAY_TEAM') / len(away_away_10) if away_away_10 else 0.0

        # H2H (last 5 meetings)
        h2h_key = tuple(sorted([home_id, away_id]))
        h2h_matches = self._get_before(self._h2h_matches[h2h_key], match_date, 5)
        h2h_home_wins = h2h_draws = h2h_away_wins = 0
        for m in h2h_matches:
            if m.home_team_id == home_id:
                if m.winner == 'HOME_TEAM': h2h_home_wins += 1
                elif m.winner == 'AWAY_TEAM': h2h_away_wins += 1
                else: h2h_draws += 1
            else:
                if m.winner == 'AWAY_TEAM': h2h_home_wins += 1
                elif m.winner == 'HOME_TEAM': h2h_away_wins += 1
                else: h2h_draws += 1

        # Rest days
        home_all = self._get_before(self._team_all_matches[home_id], match_date, 1)
        away_all = self._get_before(self._team_all_matches[away_id], match_date, 1)
        home_rest = (match_date - home_all[-1].date).days if home_all else None
        away_rest = (match_date - away_all[-1].date).days if away_all else None

        # Draw rate (last 10 matches)
        home_recent_10 = self._get_before(self._team_all_matches[home_id], match_date, 10)
        away_recent_10 = self._get_before(self._team_all_matches[away_id], match_date, 10)
        home_draw_rate = sum(1 for m in home_recent_10 if m.winner == 'DRAW') / len(home_recent_10) if home_recent_10 else 0.0
        away_draw_rate = sum(1 for m in away_recent_10 if m.winner == 'DRAW') / len(away_recent_10) if away_recent_10 else 0.0

        # Clean sheet rate (last 5 home/away)
        home_cs = sum(1 for m in home_home if m.away_score == 0) / len(home_home) if home_home else 0.0
        away_cs = sum(1 for m in away_away if m.home_score == 0) / len(away_away) if away_away else 0.0

        # Weighted form (exponential decay — recent matches matter more)
        def calc_weighted_form(team_id, matches):
            if not matches:
                return 0.0
            # matches are already in chronological order, reverse for most recent first
            recent = list(reversed(matches))
            total = weight_sum = 0.0
            for i, m in enumerate(recent):
                w = 0.9 ** i
                if m.home_team_id == team_id:
                    pts = 3 if m.winner == 'HOME_TEAM' else (1 if m.winner == 'DRAW' else 0)
                else:
                    pts = 3 if m.winner == 'AWAY_TEAM' else (1 if m.winner == 'DRAW' else 0)
                total += pts * w
                weight_sum += w
            return float(total / weight_sum) if weight_sum > 0 else 0.0

        # Standings (from cache)
        home_standing = self._get_standing_from_cache(home_id, season, match)
        away_standing = self._get_standing_from_cache(away_id, season, match)

        # Rolling match stats from cache (shots on target, corners, cards)
        def calc_rolling_stat(team_id, matches, home, stat_type):
            if not matches:
                return None
            if stat_type == 'shots_on_target':
                vals = [m.home_shots_on_target if home else m.away_shots_on_target for m in matches]
            elif stat_type == 'corners':
                vals = [m.home_corners if home else m.away_corners for m in matches]
            elif stat_type == 'cards':
                vals = [(m.home_yellow_cards or 0) + (m.home_red_cards or 0) if home
                        else (m.away_yellow_cards or 0) + (m.away_red_cards or 0) for m in matches]
            else:
                return None
            vals = [v for v in vals if v is not None]
            return float(np.mean(vals)) if vals else None

        competition = getattr(match, 'competition', '')

        return {
            'match_id': match.id,
            'home_form_5': calc_form(home_id, home_recent),
            'away_form_5': calc_form(away_id, away_recent),
            'home_goals_scored_avg': float(np.mean(home_home_scored)) if home_home_scored else 0.0,
            'away_goals_scored_avg': float(np.mean(away_away_scored)) if away_away_scored else 0.0,
            'home_goals_conceded_avg': float(np.mean(home_home_conceded)) if home_home_conceded else 0.0,
            'away_goals_conceded_avg': float(np.mean(away_away_conceded)) if away_away_conceded else 0.0,
            'home_win_rate': float(home_wr),
            'away_win_rate': float(away_wr),
            'h2h_home_wins': h2h_home_wins,
            'h2h_away_wins': h2h_away_wins,
            'h2h_draws': h2h_draws,
            'days_since_home_last_match': home_rest,
            'days_since_away_last_match': away_rest,
            'home_league_position': home_standing['league_position'],
            'away_league_position': away_standing['league_position'],
            'home_points': home_standing['points'],
            'away_points': away_standing['points'],
            'home_goal_difference': home_standing['goal_difference'],
            'away_goal_difference': away_standing['goal_difference'],
            'home_draw_rate': float(home_draw_rate),
            'away_draw_rate': float(away_draw_rate),
            'home_clean_sheet_rate': float(home_cs),
            'away_clean_sheet_rate': float(away_cs),
            'home_weighted_form': calc_weighted_form(home_id, home_recent),
            'away_weighted_form': calc_weighted_form(away_id, away_recent),
            # Rolling match stats
            'home_shots_on_target_avg': calc_rolling_stat(home_id, home_home, True, 'shots_on_target'),
            'away_shots_on_target_avg': calc_rolling_stat(away_id, away_away, False, 'shots_on_target'),
            'home_corners_avg': calc_rolling_stat(home_id, home_home, True, 'corners'),
            'away_corners_avg': calc_rolling_stat(away_id, away_away, False, 'corners'),
            'home_cards_avg': calc_rolling_stat(home_id, home_home, True, 'cards'),
            'away_cards_avg': calc_rolling_stat(away_id, away_away, False, 'cards'),
            # Betting odds (from match record)
            'avg_home_prob': getattr(match, 'avg_home_prob', None),
            'avg_draw_prob': getattr(match, 'avg_draw_prob', None),
            'avg_away_prob': getattr(match, 'avg_away_prob', None),
            # Motivation
            'home_points_from_top': self._calc_points_from_top(home_standing, season, competition),
            'away_points_from_top': self._calc_points_from_top(away_standing, season, competition),
            'home_points_from_relegation': self._calc_points_from_relegation(home_standing, season, competition),
            'away_points_from_relegation': self._calc_points_from_relegation(away_standing, season, competition),
            'season_progress': self._calc_season_progress(match),
            # Squad strength proxy
            'home_avg_position_3yr': self._calc_avg_position(home_id, season),
            'away_avg_position_3yr': self._calc_avg_position(away_id, season),
            # Pitch type
            'home_artificial_pitch': self._is_artificial_pitch(home_id),
        }

    def _get_standing_from_cache(self, team_id, season, match):
        """Get standing features from cache, with same CL/domestic fallback logic."""
        defaults = {'league_position': 10, 'points': 0, 'goal_difference': 0}

        if self.is_european_competition(match):
            # Try CL standing first
            cl = self._standings_cache.get((team_id, season, 'UEFA Champions League'))
            if cl:
                return {'league_position': cl.position, 'points': cl.points, 'goal_difference': cl.goal_difference}
            # Fall back to any non-European standing
            for key, s in self._standings_cache.items():
                if key[0] == team_id and key[1] == season and key[2] != 'UEFA Champions League':
                    return {'league_position': s.position, 'points': s.points, 'goal_difference': s.goal_difference}
            return defaults
        else:
            s = self._standings_cache.get((team_id, season, getattr(match, 'competition', '')))
            if s:
                return {'league_position': s.position, 'points': s.points, 'goal_difference': s.goal_difference}
            # Try any standing for this team/season
            for key, st in self._standings_cache.items():
                if key[0] == team_id and key[1] == season:
                    return {'league_position': st.position, 'points': st.points, 'goal_difference': st.goal_difference}
            return defaults
    
    def create_features_for_all_matches(self, save_to_db=True):
        """
        Create features for all matches in database

        Args:
            save_to_db (bool): Whether to save features to database

        Returns:
            pd.DataFrame: Features dataframe
        """
        # Build in-memory cache for fast feature computation (eliminates ~400k DB queries)
        self._build_match_cache()

        # Get all finished matches ordered by date (oldest first)
        matches = self.db.session.query(Match).filter(
            Match.status == 'FINISHED'
        ).order_by(Match.date).all()

        print(f"Creating features for {len(matches)} matches...")

        all_features = []

        # Get features computed-at timestamps so we can skip matches whose underlying
        # data hasn't changed since features were last computed. Previously this used
        # set membership only, which meant a match that got its final score (status
        # SCHEDULED → FINISHED) never had its features recomputed — silently serving
        # stale features as new results came in.
        existing_features = {}
        if save_to_db:
            existing_features = {
                row[0]: row[1]
                for row in self.db.session.query(
                    MatchFeatures.match_id, MatchFeatures.created_at
                ).all()
            }
            if existing_features:
                print(f"Found {len(existing_features)} previously-processed matches (will skip those still up-to-date)...")

        skipped_count = 0

        for idx, match in enumerate(matches):
            features_created_at = existing_features.get(match.id)
            if features_created_at is not None:
                # Skip if features were computed AFTER the match was last updated.
                # match.updated_at has onupdate=datetime.utcnow so it advances whenever
                # any column changes (e.g. score or status). If match has never been
                # touched since features were computed, no need to recompute.
                match_updated = match.updated_at or match.created_at
                if match_updated is None or features_created_at >= match_updated:
                    skipped_count += 1
                    continue

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
                    # Create new features — map all feature dict keys to MatchFeatures columns
                    feature_kwargs = {k: v for k, v in features.items()
                                      if k not in ('target',) and hasattr(MatchFeatures, k)}
                    match_features = MatchFeatures(**feature_kwargs)
                    self.db.session.add(match_features)

                # Commit every 1000 matches
                if (idx + 1) % 1000 == 0:
                    self.db.session.commit()
                    print(f"    Processed {idx + 1}/{len(matches)} matches...")

                
        # Final commit
        if save_to_db:
            self.db.session.commit()
            print("Features saved to database!")

        # Convert to DataFrame
        df = pd.DataFrame(all_features)

        print(f"\nFeatures created: {len(df)} rows, {len(df.columns)} columns "
              f"({skipped_count} skipped as up-to-date)")
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
            mf.home_form_5, mf.away_form_5,
            mf.home_goals_scored_avg, mf.away_goals_scored_avg,
            mf.home_goals_conceded_avg, mf.away_goals_conceded_avg,
            mf.h2h_home_wins, mf.h2h_away_wins, mf.h2h_draws,
            mf.home_win_rate, mf.away_win_rate,
            mf.days_since_home_last_match, mf.days_since_away_last_match,
            mf.home_league_position, mf.away_league_position,
            mf.home_points, mf.away_points,
            mf.home_goal_difference, mf.away_goal_difference,
            mf.home_draw_rate, mf.away_draw_rate,
            mf.home_clean_sheet_rate, mf.away_clean_sheet_rate,
            mf.home_weighted_form, mf.away_weighted_form,
            mf.home_shots_on_target_avg, mf.away_shots_on_target_avg,
            mf.home_corners_avg, mf.away_corners_avg,
            mf.home_cards_avg, mf.away_cards_avg,
            mf.avg_home_prob, mf.avg_draw_prob, mf.avg_away_prob,
            mf.home_points_from_top, mf.away_points_from_top,
            mf.home_points_from_relegation, mf.away_points_from_relegation,
            mf.season_progress,
            mf.home_avg_position_3yr, mf.away_avg_position_3yr,
            mf.home_artificial_pitch,
            m.winner as target,
            m.home_score, m.away_score,
            m.home_ht_score, m.away_ht_score,
            m.home_corners, m.away_corners,
            m.home_yellow_cards, m.away_yellow_cards,
            m.home_red_cards, m.away_red_cards,
            m.date, m.season, m.competition,
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
    
    def get_standing_features(self, team_id, season):
        """
        Get league position, points, and goal difference for a team

        Returns:
            dict: Standing features (default if not found)
        """
        standing = self.db.get_team_standing(team_id, season)
        if standing:
            return {
                'league_position': standing.position,
                'points': standing.points,
                'goal_difference': standing.goal_difference
            }
        # Defaults for teams without standings data (e.g., old seasons)
        return {
            'league_position': 10,
            'points': 0,
            'goal_difference': 0
        }

    def get_domestic_standing_features(self, team_id, season):
        """
        Get a team's domestic league standing (for European competition matches).
        Falls back to any available standing, then defaults.

        Returns:
            dict: Standing features from domestic league
        """
        standing = self.db.get_team_domestic_standing(team_id, season)
        if standing:
            return {
                'league_position': standing.position,
                'points': standing.points,
                'goal_difference': standing.goal_difference
            }
        # Fall back to any standing for this team/season
        return self.get_standing_features(team_id, season)

    def get_cl_standing_features(self, team_id, season):
        """
        Get a team's CL league phase standing (computed from match results).
        Falls back to domestic standing if CL standing not available
        (e.g., first CL match before any results).

        Returns:
            dict: Standing features — CL first, then domestic fallback
        """
        # Try CL standings first
        from database import Standing
        cl_standing = self.db.session.query(Standing).filter_by(
            team_id=team_id, season=season,
            competition='UEFA Champions League'
        ).first()
        if cl_standing:
            return {
                'league_position': cl_standing.position,
                'points': cl_standing.points,
                'goal_difference': cl_standing.goal_difference
            }
        # Fall back to domestic standings
        return self.get_domestic_standing_features(team_id, season)

    
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
    engineer.create_features_for_all_matches(save_to_db=True)

    # Export to CSV
    engineer.export_features_to_csv()

    # Close database connection
    engineer.close()

    print("\nFeature engineering complete!")
    

