import sys
sys.path.insert(0, 'src')
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as c:
    # Latest match WITH features (etter full JOIN)
    latest = c.execute(text("""
        SELECT MAX(m.date) FROM match_features mf
        JOIN matches m ON mf.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
    """)).scalar()
    print(f"Latest match WITH features: {latest}")
    
    # Hvor mange i siste 365d
    n365 = c.execute(text("""
        SELECT COUNT(*) FROM match_features mf
        JOIN matches m ON mf.match_id = m.id
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.date >= NOW() - INTERVAL '365 days'
    """)).scalar()
    print(f"Last 365d after JOIN:       {n365}")
    
    # Total finished matches per status (kanskje fixture-refresh gjør dem ikke FINISHED?)
    statuses = c.execute(text("SELECT status, COUNT(*) FROM matches GROUP BY status ORDER BY 2 DESC")).fetchall()
    print()
    print("Match counts by status:")
    for s, n in statuses:
        print(f"  {s:>15}: {n}")
