import sys
sys.path.insert(0, 'src')
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'])
with e.begin() as c:
    r = c.execute(text("""
        UPDATE matches 
        SET status = 'FINISHED' 
        WHERE id = 362 AND status = 'IN_PLAY'
        RETURNING id, status, home_score, away_score, winner
    """)).fetchone()
    print(f"Updated: {r}")
