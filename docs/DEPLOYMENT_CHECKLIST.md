# Deployment Checklist

## Pre-Deployment

- [ ] Backend runs locally (`python src/app.py`)
- [ ] Frontend builds without errors (`npm run build`)
- [ ] `.env.example` is up to date
- [ ] Production models committed (`best_model.pkl`, `multi_market_models.pkl`)
- [ ] `wsgi.py` entry point works with Gunicorn
- [ ] CORS configured for production frontend URL
- [ ] `.gitignore` excludes `.env`, `*.dump`, `venv/`, `__pycache__/`

## Backend Deployment (Render)

### Database Setup
1. Create PostgreSQL database on Render (free tier)
2. Copy the **External Database URL** for local access
3. Run data loading scripts against the remote DB:
   ```bash
   # Set the remote database URL
   export DATABASE_URL="postgresql://user:pass@host/dbname"

   python src/load_data.py
   python src/load_external_csv.py
   ```
4. Run feature engineering locally, then bulk-transfer:
   ```bash
   # Run feature engineering against local DB (much faster)
   python src/feature_engineering.py

   # Export features from local DB
   pg_dump -U postgres -t match_features --data-only football_predictor > features_dump.sql

   # Import to Render
   psql "EXTERNAL_DATABASE_URL" < features_dump.sql
   ```
5. Verify data: `psql "EXTERNAL_URL"` then `SELECT COUNT(*) FROM matches;`

### Web Service Setup
- [ ] Web service created, connected to GitHub repo
- [ ] Root directory: `backend`
- [ ] Build command: `pip install -r requirements.txt`
- [ ] Start command: `gunicorn wsgi:app`
- [ ] Environment variables:
  - `DATABASE_URL` — Internal database URL from Render
  - `FLASK_ENV=production`
  - `PYTHON_VERSION=3.11.0`
  - `FOOTBALL_API_KEY` — from football-data.org
  - `FRONTEND_URL` — Vercel deployment URL (add after frontend deploy)

### Verification
- [ ] Build successful (check Render logs)
- [ ] Health check: `GET /api/health` returns model status
- [ ] Teams endpoint: `GET /api/teams` returns team list
- [ ] Predictions: `GET /api/predictions/upcoming` returns match predictions

## Frontend Deployment (Vercel)

### Setup
- [ ] Import GitHub repo in Vercel
- [ ] Root directory: `frontend`
- [ ] Framework preset: Vite
- [ ] Environment variable: `VITE_API_URL=https://your-app.onrender.com/api`

### Verification
- [ ] Site loads at Vercel URL
- [ ] Dashboard shows match predictions (no CORS errors)
- [ ] Match detail modal opens with all betting markets
- [ ] Accumulator calculates combined odds
- [ ] No console errors in browser DevTools

## Post-Deployment

- [ ] Update `FRONTEND_URL` on Render with actual Vercel URL
- [ ] Verify CORS works (frontend can call backend)
- [ ] APScheduler running (check logs for "Daily fixture refresh scheduled for 06:00 UTC")
- [ ] Mobile responsiveness verified
- [ ] End-to-end test: open dashboard, click a match, check all markets load

## Daily Operations

Fixtures are refreshed automatically at 06:00 UTC via APScheduler. You can also:
- Click "Refresh Fixtures" on the dashboard for a manual refresh
- Call `POST /api/fixtures/refresh` directly

## Updating Production Data

To add new historical data or retrain models:

```bash
# Option 1: Run against remote DB directly
export DATABASE_URL="postgresql://..."
python src/load_data.py
python src/load_external_csv.py

# Option 2: Bulk transfer via pg_dump (faster for feature engineering)
# Run locally, then pg_dump/psql to transfer
```

## Common Issues

| Issue | Solution |
|-------|----------|
| Render cold start (30-60s) | Free tier spins down after inactivity — first request wakes it |
| Feature engineering slow on remote DB | Use pg_dump/pg_restore approach (see Database Setup above) |
