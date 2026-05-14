import sys
sys.path.insert(0, 'src')
import os, tempfile
import pandas as pd
from feature_engineering import FeatureEngineer
fe = FeatureEngineer()
with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tmp:
    tmp_path = tmp.name
fe.export_features_to_csv(output_path=tmp_path)
df = pd.read_csv(tmp_path)
df['date'] = pd.to_datetime(df['date'], utc=True, errors='coerce', format='mixed')
print(f"Valid dates: {df['date'].notna().sum()}")
print(f"NaT dates:   {df['date'].isna().sum()}")
cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=365)
print(f"Date >= cutoff (holdout): {(df['date'] >= cutoff).sum()}")
print(f"Date < cutoff  (train):   {(df['date'] < cutoff).sum()}")
print(f"Latest date: {df['date'].max()}")
os.unlink(tmp_path)
