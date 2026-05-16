import sys
sys.path.insert(0, 'src')
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    r = c.execute(text("""
        SELECT m.id, m.date, m.status, m.home_score, m.away_score, m.winner,
               ht.name as home, at.name as away
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE (ht.name LIKE '%Aston Villa%' OR at.name LIKE '%Aston Villa%')
          AND m.date >= NOW() - INTERVAL '3 days'
        ORDER BY m.date DESC
    """)).fetchall()
    for row in r:
        print(row)
