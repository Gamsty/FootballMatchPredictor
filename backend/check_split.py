import sys
sys.path.insert(0, 'src')
import os
import tempfile
import pandas as pd

# Eksporter CSV fra prod-DB med samme query som retrain bruker
from feature_engineering import FeatureEngineer
fe = FeatureEngineer()

with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tmp:
    tmp_path = tmp.name

print(f"Exporting to {tmp_path}...")
fe.export_features_to_csv(output_path=tmp_path)

# Les CSV
df = pd.read_csv(tmp_path)
print(f"CSV rows: {len(df)}")
print(f"Columns include 'date'? {'date' in df.columns}")

if 'date' in df.columns:
    print(f"\nDate column dtype: {df['date'].dtype}")
    print(f"Sample dates:")
    print(df['date'].head(3).tolist())
    print(df['date'].tail(3).tolist())
    
    # Apply same parsing as retrain
    df['date_parsed'] = pd.to_datetime(df['date'], utc=True, errors='coerce')
    print(f"\nValid dates: {df['date_parsed'].notna().sum()}")
    print(f"NaT dates:   {df['date_parsed'].isna().sum()}")
    
    cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=365)
    print(f"\nCutoff: {cutoff}")
    print(f"Date < cutoff: {(df['date_parsed'] < cutoff).sum()}")
    print(f"Date >= cutoff: {(df['date_parsed'] >= cutoff).sum()}")
    print(f"Latest date in CSV: {df['date_parsed'].max()}")

os.unlink(tmp_path)
