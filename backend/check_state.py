import sys
sys.path.insert(0, 'src')
from database import DatabaseManager, Match, MatchFeatures
db = DatabaseManager()
n_matches = db.session.query(Match).filter(Match.status == 'FINISHED').count()
n_features = db.session.query(MatchFeatures).count()
print(f"Finished matches: {n_matches}")
print(f"MatchFeatures:    {n_features}")
print(f"Diff:             {n_matches - n_features}")
