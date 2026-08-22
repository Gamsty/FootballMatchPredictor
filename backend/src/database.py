"""
Database Module

Handles all database operations using SQLAlchemy.

Tables:
    - teams:                Team info (name, API ID, competition)
    - matches:              Match results (scores, winner, date, stats, odds, xG, lineups)
    - standings:            League table position per (team, season, competition)
    - match_features:       Computed ML features per match (form, goals avg, h2h, ...)
    - predictions:          Latest model prediction per match + evaluation
    - prediction_snapshots: Append-only history of every prediction computed
    - bets:                 Placed bets (single or combo) with settlement + CLV
    - odds_snapshots:       Append-only bookmaker price history per market outcome

Schema changes ship through init_db() → _apply_lightweight_migrations(), which
adds missing columns and missing indexes idempotently. There is no alembic; see
that function for what it can and cannot express.
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, ForeignKey,
    Boolean, Index, JSON, inspect, text,
)
from sqlalchemy.orm import (
    sessionmaker, relationship, declarative_base, scoped_session, joinedload,
)
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import logging
import os

load_dotenv()

logger = logging.getLogger(__name__)


def _utcnow_naive():
    """Return current UTC time as a naive datetime.

    datetime.utcnow() is deprecated in Python 3.12+ and scheduled for removal.
    Our DateTime columns are defined without timezone=True so they store naive
    UTC, and switching to tz-aware would require a schema migration. This helper
    gives us a non-deprecated path with identical wire format: take the tz-aware
    'now' and strip the tzinfo before SQLAlchemy sees it.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Database connection setup — reads DATABASE_URL from .env file
DATABASE_URL = os.getenv('DATABASE_URL')

# Convert to postgreqsl
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Default to local database if not set
if not DATABASE_URL:
    DATABASE_URL= 'postgresql://postgres:password@localhost/football_predictor'

def _engine_options(url: str) -> dict:
    """
    Connection-pool settings, chosen per dialect.

    pool_size / max_overflow / pool_timeout are QueuePool-only options. Passing
    them alongside a SQLite URL raises TypeError at import — which is why the
    persistence tests used to monkeypatch create_engine just to get an in-memory
    database. Selecting by dialect makes `DATABASE_URL=sqlite://` work directly.

    Budget note: these are per-PROCESS. pool_size + max_overflow (7) × gunicorn
    workers (2) × replicas (2) = 28 connections, before the Container Apps Jobs
    take theirs, against a Burstable B1ms ceiling near 50. Tune via env rather
    than editing, so a bigger SKU doesn't need a code change.
    """
    if url.startswith('sqlite'):
        return {}
    return {
        # Azure PG closes idle connections server-side; ping before handing one out.
        'pool_pre_ping': True,
        'pool_size': int(os.getenv('DB_POOL_SIZE', '5')),
        'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', '2')),
        'pool_recycle': int(os.getenv('DB_POOL_RECYCLE', '300')),
        'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', '10')),
    }


engine = create_engine(DATABASE_URL, **_engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine)  # Factory for creating database sessions
ScopedSession = scoped_session(SessionLocal)  # Thread-safe session factory
Base = declarative_base()  # Base class for all ORM models

# ============================================================
# ORM Models — define the database table structure
# ============================================================

class Team(Base):
    """Stores football team information. Linked to matches via foreign keys."""
    __tablename__ = 'teams'

    id = Column(Integer, primary_key=True)
    api_id = Column(Integer, unique=True, nullable=False) # ID from API
    name = Column(String(100), nullable=False)
    short_name = Column(String(50))
    competition = Column(String(100))

    # Relationships — a team can be home or away in many matches
    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")
    standings = relationship("Standing", back_populates="team")

    created_at = Column(DateTime, default=_utcnow_naive)

class Match(Base):
    """Stores individual match results. Each match links to two teams (home/away)."""
    __tablename__ = 'matches'

    id = Column(Integer, primary_key=True)
    api_id = Column(Integer, unique=True, nullable=False)

    # Teams
    # No index=True here: the composite indexes below start with these columns,
    # and a leading-column prefix serves single-column lookups just as well. The
    # standalone ones only added write cost — _REDUNDANT_INDEXES drops them once
    # the composites are confirmed present.
    home_team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    away_team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)

    # Match details
    season = Column(Integer, nullable=False)
    matchday = Column(Integer)
    competition = Column(String(100), nullable=False, index=True)
    stage = Column(String(50))  # REGULAR_SEASON, GROUP_STAGE, LEAGUE_STAGE, LAST_16, QUARTER_FINALS, etc.
    date = Column(DateTime, nullable=False, index=True)
    # Indexed: `status == 'FINISHED'` is the filter behind team stats, the
    # statistics aggregates and every settlement sweep, over 40k+ rows.
    status = Column(String(20), nullable=False, index=True)

    # Composite indexes for feature engineering queries
    __table_args__ = (
        Index('ix_matches_home_date_status', 'home_team_id', 'date', 'status'),
        Index('ix_matches_away_date_status', 'away_team_id', 'date', 'status'),
    )

    # Scores
    home_score = Column(Integer)
    away_score = Column(Integer)
    home_ht_score = Column(Integer)  # Half-time home goals (from football-data.co.uk CSVs)
    away_ht_score = Column(Integer)  # Half-time away goals (from football-data.co.uk CSVs)
    winner = Column(String(20)) # HOME_TEAM, AWAY_TEAM, DRAW

    # Match statistics (from football-data.co.uk CSVs — NULL for API-sourced matches)
    home_shots = Column(Integer)
    away_shots = Column(Integer)
    home_shots_on_target = Column(Integer)
    away_shots_on_target = Column(Integer)
    home_corners = Column(Integer)
    away_corners = Column(Integer)
    home_fouls = Column(Integer)
    away_fouls = Column(Integer)
    home_yellow_cards = Column(Integer)
    away_yellow_cards = Column(Integer)
    home_red_cards = Column(Integer)
    away_red_cards = Column(Integer)

    # Betting odds (from football-data.co.uk CSVs — NULL for API-sourced matches)
    b365_home = Column(Float)
    b365_draw = Column(Float)
    b365_away = Column(Float)
    avg_home_prob = Column(Float)  # Normalized implied probability from market average
    avg_draw_prob = Column(Float)
    avg_away_prob = Column(Float)

    # Expected goals from understat (covers top-5 leagues only — NULL elsewhere).
    # Backfilled by jobs/scrape_xg.py; used by FeatureEngineer to build
    # recent-form xG aggregates that the model learns on alongside raw goals.
    xg_home = Column(Float)
    xg_away = Column(Float)

    # Sofascore event_id — caches the lookup so we don't re-resolve every
    # time we fetch lineups. Set by jobs/scrape_lineups.py the first time
    # we find this match on sofascore.
    sofascore_event_id = Column(Integer, index=True)

    # JSON snapshot of starting XI + bench + missing players. Refreshed
    # ~1h before kickoff by jobs/scrape_lineups.py. Stays NULL when the
    # lineup hasn't been posted yet or the match doesn't exist on sofascore.
    # Shape: {confirmed, home_starting:[], away_starting:[],
    #         home_missing:[{name,reason}], away_missing:[]}
    lineups = Column(JSON)
    lineups_fetched_at = Column(DateTime)

    # Relationships — link back to teams and forward to features/predictions
    # uselist=False means one-to-one (each match has one feature set and one prediction)
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    features = relationship("MatchFeatures", back_populates="match", uselist=False)
    prediction = relationship("Prediction", back_populates="match", uselist=False)

    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

class Standing(Base):
    """
    A team's league-table row for one (season, competition).

    Loaded from the external CSVs / API rather than computed at read time —
    FeatureEngineer reads position, points and goal difference per match, so this
    is on the hot path for both training and prediction.
    """
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    season = Column(Integer, nullable=False)
    competition = Column(String(100), nullable=False)

    position = Column(Integer)
    played = Column(Integer)
    won = Column(Integer)
    drawn = Column(Integer)
    lost = Column(Integer)
    goals_for = Column(Integer)
    goals_against = Column(Integer)
    goal_difference = Column(Integer)
    points = Column(Integer)

    team = relationship('Team', back_populates="standings")
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        # add_standing() upserts on exactly this triple with a read-then-write, so
        # without the unique index two loaders running together can duplicate a row
        # — after which get_team_standing()'s .first() picks one arbitrarily and
        # standings features silently go non-deterministic.
        #
        # It doubles as the lookup index: (team_id, season) is a usable prefix, and
        # this table previously had no index at all beyond its primary key, so every
        # get_team_standing / get_team_domestic_standing call — one per team per
        # match during feature engineering — was a sequential scan.
        Index('uq_standings_team_season_comp',
              'team_id', 'season', 'competition', unique=True),
    )

class MatchFeatures(Base):
    """Computed ML features for each match. Generated by feature_engineering.py."""
    __tablename__ = 'match_features'

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey('matches.id'), unique=True, nullable=False)

    # Which feature pipeline produced this row. The incremental recompute skips
    # a match whose features are newer than the match record — which only ever
    # notices changes to the MATCH, never to the pipeline. That let rows built
    # by an older definition (end-of-season league tables, a different
    # imputation) sit in the training set forever, looking current. Comparing
    # this against feature_engineering.FEATURE_PIPELINE_VERSION makes "the
    # pipeline changed" a condition the recompute can see for itself.
    # NULL means "written before versioning existed" and always recomputes.
    pipeline_version = Column(String(20))

    # Form features — average points per game over last 5 matches
    home_form_5 = Column(Float)
    away_form_5 = Column(Float)

    # Goal averages — rolling mean of goals scored/conceded over recent matches
    home_goals_scored_avg = Column(Float)
    home_goals_conceded_avg = Column(Float)
    away_goals_scored_avg = Column(Float)
    away_goals_conceded_avg = Column(Float)

    # Expected goals averages — same shape as raw goal averages but from
    # understat's shot-quality model. Strictly better signal for short windows
    # (luck-corrected). NULL for teams in leagues understat doesn't cover —
    # FeatureEngineer falls back to the raw goal averages then.
    home_xg_for_avg = Column(Float)
    home_xg_against_avg = Column(Float)
    away_xg_for_avg = Column(Float)
    away_xg_against_avg = Column(Float)

    # Lineup-derived injury features. Counts of regular starters missing
    # from sofascore's pre-match lineup. NULL when lineup wasn't available
    # at scrape time (small sample, deep history, or league not covered).
    # Model uses these alongside xG; missing players hurt prediction quality
    # in goal-poor leagues where one striker carries the squad.
    home_starters_missing = Column(Integer)
    away_starters_missing = Column(Integer)

    # Head-to-head record — results from last 5 meetings between these two teams
    h2h_home_wins = Column(Integer)
    h2h_draws = Column(Integer)
    h2h_away_wins = Column(Integer)

    # Strength indicators — overall win rate and rest days
    home_win_rate = Column(Float)  # Cumulative home win percentage
    away_win_rate = Column(Float)  # Cumulative away win percentage
    days_since_home_last_match = Column(Integer)  # Rest days for home team
    days_since_away_last_match = Column(Integer)  # Rest days for away team

    # League standings features
    home_league_position = Column(Integer)
    away_league_position = Column(Integer)
    home_points = Column(Integer)
    away_points = Column(Integer)
    home_goal_difference = Column(Integer)
    away_goal_difference = Column(Integer)

    # Draw & defensive features
    home_draw_rate = Column(Float)
    away_draw_rate = Column(Float)
    home_clean_sheet_rate = Column(Float)
    away_clean_sheet_rate = Column(Float)

    # Weighted form (exponential decay)
    home_weighted_form = Column(Float)
    away_weighted_form = Column(Float)

    # Rolling match statistics (averages from previous matches)
    home_shots_on_target_avg = Column(Float)
    away_shots_on_target_avg = Column(Float)
    home_corners_avg = Column(Float)
    away_corners_avg = Column(Float)
    home_cards_avg = Column(Float)
    away_cards_avg = Column(Float)

    # Betting odds implied probabilities (NULL for API-sourced matches)
    avg_home_prob = Column(Float)
    avg_draw_prob = Column(Float)
    avg_away_prob = Column(Float)

    # Motivation features
    home_points_from_top = Column(Integer)
    away_points_from_top = Column(Integer)
    home_points_from_relegation = Column(Integer)
    away_points_from_relegation = Column(Integer)
    season_progress = Column(Float)

    # Squad strength proxy
    home_avg_position_3yr = Column(Float)
    away_avg_position_3yr = Column(Float)

    # Pitch type
    home_artificial_pitch = Column(Integer)  # 0 or 1

    # Relationship
    match = relationship("Match", back_populates="features")

    created_at = Column(DateTime, default=_utcnow_naive)

class Prediction(Base):
    """
    Latest model prediction per match. Idempotent on match_id — every persist
    overwrites the previous row, so this table always reflects what the model
    currently says about each fixture.

    Calibration uses this table: ECE / Brier are computed on the latest snapshot
    against the actual outcome. For time-series analysis (CLV — how the
    probability moved between bet placement and kickoff), use PredictionSnapshot
    instead, which preserves history.
    """
    __tablename__ = 'predictions'

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey('matches.id'), unique=True, nullable=False)

    # Prediction
    predicted_winner = Column(String(20)) # HOME_TEAM, AWAY_TEAM, DRAW
    home_win_prob = Column(Float)
    draw_prob = Column(Float)
    away_win_prob = Column(Float)
    confidence = Column(Float) # Max probability

    # Model info
    model_version = Column(String(50))
    model_type = Column(String(50)) # 'xgboost', 'random_forest', etc.

    # Evaluation (filled after match is played)
    actual_winner = Column(String(50))
    correct = Column(Boolean)

    # Relationship
    match = relationship("Match", back_populates="prediction")

    created_at = Column(DateTime, default=_utcnow_naive)


class Bet(Base):
    """
    A user-placed bet on a market outcome (paper or real money).

    Lifecycle:
        placed (status='pending') → match plays → settle_bets() flips to won/lost/void
        → profit_loss is recorded for ROI tracking

    Why we record `model_prob_at_bet` and `edge_at_bet` separately from the
    current value: those numbers tell us what the model SAID when the bet was
    placed — that's what determines if the bet was justified by our analytical
    framework. The current model state may have shifted since (retrain, new
    calibrator). For honest backtest the bet-time snapshot is what we compare
    to bookmaker prices and to actual outcomes.

    `closing_odds` is filled by a later snapshot job (ideally an hour before
    kickoff) so we can compute CLV = (placed_odds - closing_odds) / closing_odds.
    Positive CLV over many bets is the only short-run +EV proof.
    """
    __tablename__ = 'bets'

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey('matches.id'), nullable=False, index=True)

    # Market identification
    market = Column(String(20), nullable=False)       # 'h2h' | 'totals_2_5' | 'btts' | 'combo' | 'compound'
    outcome_key = Column(String(20), nullable=False)  # 'home'/'draw'/'away' | 'over'/'under' | 'yes'/'no'
                                                       # 'multi' for combo, 'h_btts_yes'/'a_btts_no'/etc for compound
    outcome_label = Column(String(80))                # human-readable, e.g., 'Home Win' or 'Home Win & Both Score'

    # Combo legs — JSON array for market='combo'. Each leg is a dict:
    #   {match_id, market, outcome_key, outcome_label, odds, prob, home_team, away_team, competition, date}
    # Settle walks each leg's resolver; combo wins iff ALL legs win.
    # For singles this is NULL.
    combo_legs = Column(JSON, nullable=True)

    # Bet placement
    odds_at_bet = Column(Float, nullable=False)
    stake = Column(Float, nullable=False)             # NOK
    bookmaker = Column(String(60))
    placed_at = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)
    placed_via = Column(String(20), default='manual') # 'manual' (frontend), 'api', etc.

    # Model snapshot at placement — captures what we said at the moment
    model_prob_at_bet = Column(Float)
    edge_at_bet = Column(Float)
    model_version_at_bet = Column(String(50))

    # CLV — populated by jobs/snapshot_odds.py shortly before kickoff
    closing_odds = Column(Float)
    closing_snapshot_at = Column(DateTime)

    # Settlement
    status = Column(String(20), default='pending', nullable=False, index=True)
    # status ∈ {pending, won, lost, void}
    settled_at = Column(DateTime)
    profit_loss = Column(Float)  # NOK; positive for wins, negative for losses, 0 for void

    # Optional metadata
    notes = Column(String(500))

    match = relationship("Match")


class OddsSnapshot(Base):
    """
    Append-only history of best/median bookmaker odds per (match, market, outcome).

    Both types are written by the same snapshot job (src/odds_snapshot.py,
    reachable as POST /api/admin/snapshot-closing-odds):
      - 'realtime'  — a run with closing=false, over a wide window. Useful for
                      tracking price drift over the days before a match.
      - 'closing'   — a run with closing=true, over a 2h pre-kickoff window.
                      The GitHub Actions cron fires this every 30 min; the last
                      write before kickoff is the canonical closing price.

    These rows are also what makes CLV work for a single-bookmaker operator:
    `bets.closing_odds` holds a best-sharp-book price that no Norsk Tipping
    bettor could have taken, so /api/bets/performance de-vigs the snapshot set
    for a market and compares against the resulting fair line instead. See
    app._fair_closing_odds.

    Index on (match_id, market, outcome_key, snapshot_at desc) so closing-line
    lookups are O(log n).
    """
    __tablename__ = 'odds_snapshots'

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey('matches.id'), nullable=False, index=True)

    market = Column(String(20), nullable=False)
    outcome_key = Column(String(10), nullable=False)

    best_odds = Column(Float, nullable=False)
    best_bookmaker = Column(String(60))
    median_odds = Column(Float)
    book_count = Column(Integer)

    snapshot_type = Column(String(20), default='realtime', nullable=False)  # 'realtime' | 'closing'
    snapshot_at = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)

    match = relationship("Match")

    __table_args__ = (
        Index('ix_odds_snap_match_market_outcome_time',
              'match_id', 'market', 'outcome_key', 'snapshot_at'),
    )


class PredictionSnapshot(Base):
    """
    Immutable time-series log of every prediction we compute.

    Why a separate table: Prediction is keyed on match_id (latest-state per match)
    so we can't track how the probabilities moved over time on the same row.
    PredictionSnapshot is append-only — one row per (match_id, prediction time),
    so:
        - CLV analysis: compare prob @ first-seen vs prob @ kickoff
        - Model-version comparison: filter by model_version to compare e.g.
          v1 vs v2 calibration on the same fixtures
        - Audit trail: when did the model "change its mind" about a match

    Bounded growth via the `model_version` field — when retrain promotes a
    new model, downstream analysis can scope to "predictions from this
    version onwards" without polluting averages across versions.

    Index on (match_id, created_at desc) so CLV-style "give me the latest
    snapshot per match" queries are fast.
    """
    __tablename__ = 'prediction_snapshots'

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey('matches.id'), nullable=False)

    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)
    confidence = Column(Float)

    model_version = Column(String(50), nullable=False)
    model_type = Column(String(50))

    # Snapshot taken at this time — defaults to now, but the backfill job can
    # set it explicitly to (match.date - 1 hour) so historical CLV analyses
    # don't get clobbered to "all snapshots from today".
    created_at = Column(DateTime, default=_utcnow_naive, nullable=False, index=True)

    __table_args__ = (
        Index('ix_pred_snap_match_created', 'match_id', 'created_at'),
    )

# ============================================================
# Database utility functions
# ============================================================

def init_db():
    """Create all tables in the database based on the ORM models above.

    Also runs lightweight idempotent migrations — column adds and index sync —
    for changes made to tables that already exist. We don't have alembic; this is
    the cheapest way to ship a schema change without a manual DB step in every
    environment.
    """
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    print("Database tables created successfully!")


def _apply_lightweight_migrations():
    """Idempotently reconcile an existing database with the models above.

    Two passes, both safe to re-run:
      1. ADD COLUMN for columns introduced after a table was first created.
         Append to MIGRATIONS below — each entry is (table, column, type_sql).
         Existence is tested via the dialect's introspection rather than
         IF NOT EXISTS, which some PG/SQLite versions reject on ADD COLUMN.
      2. Index sync — create declared-but-missing indexes, drop known-redundant
         ones. See _sync_indexes for why create_all() cannot do this itself.

    Every statement runs in its own transaction: in Postgres a failed DDL poisons
    the surrounding transaction, so one impossible migration used to take every
    later one down with it.
    """
    MIGRATIONS = [
        ('bets', 'combo_legs',
         'JSONB' if engine.dialect.name == 'postgresql' else 'JSON'),
        # Feature-pipeline stamp. Existing rows get NULL, which the recompute
        # reads as "unknown, rebuild" — so the first run after this ships
        # refreshes the whole table onto the current definition.
        ('match_features', 'pipeline_version', 'VARCHAR(20)'),
        # understat xG — covers top-5 leagues only; rest of matches stay NULL.
        # FeatureEngineer treats NULL as "use the goal-based fallback" so
        # mixed coverage degrades gracefully.
        ('matches', 'xg_home', 'FLOAT'),
        ('matches', 'xg_away', 'FLOAT'),
        # xG-based rolling features. Computed by FeatureEngineer when xG
        # data is available for the team's recent matches; NULL otherwise.
        # Adding new columns to match_features is safe — the loaded model
        # uses feature_names to select inputs, so old models ignore them.
        ('match_features', 'home_xg_for_avg', 'FLOAT'),
        ('match_features', 'home_xg_against_avg', 'FLOAT'),
        ('match_features', 'away_xg_for_avg', 'FLOAT'),
        ('match_features', 'away_xg_against_avg', 'FLOAT'),
        # Sofascore lineup snapshot per match. Populated by jobs/scrape_lineups.py
        # ~1h before kickoff. Stays NULL for matches not on sofascore.
        ('matches', 'sofascore_event_id', 'INTEGER'),
        ('matches', 'lineups',
         'JSONB' if engine.dialect.name == 'postgresql' else 'JSON'),
        ('matches', 'lineups_fetched_at', 'TIMESTAMP'),
        # Aggregated injury-impact features per match. Counts of missing players
        # weighted by sofascore's missingType tag. Computed at scrape time, not
        # at prediction time — so we don't pay sofascore-fetch latency on the
        # request path.
        ('match_features', 'home_starters_missing', 'INTEGER'),
        ('match_features', 'away_starters_missing', 'INTEGER'),
    ]

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    for table, column, type_sql in MIGRATIONS:
        if table not in table_names:
            # Table doesn't exist yet — create_all just made it with the column
            # already on board. Nothing to do.
            continue
        if column in {c['name'] for c in inspector.get_columns(table)}:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {type_sql}'))
            print(f"Migration: added {table}.{column} ({type_sql})")
        except Exception as e:
            print(f"Migration: could not add {table}.{column} "
                  f"({type(e).__name__}: {e})")

    _sync_indexes(inspect(engine), table_names)


# Indexes that exist in older databases but are redundant now. Each entry is
# (table, index_name, required_index) — the drop only runs when `required_index`
# is present, so we never remove the only usable index for a query path.
#   - ix_<table>_id duplicated the primary key's own unique index on every table
#     (a stray index=True on the PK column), costing a write per insert for nothing.
#   - ix_matches_home_team_id / ix_matches_away_team_id are prefix-covered by the
#     composite (team, date, status) indexes.
_REDUNDANT_INDEXES = [
    ('teams', 'ix_teams_id', None),
    ('matches', 'ix_matches_id', None),
    ('standings', 'ix_standings_id', None),
    ('match_features', 'ix_match_features_id', None),
    ('predictions', 'ix_predictions_id', None),
    ('bets', 'ix_bets_id', None),
    ('odds_snapshots', 'ix_odds_snapshots_id', None),
    ('prediction_snapshots', 'ix_prediction_snapshots_id', None),
    ('matches', 'ix_matches_home_team_id', 'ix_matches_home_date_status'),
    ('matches', 'ix_matches_away_team_id', 'ix_matches_away_date_status'),
]


def _sync_indexes(inspector, table_names):
    """Create declared-but-missing indexes; drop the ones listed as redundant.

    create_all() only emits indexes as part of CREATE TABLE — it skips existing
    tables wholesale, indexes included. So an Index added to __table_args__ after
    a table already existed never lands in any database that predates the change,
    and nothing complains. That is exactly what happened to
    ix_matches_home_date_status / ix_matches_away_date_status: declared well after
    the matches table was created, therefore absent from production, despite being
    the indexes the feature-engineering queries were written against. The ADD
    COLUMN list can't express an index, so this pass exists to close that gap for
    every index, past and future.
    """
    for table in Base.metadata.sorted_tables:
        if table.name not in table_names:
            continue  # freshly created by create_all — indexes came with it
        existing = {i['name'] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            try:
                with engine.begin() as conn:
                    index.create(bind=conn, checkfirst=True)
                print(f"Migration: created index {index.name} on {table.name}")
            except Exception as e:
                # The expected failure is a UNIQUE index over data that already
                # violates it. Say so plainly instead of dying during startup.
                print(f"Migration: could not create index {index.name} on {table.name} "
                      f"({type(e).__name__}: {e}). If it is UNIQUE, de-duplicate first.")

    for table, index_name, required in _REDUNDANT_INDEXES:
        if table not in table_names:
            continue
        present = {i['name'] for i in inspector.get_indexes(table)}
        if index_name not in present or (required and required not in present):
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f'DROP INDEX IF EXISTS {index_name}'))
            print(f"Migration: dropped redundant index {index_name} on {table}")
        except Exception as e:
            print(f"Migration: could not drop {index_name} "
                  f"({type(e).__name__}: {e})")

# ============================================================
# DatabaseManager — high-level CRUD operations
# ============================================================

class DatabaseManager:
    """Provides methods to add/query teams and matches. Handles upsert logic."""

    def __init__(self):
        self._scoped = ScopedSession

    @property
    def session(self):
        """Return a thread-local session via scoped_session."""
        return self._scoped()

    def close(self):
        """Close/remove the current thread-local session"""
        self._scoped.remove()

    def add_team(self, api_id, name, short_name, competition, commit=True,
                 match_by_name=False):
        """Add a new team or update existing one (matched by api_id).

        commit=False leaves the write pending so bulk loaders can flush once per
        batch — see the note on add_match.

        match_by_name=True adds a fallback lookup on the club's name when the
        api_id misses, and is what the CSV loaders use. Clubs that only appear in
        the football-data.co.uk CSVs get a *synthetic* api_id, but many of them
        also exist under their real football-data.org ID from the API loader.
        Keying on api_id alone gave those clubs two identities — and since
        features are computed per team_id, each one saw only part of the club's
        history. Resolving by name keeps a single row and preserves whichever
        api_id it already had.
        """
        team = self.session.query(Team).filter_by(api_id=api_id).first()

        if team is None and match_by_name:
            team = self.session.query(Team).filter_by(name=name).first()
            if team is not None:
                logger.debug("add_team: resolved %r by name to existing api_id=%s "
                             "(incoming api_id=%s)", name, team.api_id, api_id)

        if team:
            # Update existing
            team.name = name
            team.short_name = short_name
            team.competition = competition
        else:
            # Create new
            team = Team(
                api_id=api_id,
                name=name,
                short_name=short_name,
                competition = competition
            )
            self.session.add(team)

        if commit:
            self.session.commit()
        return team
    
    def add_match(self, match_data, commit=True, natural_key=False):
        """Add a new match or update scores/status if it already exists.

        commit=False leaves the write pending. The CSV loaders insert tens of
        thousands of rows through here, and a commit per row is a transaction and
        a network round-trip per row; they batch instead and commit periodically.

        natural_key=True adds a fallback lookup on
        (competition, season, home_team_id, away_team_id) when the api_id misses.
        The CSV loader's api_id is derived from the row's position in the
        downloaded file, so a revised upstream CSV — one extra rescheduled
        fixture near the top — shifts every later row and would otherwise insert
        duplicates of matches we already hold. In a league season a given
        home/away pairing occurs exactly once, so the tuple is a real identity.
        Only for the round-robin CSV leagues; API-sourced matches have stable IDs
        and leave this off.
        """
        # Look up both teams. Teams must be loaded before matches (FK dependency).
        home_team = self.session.query(Team).filter_by(api_id=match_data['home_team_api_id']).first()
        away_team = self.session.query(Team).filter_by(api_id=match_data['away_team_api_id']).first()

        # Same story as add_team: when a club was resolved by name its stored
        # api_id won't be the synthetic one the CSV row carries, so fall back to
        # the name the loader passed alongside it.
        if home_team is None and match_data.get('home_team_name'):
            home_team = self.session.query(Team).filter_by(
                name=match_data['home_team_name']).first()
        if away_team is None and match_data.get('away_team_name'):
            away_team = self.session.query(Team).filter_by(
                name=match_data['away_team_name']).first()

        match = self.session.query(Match).filter_by(api_id=match_data['api_id']).first()
        if match is None and natural_key and home_team is not None and away_team is not None:
            match = self.session.query(Match).filter_by(
                competition=match_data['competition'],
                season=match_data['season'],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
            ).first()
            if match is not None:
                # Keep the existing api_id — it is this row's identity, and
                # rewriting it to the freshly-generated one would just move the
                # duplicate problem rather than solve it.
                logger.debug("add_match: matched %s vs %s (%s %s) on natural key",
                             home_team.name, away_team.name,
                             match_data['competition'], match_data['season'])

        # Only the insert path dereferences these, but failing here names the
        # missing team instead of raising AttributeError on None thirty lines down.
        if not match and (home_team is None or away_team is None):
            missing = [
                str(match_data[k]) for k, t in
                (('home_team_api_id', home_team), ('away_team_api_id', away_team))
                if t is None
            ]
            raise ValueError(
                f"Cannot add match api_id={match_data['api_id']}: "
                f"team(s) not in database (api_id {', '.join(missing)}). "
                "Load teams before matches."
            )

        # Extra columns (match stats + odds) — may or may not be present
        extra_fields = [
            'home_ht_score', 'away_ht_score',
            'home_shots', 'away_shots', 'home_shots_on_target', 'away_shots_on_target',
            'home_corners', 'away_corners', 'home_fouls', 'away_fouls',
            'home_yellow_cards', 'away_yellow_cards', 'home_red_cards', 'away_red_cards',
            'b365_home', 'b365_draw', 'b365_away',
            'avg_home_prob', 'avg_draw_prob', 'avg_away_prob',
        ]

        if match:
            # Update existing
            match.home_score = match_data.get('home_score')
            match.away_score = match_data.get('away_score')
            match.winner = match_data.get('winner')
            match.status = match_data.get('status')
            match.stage = match_data.get('stage')
            # Kickoff time can move after the initial import — football-data.org
            # often imports matches with TBD/placeholder times and updates them
            # later when the broadcaster confirms. Without this line, our DB
            # would stay stuck on the original (often 00:00) timestamp.
            if match_data.get('date') is not None:
                match.date = match_data['date']
            for field in extra_fields:
                if field in match_data:
                    setattr(match, field, match_data[field])
        else:
            # Create new
            match = Match(
                api_id=match_data['api_id'],
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                season=match_data['season'],
                matchday=match_data.get('matchday'),
                competition=match_data['competition'],
                stage=match_data.get('stage'),
                date=match_data['date'],
                status=match_data['status'],
                home_score=match_data.get('home_score'),
                away_score=match_data.get('away_score'),
                winner=match_data.get('winner'),
                **{f: match_data[f] for f in extra_fields if f in match_data}
            )
            self.session.add(match)

        if commit:
            self.session.commit()
        return match

    def add_standing(self, team_id, season, competition, data, commit=True):
        standing = self.session.query(Standing).filter_by(
            team_id=team_id, season=season, competition=competition
        ).first()

        if standing:
            for key, value in data.items():
                setattr(standing, key, value)
        else:
            standing = Standing(
                team_id=team_id, season=season,
                competition=competition, **data
            )
            self.session.add(standing)
        if commit:
            self.session.commit()
        return standing
    
    def get_team_standing(self, team_id, season):
        return self.session.query(Standing).filter_by(
            team_id=team_id, season=season
        ).first()

    def get_team_domestic_standing(self, team_id, season):
        """
        Get a team's domestic league standing (excludes European competitions).
        Used for European match feature engineering — a team's domestic form
        is more meaningful than their CL group position.
        """
        european_comps = [
            'UEFA Champions League'
        ]
        standing = self.session.query(Standing).filter(
            Standing.team_id == team_id,
            Standing.season == season,
            ~Standing.competition.in_(european_comps)
        ).first()
        return standing
    
    def get_upcoming_matches(self, days=7):
        # Inclusive of the FULL last day. days=3 means today + next 3 calendar
        # days (so 4 days total, ending at midnight of day+3). Without the +1,
        # late-evening matches on the last day get cut off — that's how Serie A
        # Sunday-18:00 fixtures vanished from the dashboard whenever their
        # kickoff times got refreshed from football-data.org (matches previously
        # sat at midnight UTC, right on the boundary).
        # UTC, not local: the DateTime columns store naive UTC (see _utcnow_naive),
        # so datetime.now() compared a local-midnight boundary against UTC values —
        # correct on a UTC container, two hours adrift on a CEST dev machine.
        today_start = _utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
        end = today_start + timedelta(days=days + 1)
        # Eager-load both teams: every caller (dashboard, value bets, cache warm)
        # immediately reads match.home_team / match.away_team, which otherwise
        # lazy-loads two extra SELECTs per fixture.
        return self.session.query(Match).options(
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        ).filter(
            Match.status.in_(['SCHEDULED', 'TIMED']),
            Match.date >= today_start,
            Match.date < end,
        ).order_by(Match.date.asc()).all()
    
# Main execution
if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Database ready!")

