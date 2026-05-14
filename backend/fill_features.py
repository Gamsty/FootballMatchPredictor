import sys
sys.path.insert(0, 'src')
from database import DatabaseManager, Match, MatchFeatures
db = DatabaseManager()

n_matches = db.session.query(Match).filter(Match.status == 'FINISHED').count()
n_features = db.session.query(MatchFeatures).count()
diff = n_matches - n_features
print(f"Finished matches: {n_matches:>6}")
print(f"MatchFeatures:    {n_features:>6}")
print(f"Missing features: {diff:>6}")

# Force re-create features for ALL matches without them
print()
print("Building features for all matches WITHOUT MatchFeatures...")
from feature_engineering import FeatureEngineer
fe = FeatureEngineer()
fe._build_match_cache()

missing = db.session.query(Match).outerjoin(
    MatchFeatures, MatchFeatures.match_id == Match.id
).filter(
    Match.status == 'FINISHED',
    MatchFeatures.match_id.is_(None)
).all()

print(f"Found {len(missing)} matches without features")
created = 0
failed = 0
for i, m in enumerate(missing):
    try:
        feats = fe.create_match_features(m)
        feat_kwargs = {k: v for k, v in feats.items() if k != 'target' and hasattr(MatchFeatures, k)}
        db.session.add(MatchFeatures(**feat_kwargs))
        created += 1
        if (i + 1) % 200 == 0:
            db.session.commit()
            print(f"  Committed {i+1}/{len(missing)}...")
    except Exception as e:
        failed += 1
        if failed <= 3:
            print(f"  Failed match {m.id}: {type(e).__name__}: {e}")
db.session.commit()
print(f"Done: created={created}, failed={failed}")
