"""
Database Module

Handles all database operations using SQLAlchemy.

Tables:
    - teams: Stores team info (name, API ID, competition)
    - matches: Stores match results (scores, winner, date)
    - match_features: Stores computed ML features per match (form, goals avg, h2h)
    - predictions: Stores model predictions and evaluation results
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import sessionmaker, relationship, declarative_base, scoped_session
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv()

# Database connection setup — reads DATABASE_URL from .env file
DATABASE_URL = os.getenv('DATABASE_URL')

# Convert to postgreqsl
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Default to local database if not set
if not DATABASE_URL:
    DATABASE_URL= 'postgresql://postgres:password@localhost/football_predictor'

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=2,
    pool_recycle=300,
    pool_timeout=10,
)
SessionLocal = sessionmaker(bind=engine)  # Factory for creating database sessions
ScopedSession = scoped_session(SessionLocal)  # Thread-safe session factory
Base = declarative_base()  # Base class for all ORM models

# ============================================================
# ORM Models — define the database table structure
# ============================================================

class Team(Base):
    """Stores football team information. Linked to matches via foreign keys."""
    __tablename__ = 'teams'

    id = Column(Integer, primary_key=True, index=True)
    api_id = Column(Integer, unique=True, nullable=False) # ID from API
    name = Column(String(100), nullable=False)
    short_name = Column(String(50))
    competition = Column(String(100))

    # Relationships — a team can be home or away in many matches
    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")
    standings = relationship("Standing", back_populates="team")

    created_at = Column(DateTime, default=datetime.utcnow)

class Match(Base):
    """Stores individual match results. Each match links to two teams (home/away)."""
    __tablename__ = 'matches'

    id = Column(Integer, primary_key=True, index=True)
    api_id = Column(Integer, unique=True, nullable=False)

    # Teams
    home_team_id = Column(Integer, ForeignKey('teams.id'), nullable=False, index=True)
    away_team_id = Column(Integer, ForeignKey('teams.id'), nullable=False, index=True)

    # Match details
    season = Column(Integer, nullable=False)
    matchday = Column(Integer)
    competition = Column(String(100), nullable=False, index=True)
    stage = Column(String(50))  # REGULAR_SEASON, GROUP_STAGE, LEAGUE_STAGE, LAST_16, QUARTER_FINALS, etc.
    date = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False)

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

    # Relationships — link back to teams and forward to features/predictions
    # uselist=False means one-to-one (each match has one feature set and one prediction)
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    features = relationship("MatchFeatures", back_populates="match", uselist=False)
    prediction = relationship("Prediction", back_populates="match", uselist=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Standing(Base):
    """Computed Stanindgs in the league"""
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True, index=True)
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
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MatchFeatures(Base):
    """Computed ML features for each match. Generated by feature_engineering.py."""
    __tablename__ = 'match_features'

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey('matches.id'), unique=True, nullable=False)

    # Form features — average points per game over last 5 matches
    home_form_5 = Column(Float)
    away_form_5 = Column(Float)

    # Goal averages — rolling mean of goals scored/conceded over recent matches
    home_goals_scored_avg = Column(Float)
    home_goals_conceded_avg = Column(Float)
    away_goals_scored_avg = Column(Float)
    away_goals_conceded_avg = Column(Float)

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

    created_at = Column(DateTime, default=datetime.utcnow)

class Prediction(Base):
    """Stores ML model predictions. Evaluated after match is played (correct column)."""
    __tablename__ = 'predictions'

    id = Column(Integer, primary_key=True, index=True)
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

    created_at = Column(DateTime, default=datetime.utcnow)

# ============================================================
# Database utility functions
# ============================================================

def init_db():
    """Create all tables in the database based on the ORM models above."""
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")

def get_db():
    """Yield a database session, ensuring it's closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

    def add_team(self, api_id, name, short_name, competition):
        """Add a new team or update existing one (matched by api_id)."""
        team = self.session.query(Team).filter_by(api_id=api_id).first()

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

        self.session.commit()
        return team
    
    def add_match(self, match_data):
        """Add a new match or update scores/status if it already exists."""
        match = self.session.query(Match).filter_by(api_id=match_data['api_id']).first()

        # Get or create teams
        home_team = self.session.query(Team).filter_by(api_id=match_data['home_team_api_id']).first()
        away_team = self.session.query(Team).filter_by(api_id=match_data['away_team_api_id']).first()

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

        self.session.commit()
        return match

    def get_team_matches(self, team_id, limit=None):
        """Get all matches where team played (home or away), ordered by date descending."""
        query = self.session.query(Match).filter(
            (Match.home_team_id == team_id) | (Match.away_team_id == team_id)
        ).order_by(Match.date.desc())

        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    def get_all_matches(self):
        """Get all matches"""
        return self.session.query(Match).order_by(Match.date.desc()).all()
    
    def add_standing(self, team_id, season, competition, data):
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
        # Use start of today (not current time) so we don't miss matches earlier today
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = today_start + timedelta(days=days)
        return self.session.query(Match).filter(
            Match.status.in_(['SCHEDULED', 'TIMED']),
            Match.date >= today_start,
            Match.date <= end,
        ).order_by(Match.date.asc()).all()
    
# Main execution
if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Database ready!")

