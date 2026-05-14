import sys
sys.path.insert(0, 'src')
from datetime import datetime
from database import DatabaseManager, Match, MatchFeatures
db = DatabaseManager()

print('5 most recent FINISHED matches WITHOUT features:')
recent_no_feat = db.session.query(Match).outerjoin(
    MatchFeatures, MatchFeatures.match_id == Match.id
).filter(
    Match.status == 'FINISHED',
    MatchFeatures.match_id.is_(None)
).order_by(Match.date.desc()).limit(5).all()
for m in recent_no_feat:
    print(f"  id={m.id}  {m.date.isoformat()}  {m.competition}  winner={m.winner}  scores={m.home_score}-{m.away_score}")

if recent_no_feat:
    from feature_engineering import FeatureEngineer
    fe = FeatureEngineer()
    test_match = recent_no_feat[0]
    print()
    print(f"Trying to build features for match {test_match.id}...")
    try:
        features = fe.create_match_features(test_match)
        print(f"  Success! {len(features)} features computed")
        sample_keys = ['home_form_5', 'home_xg_for_avg', 'home_league_position']
        for k in sample_keys:
            print(f"    {k} = {features.get(k)}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
