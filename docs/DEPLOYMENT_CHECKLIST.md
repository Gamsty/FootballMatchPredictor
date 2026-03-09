# 🚀 Deployment Checklist

## Pre-Deployment

- [ ] All API endpoints tested locally
- [ ] requirements.txt contains only runtime dependencies (clean UTF-8)
- [ ] Environment variables documented in .env.example
- [ ] XGBoost model file committed to repository (backend/models/xgboost_model.pkl)
- [ ] wsgi.py entry point working with gunicorn
- [ ] CORS configured for production frontend URL
- [ ] .gitignore excludes .env, backup.dump, venv, __pycache__

## Backend Deployment (Render)

### Database Setup
- [ ] PostgreSQL database created on Render
- [ ] External Database URL copied for local access
- [ ] Local database dumped: `pg_dump -Fc -U postgres --file=backup.dump football_predictor`
- [ ] Data restored to production: `pg_restore --no-owner --no-privileges -d "EXTERNAL_URL" backup.dump`
- [ ] Data verified: `psql "EXTERNAL_URL"` → `SELECT COUNT(*) FROM matches;`

### Web Service Setup
- [ ] Web service created and connected to GitHub
- [ ] Root directory set to `backend`
- [ ] Build command: `pip install -r requirements.txt`
- [ ] Start command: `gunicorn wsgi:app`
- [ ] Environment variables configured:
  - [ ] DATABASE_URL (from Render database, internal URL)
  - [ ] FLASK_ENV=production
  - [ ] FLASK_DEBUG=False
  - [ ] PYTHON_VERSION=3.11.0
  - [ ] FOOTBALL_API_KEY=your_api_key
  - [ ] FRONTEND_URL (add after frontend deploy)

### Verification
- [ ] Build successful (no import errors)
- [ ] Model loaded successfully (check logs for "Model loaded successfully")
- [ ] Health endpoint: `https://footballmatchpredictor.onrender.com/api/health`
- [ ] Teams endpoint: `https://footballmatchpredictor.onrender.com/api/teams`
- [ ] Statistics endpoint: `https://footballmatchpredictor.onrender.com/api/statistics/overview`

## Frontend Deployment (Vercel)

### Setup
- [ ] Production build tested locally: `npm run build && npm run preview`
- [ ] Vercel project created
- [ ] Repository connected to Vercel
- [ ] Root directory set to `frontend`
- [ ] Framework preset: Vite
- [ ] Environment variables configured:
  - [ ] VITE_API_URL=https://footballmatchpredictor.onrender.com/api

### Verification
- [ ] Build successful on Vercel
- [ ] Site accessible at production URL
- [ ] Teams load in dropdown selectors (no CORS errors)
- [ ] Predictions return results with probabilities
- [ ] Statistics page loads with charts and metrics
- [ ] No console errors in browser DevTools
- [ ] Toast notifications appear for errors

## Post-Deployment

- [ ] Backend CORS updated with Vercel frontend URL (in app.py and Render env vars)
- [ ] End-to-end prediction test completed
- [ ] Mobile responsiveness verified
- [ ] README.md updated with live demo URLs
- [ ] Error boundary displays fallback on crash

## Security

- [ ] No API keys or passwords in frontend code
- [ ] .env file not committed to repository
- [ ] Environment variables set in Render/Vercel dashboards
- [ ] CORS restricted to specific frontend origins
- [ ] SQL injection prevented (using SQLAlchemy ORM)
- [ ] Database credentials not exposed in logs

## Updating Production Data

To load new match data into production:

```bash
# From backend/ directory
set DATABASE_URL=postgresql://user:pass@host/dbname
python src/load_data.py
python src/feature_engineering.py
```

Or dump and restore again:

```bash
pg_dump -Fc -U postgres --file=backup.dump football_predictor
pg_restore --no-owner --no-privileges -d "EXTERNAL_URL" backup.dump
```

## Common Issues

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: No module named 'database'` | wsgi.py needs `sys.path.insert` for src/ directory |
| `No such file: '../models/xgboost_model.pkl'` | Use `os.path.dirname(__file__)` for absolute model path |
| `pg_restore: input file does not appear to be a valid archive` | Use `pg_dump --file=` flag instead of `>` redirect (PowerShell issue) |
| CORS error in browser | Add frontend URL to CORS origins in app.py + Render env vars |
| Gunicorn `ModuleNotFoundError: fcntl` | Gunicorn doesn't work on Windows — use `python app.py` locally |

---

✅ All checks complete = Ready for production!
