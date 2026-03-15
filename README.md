# Football Match Predictor

A full-stack machine learning application that predicts football match outcomes across multiple betting markets. Uses XGBoost classification trained on 9 European leagues with 40,000+ historical matches.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![React](https://img.shields.io/badge/React-19-blue)
![ML](https://img.shields.io/badge/ML-XGBoost-orange)
![Deployment](https://img.shields.io/badge/Deployed-Vercel%20%2B%20Render-success)

## Live Demo

- **Frontend**: [football-match-predictor-pearl.vercel.app](https://football-match-predictor-pearl.vercel.app)
- **API**: [footballmatchpredictor.onrender.com](https://footballmatchpredictor.onrender.com)

> Note: The Render free tier spins down after inactivity — first load may take 30-60 seconds, maybe even longer.

## Features

- **Multi-market predictions** — Match result (H/D/A), Double Chance, BTTS, Over/Under 2.5, Half-Time result, Corners, Cards
- **Combo bets** — Result+BTTS, Result+O/U, BTTS+O/U combinations with combined probabilities
- **Smart bet recommendations** — "Best Bet" (highest edge) and "Safest Bet" (highest probability) with reasoning
- **Accumulator builder** — Select bets across matches, calculates combined odds and potential returns
- **Match tagging** — High Confidence, Upset Pick, Banker classifications
- **Auto-refresh** — Daily fixture updates via APScheduler (06:00 UTC)
- **9 leagues** — Premier League, Championship, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Primeira Liga, Champions League

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Frontend (React 19 + Vite 6)               │
│                   Deployed on Vercel                    │
│  - Dashboard with match cards grouped by league         │
│  - Match detail modal with all betting markets          │
│  - Accumulator builder with live odds calculation       │
│  - Filter by confidence, upset picks, bankers           │
└──────────────────────┬──────────────────────────────────┘
                       │ REST API (Axios)
┌──────────────────────┴──────────────────────────────────┐
│            Backend (Flask + Gunicorn)                    │
│                  Deployed on Render                      │
│  - XGBoost model serving (match result + 10 markets)    │
│  - Feature engineering pipeline (13 features)           │
│  - Prediction caching (LRU, 6-hour TTL)                │
│  - APScheduler for daily fixture refresh                │
└──────────────────────┬──────────────────────────────────┘
                       │ SQLAlchemy ORM
┌──────────────────────┴──────────────────────────────────┐
│               PostgreSQL Database                       │
│                  Hosted on Render                        │
│  - 40,000+ matches across 9 leagues                     │
│  - Teams, Features, Standings, Predictions              │
└─────────────────────────────────────────────────────────┘
```

## Prediction Markets

| Market | Outcomes | Description |
|--------|----------|-------------|
| Match Result | Home / Draw / Away | Main 1X2 prediction |
| Double Chance | 1X / X2 / 12 | Combined outcome probabilities |
| BTTS | Yes / No | Both teams to score |
| Over/Under 2.5 | Over / Under | Total goals threshold |
| Half-Time Result | Home / Draw / Away | First half prediction |
| Corners O/U | Over / Under 9.5 | Total corners prediction |
| Cards O/U | Over / Under 3.5 | Total cards prediction |
| Combos | 18 combinations | Result+BTTS, Result+O/U, BTTS+O/U |

## Tech Stack

### Backend
- Python 3.11, Flask 3.1, Gunicorn
- SQLAlchemy + PostgreSQL
- XGBoost, Scikit-learn, Pandas, NumPy
- APScheduler (daily fixture refresh)
- football-data.org API (match data)

### Frontend
- React 19, Vite 6
- Tailwind CSS 4
- React Router 7, Axios

### Deployment
- **Frontend**: Vercel (auto-deploy from GitHub)
- **Backend**: Render (Web Service + PostgreSQL)

## Installation

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- API key from [football-data.org](https://www.football-data.org/)

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your database URL and API key

# Initialize database tables
python src/database.py

# Load historical match data from CSVs
python src/load_data.py

# Load external league data (standings, etc.)
python src/load_external_csv.py

# Compute features for all matches
python src/feature_engineering.py

# Train models (optional — pre-trained models included)
python src/model_training.py

# Run development server
python src/app.py
```

### Frontend Setup

```bash
cd frontend

npm install
npm run dev
```

The app will be available at `http://localhost:5173` (frontend) and `http://localhost:5000` (API).

## API Endpoints

### Core
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | API status and model info |
| GET | `/api/teams` | List all teams |
| GET | `/api/teams/:id` | Team details with stats and recent form |
| GET | `/api/competitions` | List of leagues in database |

### Predictions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/predict` | Predict match result (H/D/A) |
| POST | `/api/predict/markets` | Full multi-market prediction |
| GET | `/api/predictions/upcoming` | Dashboard — batch predictions for next 3 days |
| GET | `/api/predictions/history` | Past predictions with accuracy |

### Matches
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/matches` | List matches (filter by season, team, status) |
| GET | `/api/matches/:id` | Match details with features |
| GET | `/api/matches/upcoming` | Raw upcoming matches |
| POST | `/api/fixtures/refresh` | Fetch new fixtures from API |

### Statistics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/statistics/overview` | League-wide match and goal stats |
| GET | `/api/statistics/head-to-head` | H2H record between two teams |

## Features Used by ML Model

| Feature | Description |
|---------|-------------|
| Home/Away Form | Win rate over last 5 matches |
| Goals Scored/Conceded | Average per match |
| Home/Away Win Rate | Historical win percentage at venue |
| Head-to-Head Record | Historical record between teams |
| Days Since Last Match | Rest days before the match |
| Elo Rating | Team strength rating |
| League Standings | Current league position and points |

## Project Structure

```
FootballMatchPredictor/
├── backend/
│   ├── models/                     # Trained ML models (.pkl)
│   │   ├── best_model.pkl          # Main XGBoost match result model
│   │   └── multi_market_models.pkl # BTTS, O/U, corners, cards models
│   ├── src/
│   │   ├── app.py                  # Flask API + APScheduler
│   │   ├── database.py             # SQLAlchemy models & DB manager
│   │   ├── data_collection.py      # football-data.org API client
│   │   ├── feature_engineering.py  # Feature computation pipeline
│   │   ├── prediction_service.py   # Multi-market prediction engine
│   │   ├── cache.py                # LRU prediction cache (6h TTL)
│   │   ├── load_data.py            # CSV → database loader
│   │   ├── load_external_csv.py    # External league data loader
│   │   └── model_training.py       # Model training & evaluation
│   ├── .env.example                # Environment variable template
│   ├── requirements.txt            # Python dependencies
│   ├── wsgi.py                     # WSGI entry point for Gunicorn
│   ├── gunicorn.conf.py            # Gunicorn production config
│   └── render.yaml                 # Render deployment config
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── MatchCard.jsx       # Compact match prediction card
│   │   │   ├── MatchDetail.jsx     # Full match modal (all markets)
│   │   │   ├── CategoryTabs.jsx    # Today / Upcoming tabs
│   │   │   ├── FilterBar.jsx       # Confidence/upset/banker filters
│   │   │   ├── ErrorBoundary.jsx   # React error boundary
│   │   │   └── ToastContainer.jsx  # Toast notifications
│   │   ├── pages/
│   │   │   └── Dashboard.jsx       # Main dashboard page
│   │   ├── contexts/
│   │   │   └── ToastContext.jsx     # Toast notification context
│   │   ├── services/
│   │   │   └── api.js              # Axios API client
│   │   ├── utils/
│   │   │   └── constants.js        # Formatters, bet engine, config
│   │   ├── App.jsx                 # Root component with routing
│   │   └── main.jsx                # React entry point
│   ├── vercel.json                 # Vercel deployment config
│   └── package.json                # Node dependencies
├── docs/
│   └── DEPLOYMENT_CHECKLIST.md     # Step-by-step deployment guide
├── LICENSE                         # MIT License
└── README.md
```

## Deployment

See [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) for a full step-by-step deployment guide.

### Quick overview:
1. **Backend** → Render Web Service (root: `backend`, start: `gunicorn wsgi:app`)
2. **Database** → Render PostgreSQL (load data via `pg_dump`/`pg_restore`)
3. **Frontend** → Vercel (root: `frontend`, framework: Vite)
4. **Fixtures** refresh automatically daily at 06:00 UTC via APScheduler

## Author

**Adrian** — [@Gamsty](https://github.com/Gamsty)

## Acknowledgments

- [Football-Data.org](https://www.football-data.org/) for the football data API
- Data from 9 European leagues (2021–2025 seasons)

---

Built with Python, React, and machine learning.
