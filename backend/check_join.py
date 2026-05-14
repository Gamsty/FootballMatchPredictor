import sys
sys.path.insert(0, 'src')
from database import DatabaseManager, Match, MatchFeatures
db = DatabaseManager()

# Eksplisitt expire all + fresh query
db.session.expire_all()
db.session.commit()

# Try with a brand-new connection
from sqlalchemy import create_engine
import os
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    from sqlalchemy import text
    n_match = conn.execute(text("SELECT COUNT(*) FROM matches WHERE status='FINISHED'")).scalar()
    n_feat = conn.execute(text("SELECT COUNT(*) FROM match_features")).scalar()
    n_join = conn.execute(text("""
        SELECT COUNT(*) FROM match_features mf
        JOIN matches m ON mf.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
    """)).scalar()
    print(f"Raw FINISHED matches:        {n_match}")
    print(f"Raw match_features rows:     {n_feat}")
    print(f"Full join (CSV export query): {n_join}")
    
    # Check if recent features survive the join
    n_recent_join = conn.execute(text("""
        SELECT COUNT(*) FROM match_features mf
        JOIN matches m ON mf.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.date >= NOW() - INTERVAL '365 days'
    """)).scalar()
    print(f"Last 365d after JOIN:        {n_recent_join}")
