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

import bisect
import os
import time
import pandas as pd
import numpy as np
from collections import defaultdict
from datetime import datetime
from database import DatabaseManager, Match, Team, MatchFeatures, Standing
from sqlalchemy import and_, or_

# Bump whenever the meaning of any stored feature changes — a new column, a
# different imputation, a corrected lookup. create_features_for_all_matches
# rebuilds any row not carrying the current value, so a pipeline change
# propagates on the next run instead of waiting for someone to remember
# `force=True`. Without this the skip only ever noticed changes to the MATCH,
# and the weekly retrain kept training on rows built by an older definition.
#
# 2 — point-in-time league tables (was: end-of-season standings on every
#     historical row) and rest days imputed to 7 rather than 0.
# 3 — early-season league position seeded from the previous season's final
#     table. See EARLY_SEASON_MATCHES.
FEATURE_PIPELINE_VERSION = '3'

# How many matches a club must have played before this season's table is taken
# at face value.
#
# Before that it carries no information at all. _rank breaks ties on points,
# then goal difference, then goals for, then team id — so on matchday 1 every
# club is level and the "league position" handed to the model is an artefact of
# primary keys. Where a previous season exists, its FINAL table is a real
# ordering that was already known when this match kicked off, which makes it a
# legal point-in-time input.
#
# ONLY position is seeded this way. Points and goal difference stay at their
# true current values (0 at the start of a season). Substituting an
# end-of-season points total would hand the model a number it only ever saw on
# end-of-season rows in training — precisely the train/serve skew that the
# point-in-time work removed. Position is the one of the three on the same scale
# in August as it is in May.
EARLY_SEASON_MATCHES = int(os.getenv('EARLY_SEASON_MATCHES', '5'))

# Sentinel date meaning "after every match in the season".
_END_OF_SEASON = datetime(2999, 1, 1)

# How long a SQL-built league table may be reused. Results land after the fact —
# a 15:00 kickoff gets its score when the fixture refresh next runs — so a table
# built before that arrives is stale for every later fixture. Short enough that
# a stale table cannot survive a matchday, long enough that one dashboard load
# does not re-aggregate per fixture.
DB_TABLE_CACHE_TTL = float(os.getenv('DB_TABLE_CACHE_TTL', '600'))


def feature_row_is_current(entry, match) -> bool:
    """Can this match's stored feature row be reused?

    `entry` is (created_at, pipeline_version) for the stored row, or None when
    there isn't one. Two independent ways to be stale, and the second is the one
    that used to be missed entirely:

      1. The MATCH changed after the features were computed — a score arriving,
         a status moving SCHEDULED -> FINISHED.
      2. The PIPELINE changed. Nothing about the match record moves when the
         meaning of a feature does, so a row built under an older definition
         looked permanently up to date and the weekly retrain kept training on it.
    """
    if not entry:
        return False
    created_at, version = entry
    if created_at is None or version != FEATURE_PIPELINE_VERSION:
        return False
    match_updated = getattr(match, 'updated_at', None) or getattr(match, 'created_at', None)
    if match_updated is None:
        return True
    return created_at >= match_updated


# ============================================================================
# Point-in-time Elo computation — for honest backtests
# ============================================================================
#
# The frozen `model_data['elo_ratings']` snapshot reflects all matches up to
# the training cutoff. Using it at inference time for matches WITHIN the
# training window is a form of leakage: the rating for "Arsenal on 2026-02-15"
# already incorporates the outcome of that very match.
#
# These helpers replay the full match history in chronological order to
# reconstruct each team's Elo AS IT WAS just before any given date.
#
# Usage:
#     history = compute_elo_history(db)
#     home_elo = get_elo_at("Arsenal FC", match.date, history)
#     # ↑ Elo computed from matches strictly before match.date
#
# Cost: ~2 seconds per league-season once at the start of a backfill, then O(log n)
# per lookup. Negligible for a 2000-match backfill.

def compute_elo_history(db, k: float = 20.0, home_advantage: float = 50.0) -> dict:
    """
    Reconstruct each team's Elo trajectory by replaying every finished match
    in chronological order. Returns a dict mapping team_name → sorted list of
    (match_date, pre_match_elo) tuples.

    The Elo snapshot is recorded BEFORE the match is processed for ratings
    update. So to ask "what was team X's Elo on date D?", find the latest
    entry strictly before D — that's the value we'd have had at kickoff.

    Mirrors compute_elo_ratings() in model_training.py for consistency with
    how the training pipeline computes Elo.
    """
    matches = (db.session.query(Match)
               .filter(Match.status == 'FINISHED')
               .filter(Match.winner.isnot(None))
               .order_by(Match.date.asc())
               .all())

    elo: dict[str, float] = defaultdict(lambda: 1500.0)
    history: dict[str, list[tuple]] = defaultdict(list)

    for m in matches:
        if not m.home_team or not m.away_team or not m.date:
            continue
        home = m.home_team.name
        away = m.away_team.name
        # Normalize to tz-naive UTC for consistent bisect comparisons
        d = m.date.replace(tzinfo=None) if m.date.tzinfo else m.date

        # Snapshot Elo BEFORE this match — this is what would have been visible
        history[home].append((d, elo[home]))
        history[away].append((d, elo[away]))

        # Expected scores with home advantage
        exp_home = 1 / (1 + 10 ** ((elo[away] - elo[home] - home_advantage) / 400))
        exp_away = 1 - exp_home

        if m.winner == 'HOME_TEAM':
            actual_home, actual_away = 1.0, 0.0
        elif m.winner == 'AWAY_TEAM':
            actual_home, actual_away = 0.0, 1.0
        else:
            actual_home, actual_away = 0.5, 0.5

        elo[home] += k * (actual_home - exp_home)
        elo[away] += k * (actual_away - exp_away)

    return dict(history)


def get_elo_at(team_name: str, target_date, history: dict, default: float = 1500.0) -> float:
    """
    Return team's Elo as of just BEFORE target_date. Latest history entry
    strictly less than target_date, or `default` if no prior history exists
    (team played their first match on/after target_date).

    Uses bisect for O(log n) lookup — works because history lists are
    chronologically sorted by construction in compute_elo_history.
    """
    entries = history.get(team_name)
    if not entries:
        return default
    target = target_date.replace(tzinfo=None) if hasattr(target_date, 'tzinfo') and target_date.tzinfo else target_date
    dates = [e[0] for e in entries]
    idx = bisect.bisect_left(dates, target)
    if idx == 0:
        return default
    return float(entries[idx - 1][1])

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
        self._date_index = {}       # id(match_list) -> [dates], see _dates_for
        # match_id -> {'home': {...}, 'away': {...}} — league table as it stood at
        # kickoff. See _build_pit_standings for why the stored Standing rows can't
        # be used for this.
        self._pit_standings = {}
        self._group_matches = defaultdict(list)   # (competition, season) -> matches
        self._group_teams = defaultdict(set)      # (competition, season) -> team ids
        self._table_cache = {}                    # (comp, season, date) -> table
        # Serving-side equivalent, keyed to the day and filled by SQL rather than
        # by the in-memory match cache (which single predictions never build).
        self._db_table_cache = {}                 # (comp, season, day) -> table
        self._domestic_cache = {}                 # (team_id, season) -> competition

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
            group = (m.competition, m.season)
            self._group_matches[group].append(m)
            self._group_teams[group].add(m.home_team_id)
            self._group_teams[group].add(m.away_team_id)

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

        self._build_pit_standings()

        print(f"Cache built: {len(all_matches)} matches, {len(all_standings)} standings, "
              f"{len(self._pit_standings)} point-in-time tables")
        self._cache_built = True

    # ------------------------------------------------------------------
    # Point-in-time league tables
    # ------------------------------------------------------------------

    @staticmethod
    def _table_entry(table, positions, team_id):
        row = table.get(team_id) or {'points': 0, 'gf': 0, 'ga': 0, 'played': 0}
        return {
            'league_position': positions.get(team_id, len(positions) + 1),
            'points': row['points'],
            'goal_difference': row['gf'] - row['ga'],
            # How many matches this entry rests on. _get_standing_from_cache uses
            # it to tell "8th on merit" from "8th because the table is empty and
            # the tiebreak fell that way".
            'played': row.get('played', 0),
        }

    @staticmethod
    def _rank(table):
        ranked = sorted(
            table.items(),
            key=lambda kv: (kv[1]['points'], kv[1]['gf'] - kv[1]['ga'], kv[1]['gf'], -kv[0]),
            reverse=True,
        )
        return {tid: i + 1 for i, (tid, _row) in enumerate(ranked)}

    @staticmethod
    def _apply_result(table, m):
        if m.home_score is None or m.away_score is None or not m.winner:
            return
        h, a = table[m.home_team_id], table[m.away_team_id]
        h['gf'] += m.home_score
        h['ga'] += m.away_score
        a['gf'] += m.away_score
        a['ga'] += m.home_score
        h['played'] += 1
        a['played'] += 1
        if m.winner == 'HOME_TEAM':
            h['points'] += 3
        elif m.winner == 'AWAY_TEAM':
            a['points'] += 3
        else:
            h['points'] += 1
            a['points'] += 1

    def _build_pit_standings(self):
        """Snapshot every league table as it stood BEFORE each match.

        The stored `standings` rows hold one entry per (team, season, competition)
        — the FINAL table, since compute_standings_from_matches walks the whole
        season. Reading them with no date filter meant a match played in September
        was featurised with the table from the following May, so league position,
        points and goal difference (and position_diff, points_diff, gd_diff,
        points_from_top, points_from_relegation, motivation_diff, the dead-rubber
        and safety flags derived from them) all encoded how the season finished —
        including the result being predicted.

        Every other feature here is strictly point-in-time; this makes the table
        features match. One chronological pass per (competition, season).
        """
        for group, matches in self._group_matches.items():
            matches.sort(key=lambda m: (m.date, m.id))
            table = {tid: {'points': 0, 'gf': 0, 'ga': 0, 'played': 0}
                     for tid in self._group_teams[group]}
            # Matches kicking off at the same moment are snapshotted together
            # and only then applied. Walking them one at a time let a 15:00
            # Saturday fixture see the results of the other 15:00 fixtures,
            # ordered by primary key — a small leak, but the same kind as the
            # one this method exists to remove.
            i = 0
            while i < len(matches):
                j = i
                while j < len(matches) and matches[j].date == matches[i].date:
                    j += 1
                positions = self._rank(table)
                for m in matches[i:j]:
                    self._pit_standings[m.id] = {
                        'home': self._table_entry(table, positions, m.home_team_id),
                        'away': self._table_entry(table, positions, m.away_team_id),
                    }
                for m in matches[i:j]:
                    self._apply_result(table, m)
                i = j

    def _table_as_of(self, competition, season, date):
        """League table for one (competition, season) as of `date`.

        Used for upcoming fixtures — which have no stored match to snapshot — and
        for European ties, where we want a club's domestic table rather than its
        group position. Memoised because a batch of upcoming matches shares both
        the group and, effectively, the date.
        """
        key = (competition, season, date)
        cached = self._table_cache.get(key)
        if cached is not None:
            return cached
        group = (competition, season)
        table = {tid: {'points': 0, 'gf': 0, 'ga': 0, 'played': 0}
                 for tid in self._group_teams.get(group, ())}
        for m in self._group_matches.get(group, ()):
            if m.date >= date:
                break  # list is date-sorted
            self._apply_result(table, m)
        positions = self._rank(table)
        built = {tid: self._table_entry(table, positions, tid) for tid in table}
        if len(self._table_cache) > 512:
            self._table_cache.clear()
        self._table_cache[key] = built
        return built

    def _previous_season_position(self, team_id, competition, season):
        """Where this club finished in the same competition last season.

        Returns None when there is no previous season on record for the
        competition — then the current (uninformative) position stands rather
        than being replaced by a guess.

        A club with no row in last season's table was promoted into this one, or
        is otherwise new to it. They are placed just below the bottom of that
        table, which is the same convention _table_entry already uses for a club
        missing from a ranking, and matches the fact that promoted sides start as
        the division's weakest on average.
        """
        if not competition or season is None:
            return None
        try:
            previous = int(season) - 1
        except (TypeError, ValueError):
            return None
        # In-memory when bulk feature engineering has built the caches, SQL
        # otherwise. Live predictions never call _build_match_cache, so without
        # the second branch this silently returned None at serve time and the
        # two paths disagreed about position while agreeing about everything
        # else — the same split that let the serving leak survive this long.
        if (competition, previous) in self._group_teams:
            final = self._table_as_of(competition, previous, _END_OF_SEASON)
        else:
            final = self._table_as_of_db(competition, previous, _END_OF_SEASON)
        if not final:
            return None
        entry = final.get(team_id)
        if entry:
            return entry['league_position']
        return len(final) + 1

    def _domestic_competition_db(self, team_id, season):
        """The league this club played in that season, from SQL.

        Serving counterpart to _domestic_competition, which reads the in-memory
        group cache that single predictions never build.
        """
        from sqlalchemy import text

        if season is None:
            return None
        key = (team_id, season)
        cached = self._domestic_cache.get(key)
        if cached is not None:
            return cached or None
        row = self.db.session.execute(text("""
            SELECT competition FROM matches
            WHERE season = :season
              AND (home_team_id = :tid OR away_team_id = :tid)
              AND competition NOT LIKE 'UEFA%'
            GROUP BY competition
            ORDER BY count(*) DESC
            LIMIT 1
        """), {'season': season, 'tid': team_id}).fetchone()
        found = row[0] if row else ''
        if len(self._domestic_cache) > 2048:
            self._domestic_cache.clear()
        self._domestic_cache[key] = found
        return found or None

    def _domestic_competition(self, team_id, season):
        """The league this club played in that season, ignoring European ties."""
        for (comp, comp_season), teams in self._group_teams.items():
            if comp_season == season and team_id in teams and 'UEFA' not in (comp or ''):
                return comp
        return None

    def _dates_for(self, match_list):
        """Memoised date list for a cached match list, keyed by list identity.

        _get_before rebuilt [m.date for m in match_list] on every call, so the
        O(log n) bisect sat behind an O(n) list build — and it is called about ten
        times per match across tens of thousands of matches. The length check
        rebuilds if a list ever grows.
        """
        key = id(match_list)
        cached = self._date_index.get(key)
        if cached is None or len(cached) != len(match_list):
            cached = [m.date for m in match_list]
            self._date_index[key] = cached
        return cached

    def _get_before(self, match_list, before_date, last_n):
        """Get last N matches strictly before a date from a sorted list."""
        idx = bisect.bisect_left(self._dates_for(match_list), before_date)
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
    
    def calculate_avg_xg_for(self, team_id, before_date, home=True, last_n=5):
        """
        Average expected goals FOR by team across recent matches.

        Returns 0.0 when no xG-tagged matches are available (e.g. teams in
        understat-uncovered leagues). The 0.0 sentinel matches the goal-based
        feature convention (`calculate_avg_goals_scored`) so pandas keeps
        the column as float64 instead of object dtype — critical for
        sklearn's cross_val_predict which choked on mixed None/float
        columns with `cross_val_predict only works for partitions`.

        xG-for is a strictly better feature than goals-for for short
        windows: a team that creates good chances but doesn't convert is
        about to mean-revert, and the goals-only signal misses that.
        """
        if home:
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.home_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED',
                    Match.xg_home.isnot(None),
                )
            ).order_by(Match.date.desc()).limit(last_n).all()
            xg = [m.xg_home for m in matches]
        else:
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.away_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED',
                    Match.xg_away.isnot(None),
                )
            ).order_by(Match.date.desc()).limit(last_n).all()
            xg = [m.xg_away for m in matches]
        return float(np.mean(xg)) if xg else 0.0

    def calculate_avg_xg_against(self, team_id, before_date, home=True, last_n=5):
        """Average xG conceded — mirror of calculate_avg_xg_for. Returns 0.0
        when no xG data available; same dtype-stability rationale."""
        if home:
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.home_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED',
                    Match.xg_away.isnot(None),
                )
            ).order_by(Match.date.desc()).limit(last_n).all()
            xg = [m.xg_away for m in matches]
        else:
            matches = self.db.session.query(Match).filter(
                and_(
                    Match.away_team_id == team_id,
                    Match.date < before_date,
                    Match.status == 'FINISHED',
                    Match.xg_home.isnot(None),
                )
            ).order_by(Match.date.desc()).limit(last_n).all()
            xg = [m.xg_home for m in matches]
        return float(np.mean(xg)) if xg else 0.0

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

    # ------------------------------------------------------------------
    # v2 motivation features — used by next-retrained models.
    # All take a `standing` dict shaped like {'league_position', 'points',
    # 'points_from_top', 'points_from_relegation', ...} as produced by
    # get_standing_features().
    # ------------------------------------------------------------------

    def _is_safe(self, standing: dict) -> int:
        """
        1 if the team is in 'mid-table no-stakes' zone — comfortably above the
        relegation cutoff and comfortably below European places. The threshold
        of 8 points is a heuristic (≈ 3 wins of cushion in top-5 leagues).

        Returns 0 when either: in/near relegation, in/near European places,
        or we lack standings data.
        """
        if not standing:
            return 0
        pos = standing.get('league_position') or 0
        pts_from_rel = standing.get('points_from_relegation')
        pts_from_top = standing.get('points_from_top')
        if not pts_from_rel or not pts_from_top:
            return 0
        # Position 8-14 with > 8pts cushion both ways = "safe mid-table"
        if 8 <= pos <= 14 and pts_from_rel >= 8 and pts_from_top >= 8:
            return 1
        return 0

    def _dead_rubber_likelihood(self, home_standing: dict, away_standing: dict,
                                 season_progress: float) -> float:
        """
        Heuristic 0-1 likelihood that the match is a dead rubber (both sides
        have nothing to play for). Rises with season_progress: matches early
        in the season are NEVER dead rubbers regardless of standings.

        Composition:
            P(home_safe) × P(away_safe) × season_progress

        season_progress > 0.85 (last ~5 matchdays) is where this signal is
        actually useful. Earlier in the season it stays near 0.
        """
        if not home_standing or not away_standing or season_progress < 0.7:
            return 0.0
        h_safe = self._is_safe(home_standing)
        a_safe = self._is_safe(away_standing)
        # Linear ramp from 0.7 (no effect) to 1.0 (full effect)
        progress_weight = max(0.0, min(1.0, (season_progress - 0.7) / 0.3))
        return round(h_safe * a_safe * progress_weight, 3)

    def _motivation_asymmetry(self, home_standing: dict, away_standing: dict) -> float:
        """
        Signed difference in "stakes" between the two sides. Negative if away has
        more stakes (relegation / Europa fight), positive if home has more.

        Magnitude is in 'points of distance' — being 1pt from safety is high
        stakes; being 15pts from anything is no stakes. Capped at ±10.
        """
        def stakes(standing: dict) -> float:
            if not standing:
                return 0.0
            r = standing.get('points_from_relegation')
            t = standing.get('points_from_top')
            if r is None or t is None:
                return 0.0
            # Smaller of the two distances = how close to a meaningful boundary
            # Negative = above (close to top); positive = below (close to relegation)
            return min(abs(r), abs(t))

        h = stakes(home_standing)
        a = stakes(away_standing)
        # If both close to a boundary (low stakes value), asymmetry is small.
        # If one is close and the other comfortable, asymmetry is large.
        diff = a - h  # positive if away has higher stakes value (more comfortable)
        return max(-10.0, min(10.0, diff))

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

        # DB query fallback (used for single predictions via API). European ties
        # are handled inside get_standing_features, which substitutes the club's
        # domestic table — the same rule the bulk path applies. Routing them to a
        # separate method was how the two came to disagree: serving reported a
        # club's Champions League group position (26th, 4 points) where training
        # reported its league position (1st, 40 points).
        home_standing = self.get_standing_features(home_team_id, season, match)
        away_standing = self.get_standing_features(away_team_id, season, match)

        h2h_home, h2h_draws_val, h2h_away = self.calculate_head_to_head(home_team_id, away_team_id, match_date, 5)

        features = {
            'match_id': match.id,
            'home_form_5': self.calculate_team_form(home_team_id, match_date, 5),
            'away_form_5': self.calculate_team_form(away_team_id, match_date, 5),
            'home_goals_scored_avg': self.calculate_avg_goals_scored(home_team_id, match_date, True, 5),
            'away_goals_scored_avg': self.calculate_avg_goals_scored(away_team_id, match_date, False, 5),
            'home_goals_conceded_avg': self.calculate_avg_goals_conceded(home_team_id, match_date, True, 5),
            'away_goals_conceded_avg': self.calculate_avg_goals_conceded(away_team_id, match_date, False, 5),
            # xG-based equivalents. 0.0 for teams in understat-uncovered leagues
            # (Eredivisie / Primeira Liga / Championship) — matches the goal-
            # feature fallback so dtype stays float64 (None/float mix breaks
            # sklearn's cross_val_predict in the stacked-ensemble training).
            'home_xg_for_avg': self.calculate_avg_xg_for(home_team_id, match_date, True, 5),
            'away_xg_for_avg': self.calculate_avg_xg_for(away_team_id, match_date, False, 5),
            'home_xg_against_avg': self.calculate_avg_xg_against(home_team_id, match_date, True, 5),
            'away_xg_against_avg': self.calculate_avg_xg_against(away_team_id, match_date, False, 5),
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
            # --- v2 motivation features (additive — get used after next retrain) ---
            # `is_safe` = both far from relegation AND far from the European places.
            # Threshold tuning is league-specific but 8pts is a reasonable default
            # for top-5 leagues (≈ 3 wins of cushion).
            'home_is_safe': self._is_safe(home_standing),
            'away_is_safe': self._is_safe(away_standing),
            # `dead_rubber_likelihood` rises late in the season when both sides are safe
            # — proxy for "favourite may rest starters". Bookmakers heavily weight this
            # signal; our model currently doesn't, which is the leading hypothesis
            # for the favourite-overconfidence we see in /api/value-bets.
            'dead_rubber_likelihood': self._dead_rubber_likelihood(
                home_standing, away_standing, self._calc_season_progress(match)),
            # Asymmetric motivation: one team has stakes (relegation fight / europa
            # race) and the other doesn't. Negative = away is more motivated.
            'motivation_asymmetry': self._motivation_asymmetry(home_standing, away_standing),
            # Travel + fixture congestion proxies — already-existing days_since fields
            # surface raw count, this normalises to "is congested" {0, 1}.
            'home_congested_fixtures': 1 if (
                self.calculate_days_since_last_match(home_team_id, match_date) or 99
            ) <= 3 else 0,
            'away_congested_fixtures': 1 if (
                self.calculate_days_since_last_match(away_team_id, match_date) or 99
            ) <= 3 else 0,
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
                    if m.winner == 'HOME_TEAM':
                        pts += 3
                    elif m.winner == 'DRAW':
                        pts += 1
                else:
                    if m.winner == 'AWAY_TEAM':
                        pts += 3
                    elif m.winner == 'DRAW':
                        pts += 1
            return float(pts)

        # Goals scored/conceded avg (last 5 home/away matches)
        home_home = self._get_before(self._team_home_matches[home_id], match_date, 5)
        away_away = self._get_before(self._team_away_matches[away_id], match_date, 5)
        home_home_conceded = [m.away_score for m in home_home if m.away_score is not None]
        away_away_conceded = [m.home_score for m in away_away if m.home_score is not None]
        home_home_scored = [m.home_score for m in home_home if m.home_score is not None]
        away_away_scored = [m.away_score for m in away_away if m.away_score is not None]

        # xG averages — only for matches that have xg data (top-5 leagues).
        # None when sample is empty so caller / model can distinguish from 0.0
        # (which legitimately means "team averages 0 xG", essentially never).
        home_xg_for = [m.xg_home for m in home_home if getattr(m, 'xg_home', None) is not None]
        away_xg_for = [m.xg_away for m in away_away if getattr(m, 'xg_away', None) is not None]
        home_xg_against = [m.xg_away for m in home_home if getattr(m, 'xg_away', None) is not None]
        away_xg_against = [m.xg_home for m in away_away if getattr(m, 'xg_home', None) is not None]

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
                if m.winner == 'HOME_TEAM':
                    h2h_home_wins += 1
                elif m.winner == 'AWAY_TEAM':
                    h2h_away_wins += 1
                else:
                    h2h_draws += 1
            else:
                if m.winner == 'AWAY_TEAM':
                    h2h_home_wins += 1
                elif m.winner == 'HOME_TEAM':
                    h2h_away_wins += 1
                else:
                    h2h_draws += 1

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
            'home_xg_for_avg': float(np.mean(home_xg_for)) if home_xg_for else 0.0,
            'away_xg_for_avg': float(np.mean(away_xg_for)) if away_xg_for else 0.0,
            'home_xg_against_avg': float(np.mean(home_xg_against)) if home_xg_against else 0.0,
            'away_xg_against_avg': float(np.mean(away_xg_against)) if away_xg_against else 0.0,
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
            # v2 motivation (used after next retrain)
            'home_is_safe': self._is_safe(home_standing),
            'away_is_safe': self._is_safe(away_standing),
            'dead_rubber_likelihood': self._dead_rubber_likelihood(
                home_standing, away_standing, self._calc_season_progress(match)),
            'motivation_asymmetry': self._motivation_asymmetry(home_standing, away_standing),
            'home_congested_fixtures': 1 if (
                self.calculate_days_since_last_match(home_id, match.date) or 99
            ) <= 3 else 0,
            'away_congested_fixtures': 1 if (
                self.calculate_days_since_last_match(away_id, match.date) or 99
            ) <= 3 else 0,
            # Squad strength proxy
            'home_avg_position_3yr': self._calc_avg_position(home_id, season),
            'away_avg_position_3yr': self._calc_avg_position(away_id, season),
            # Pitch type
            'home_artificial_pitch': self._is_artificial_pitch(home_id),
        }

    def _get_standing_from_cache(self, team_id, season, match):
        """League-table features for one club, as the table stood at kickoff.

        Resolution order:
          1. the snapshot taken for this exact match (bulk feature engineering),
          2. the table computed as of the match date (upcoming fixtures, and
             European ties, which use the club's DOMESTIC table),
          3. the stored Standing row — final-table data, so a last resort only,
             kept for competitions we can't reconstruct (e.g. a club whose league
             isn't in our match history).
        """
        defaults = {'league_position': 10, 'points': 0, 'goal_difference': 0, 'played': 0}
        match_comp = getattr(match, 'competition', '') or ''
        date = getattr(match, 'date', None)
        european = self.is_european_competition(match)

        # A club's Champions League group position says little about its strength;
        # its domestic table is the meaningful signal, as before.
        comp = (self._domestic_competition(team_id, season) or match_comp) if european else match_comp

        if not european:
            snapshot = self._pit_standings.get(getattr(match, 'id', None))
            if snapshot:
                if team_id == match.home_team_id:
                    return self._seed_early_position(snapshot['home'], team_id, comp, season)
                if team_id == match.away_team_id:
                    return self._seed_early_position(snapshot['away'], team_id, comp, season)

        if date is not None and comp:
            entry = self._table_as_of(comp, season, date).get(team_id)
            if entry:
                return self._seed_early_position(entry, team_id, comp, season)

        stored = (self._standings_cache.get((team_id, season, comp))
                  or self._standings_cache.get((team_id, season, match_comp)))
        if stored:
            return {'league_position': stored.position, 'points': stored.points,
                    'goal_difference': stored.goal_difference,
                    'played': EARLY_SEASON_MATCHES}
        return defaults

    def _seed_early_position(self, entry, team_id, competition, season):
        """Replace a not-yet-meaningful league position with last season's finish.

        Applied in ONE place so bulk feature engineering and live prediction
        cannot drift apart — the two paths reach the table differently (stored
        snapshot vs computed as-of) but both come through here. Returns a new
        dict; snapshots are shared between the home and away lookups and must
        not be mutated.
        """
        if entry.get('played', 0) >= EARLY_SEASON_MATCHES:
            return entry
        seeded = self._previous_season_position(team_id, competition, season)
        if seeded is None:
            return entry
        return {**entry, 'league_position': seeded}
    
    def create_features_for_all_matches(self, save_to_db=True, force=False):
        """
        Create features for all matches in database

        Args:
            save_to_db (bool): Whether to save features to database
            force (bool): Recompute every match, even ones that look current
                AND carry the current pipeline version. Rarely needed now —
                a pipeline change should bump FEATURE_PIPELINE_VERSION instead,
                which makes every stale row rebuild itself on the next run.
                Keep this for the case where the stored values are suspect for
                some reason the version stamp can't express.

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
                row[0]: (row[1], row[2])
                for row in self.db.session.query(
                    MatchFeatures.match_id, MatchFeatures.created_at,
                    MatchFeatures.pipeline_version,
                ).all()
            }
            stale_version = sum(1 for _c, v in existing_features.values()
                                if v != FEATURE_PIPELINE_VERSION)
            if existing_features:
                print(f"Found {len(existing_features)} previously-processed matches "
                      f"({stale_version} on an older pipeline — rebuilding those)...")

        skipped_count = 0

        for idx, match in enumerate(matches):
            if not force and feature_row_is_current(existing_features.get(match.id), match):
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
                    existing.pipeline_version = FEATURE_PIPELINE_VERSION
                else:
                    # Create new features — map all feature dict keys to MatchFeatures columns
                    feature_kwargs = {k: v for k, v in features.items()
                                      if k not in ('target',) and hasattr(MatchFeatures, k)}
                    feature_kwargs['pipeline_version'] = FEATURE_PIPELINE_VERSION
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
    
    def _table_as_of_db(self, competition, season, date):
        """League table for (competition, season) as of `date`, straight from SQL.

        The serving counterpart to _table_as_of. Bulk feature engineering holds
        every match in memory and can walk them; a single prediction cannot —
        _build_match_cache is only called by create_features_for_all_matches, so
        at serve time the in-memory group tables are empty.

        One aggregate per (competition, season, day), memoised. Rounding the key
        to the day matters: a Saturday slate shares one table rather than
        re-querying per kickoff time, and results only land between matchdays
        anyway.
        """
        from sqlalchemy import text

        if not competition or season is None or date is None:
            return {}
        # Keyed on the exact kickoff, not the day. Day-keying looked like a free
        # win on cache hits and quietly lost information: with staggered
        # kickoffs — 353 competition-days in the 2025 season alone — a 20:30
        # fixture reused the table built for the 13:00 one and never saw the
        # afternoon's results, while the training path did.
        key = (competition, season, date)
        cached = self._db_table_cache.get(key)
        if cached is not None:
            built_at, table = cached
            if (time.monotonic() - built_at) < DB_TABLE_CACHE_TTL:
                return table
            del self._db_table_cache[key]

        rows = self.db.session.execute(text("""
            SELECT team_id,
                   SUM(points) AS points,
                   SUM(gf) AS gf,
                   SUM(ga) AS ga,
                   COUNT(*) AS played
            FROM (
                SELECT home_team_id AS team_id,
                       CASE winner WHEN 'HOME_TEAM' THEN 3 WHEN 'DRAW' THEN 1 ELSE 0 END AS points,
                       home_score AS gf, away_score AS ga
                FROM matches
                WHERE competition = :comp AND season = :season
                  AND status = 'FINISHED' AND winner IS NOT NULL
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date < :cutoff
                UNION ALL
                SELECT away_team_id AS team_id,
                       CASE winner WHEN 'AWAY_TEAM' THEN 3 WHEN 'DRAW' THEN 1 ELSE 0 END AS points,
                       away_score AS gf, home_score AS ga
                FROM matches
                WHERE competition = :comp AND season = :season
                  AND status = 'FINISHED' AND winner IS NOT NULL
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date < :cutoff
            ) legs
            GROUP BY team_id
        """), {'comp': competition, 'season': season, 'cutoff': date}).fetchall()

        # Every club in the division, not only those with a result yet. The
        # in-memory table seeds all known teams at zero, and a table missing its
        # winless clubs ranks everyone else too high — which is what made the two
        # paths disagree on position while agreeing on points.
        roster = self.db.session.execute(text("""
            SELECT DISTINCT home_team_id AS team_id FROM matches
            WHERE competition = :comp AND season = :season
            UNION
            SELECT DISTINCT away_team_id AS team_id FROM matches
            WHERE competition = :comp AND season = :season
        """), {'comp': competition, 'season': season}).fetchall()

        table = {r[0]: {'points': 0, 'gf': 0, 'ga': 0, 'played': 0} for r in roster}
        for r in rows:
            table[r[0]] = {'points': int(r[1] or 0), 'gf': int(r[2] or 0),
                           'ga': int(r[3] or 0), 'played': int(r[4] or 0)}
        positions = self._rank(table)
        built = {tid: self._table_entry(table, positions, tid) for tid in table}
        if len(self._db_table_cache) > 512:
            self._db_table_cache.clear()
        self._db_table_cache[key] = (time.monotonic(), built)
        return built

    def get_standing_features(self, team_id, season, match=None):
        """League position, points and goal difference as they stood at kickoff.

        WHY THIS DOES NOT READ THE standings TABLE ANY MORE
        ---------------------------------------------------
        It used to, via db.get_team_standing(team_id, season), with no date
        filter. Those rows are END-OF-SEASON tables. On the opening fixture of
        2024-25 that returned Manchester United on 42 points with a goal
        difference of -10 — their final table, handed to the model on matchday 1.

        Bulk feature engineering was moved onto point-in-time tables, but this
        method is the one every LIVE prediction goes through, and it was left
        behind. The result was worse than the original leak: training learned
        "points" as a running total while serving supplied a season-final one,
        so the two disagreed about what the feature meant.

        Falls back to the stored row only when the match is unknown (no
        competition/date to reconstruct from) — callers that pass `match` get a
        point-in-time answer.
        """
        if match is not None:
            date = getattr(match, 'date', None)
            match_comp = getattr(match, 'competition', None)
            # A club's Champions League group position says little about its
            # strength; its domestic table is the meaningful signal. Same rule as
            # _get_standing_from_cache, so the two paths cannot diverge.
            if self.is_european_competition(match):
                competition = self._domestic_competition_db(team_id, season) or match_comp
            else:
                competition = match_comp
            entry = self._table_as_of_db(competition, season, date).get(team_id)
            if entry:
                return self._seed_early_position(entry, team_id, competition, season)
            seeded = self._previous_season_position(team_id, competition, season)
            if seeded is not None:
                return {'league_position': seeded, 'points': 0,
                        'goal_difference': 0, 'played': 0}
            return {'league_position': 10, 'points': 0, 'goal_difference': 0, 'played': 0}

        # No match context — cannot place this in time. Stored rows are
        # end-of-season, so they are only safe for a season already over.
        standing = self.db.get_team_standing(team_id, season)
        if standing:
            return {
                'league_position': standing.position,
                'points': standing.points,
                'goal_difference': standing.goal_difference,
                'played': EARLY_SEASON_MATCHES,
            }
        # Defaults for teams without standings data (e.g., old seasons)
        return {
            'league_position': 10,
            'points': 0,
            'goal_difference': 0,
            'played': 0,
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
    

