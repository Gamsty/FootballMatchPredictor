# ⚽ Football Match Predictor

A full-stack machine learning application that predicts football match outcomes using historical data and XGBoost classification.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![React](https://img.shields.io/badge/React-19-blue)
![ML](https://img.shields.io/badge/ML-XGBoost-orange)
![Deployment](https://img.shields.io/badge/Deployed-Vercel%20%2B%20Render-success)

## 🌟 Features

- **Match Prediction**: Predict outcomes (Home Win, Draw, Away Win) with probability scores
- **ML Model**: XGBoost classifier trained on Premier League data
- **Feature Engineering**: 13 statistical features including form, goals, and head-to-head records
- **Statistics Dashboard**: Comprehensive analytics with model performance metrics
- **Real-time API**: RESTful API serving predictions and historical data
- **Dark Theme UI**: Modern React interface with Tailwind CSS

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Frontend (React + Vite)                  │
│         Deployed on Vercel                               │
│  - Match Prediction Interface                            │
│  - Statistics Dashboard                                  │
│  - Toast Notification System                             │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS/REST API
┌──────────────────────┴──────────────────────────────────┐
│              Backend (Flask + Gunicorn)                  │
│         Deployed on Render                               │
│  - ML Model Serving (XGBoost)                           │
│  - Feature Engineering Pipeline                          │
│  - RESTful API Endpoints                                 │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│            Database (PostgreSQL)                        │
│         Hosted on Render                                 │
│  - Teams, Matches, Features, Predictions                │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Live Demo

- **Frontend**: [https://football-match-predictor-pearl.vercel.app](https://football-match-predictor-pearl.vercel.app)
- **API**: [https://footballmatchpredictor.onrender.com](https://footballmatchpredictor.onrender.com)

## 📊 Model Performance

- **Algorithm**: XGBoost Classifier
- **Training Data**: Premier League matches (2021-2024 seasons)
- **Compared Models**: XGBoost, Random Forest, Gradient Boosting, Logistic Regression
- **Best Model**: XGBoost (selected for best performance)

### Features Used

| Feature | Description |
|---------|-------------|
| Home/Away Form | Win rate over last 5 matches |
| Goals Scored | Average goals scored per match |
| Goals Conceded | Average goals conceded per match |
| Home/Away Win Rate | Historical win percentage at home/away |
| Head-to-Head | Historical record between the two teams |
| Days Since Last Match | Rest days before the match |

## 🛠️ Tech Stack

### Backend
- Python 3.11
- Flask 3.1 (API framework)
- SQLAlchemy (ORM)
- PostgreSQL (Database)
- Scikit-learn (ML preprocessing)
- XGBoost (Classification)
- Pandas & NumPy (Data processing)
- Gunicorn (Production server)

### Frontend
- React 19
- Vite 6 (Build tool)
- Tailwind CSS 4 (Styling)
- React Router 7 (Navigation)
- Axios (HTTP client)

### Deployment
- Vercel (Frontend hosting)
- Render (Backend + PostgreSQL database)

## 📦 Installation

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+

### Backend Setup

```bash
# Clone repository
git clone https://github.com/Gamsty/FootballMatchPredictor.git
cd FootballMatchPredictor/backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration:
#   FOOTBALL_API_KEY=your_api_key
#   DATABASE_URL=postgresql://user:password@localhost:5432/football_predictor

# Initialize database
python src/database.py

# Load historical data
python src/load_data.py

# Create features
python src/feature_engineering.py

# Train model (optional - pre-trained model included)
python src/model_training.py

# Run development server
python src/app.py
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

The app will be available at `http://localhost:5173` (frontend) and `http://localhost:5000` (API).

## 🔌 API Endpoints

### Health Check
- `GET /api/health` - API status and model info

### Teams
- `GET /api/teams` - Get all teams
- `GET /api/teams/:id` - Get team details with statistics and recent form

### Predictions
- `POST /api/predict` - Predict match outcome
```json
{
  "home_team_id": 1,
  "away_team_id": 2
}
```

### Matches
- `GET /api/matches` - Get matches (filterable by season, team, status)
- `GET /api/matches/:id` - Get match details with features and prediction

### Statistics
- `GET /api/statistics/overview` - Overall match and goal statistics
- `GET /api/statistics/head-to-head?home_team_id=1&away_team_id=2` - Head-to-head record

### Prediction History
- `GET /api/predictions/history` - Past predictions with accuracy stats

## 📈 Development Process

### Phase 1: Data Collection
- Integrated Football-Data.org API
- Collected Premier League match data across multiple seasons

### Phase 2: Exploratory Data Analysis
- Analyzed match outcomes, goal distributions, and team performance
- Jupyter notebooks for visualization and insights

### Phase 3: Feature Engineering
- Created 13 statistical features per match
- Automated pipeline for computing features on new matches

### Phase 4: Model Training
- Compared 4 algorithms (XGBoost, Random Forest, Gradient Boosting, Logistic Regression)
- XGBoost selected as best performer
- Cross-validation for robustness

### Phase 5: API Development
- Built RESTful Flask API
- ML model serving with real-time predictions
- SQLAlchemy ORM for database operations

### Phase 6: Frontend Development
- React SPA with dark theme
- Toast notification system for user feedback
- Error boundary for crash protection

### Phase 7: Integration & Testing
- Connected frontend to backend API
- End-to-end testing of prediction flow

### Phase 8: Deployment
- Backend deployed to Render with Gunicorn
- Frontend deployed to Vercel
- PostgreSQL database hosted on Render

## 📁 Project Structure

```
FootballMatchPredictor/
├── backend/
│   ├── data/
│   │   ├── raw/                    # Raw API data
│   │   └── processed/             # Cleaned CSV data
│   ├── models/                    # Trained ML models (.pkl)
│   ├── notebooks/                 # Jupyter notebooks (EDA, analysis)
│   │   ├── 01_api_exploration.ipynb
│   │   ├── 02_exploratory_analysis.ipynb
│   │   ├── 03_feature_analysis.ipynb
│   │   └── 04_model_analysis.ipynb
│   ├── src/
│   │   ├── app.py                 # Flask API application
│   │   ├── database.py            # SQLAlchemy models & DB manager
│   │   ├── data_collection.py     # Football API data fetching
│   │   ├── feature_engineering.py # Feature computation pipeline
│   │   ├── load_data.py           # CSV to database loader
│   │   └── model_training.py      # ML model training & evaluation
│   ├── .env                       # Environment variables (not committed)
│   ├── gunicorn.conf.py           # Gunicorn production config
│   ├── render.yaml                # Render deployment config
│   ├── requirements.txt           # Python dependencies
│   └── wsgi.py                    # WSGI entry point
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ErrorBoundary.jsx  # React error boundary
│   │   │   ├── FeatureDisplay.jsx # Match feature visualization
│   │   │   ├── Predict.jsx        # Prediction form & results
│   │   │   ├── PredictionResult.jsx # Prediction outcome display
│   │   │   ├── TeamSelector.jsx   # Team dropdown selectors
│   │   │   └── ToastContainer.jsx # Toast notification renderer
│   │   ├── contexts/
│   │   │   └── ToastContext.jsx   # Toast notification context
│   │   ├── pages/
│   │   │   └── Statistics.jsx     # Statistics dashboard page
│   │   ├── services/
│   │   │   └── api.js             # Axios API client
│   │   ├── App.jsx                # Root component with routing
│   │   ├── main.jsx               # React entry point
│   │   └── index.css              # Global styles & Tailwind
│   ├── .env.production            # Production API URL
│   ├── vercel.json                # Vercel deployment config
│   └── package.json               # Node dependencies
├── .gitignore
└── README.md
```

## 🔄 Updating Match Data

To update with new match results:

```bash
cd backend/src

# Fetch and load new matches
python load_data.py

# Recompute features
python feature_engineering.py
```

For production database, set the `DATABASE_URL` environment variable to your production connection string before running.

## 👨‍💻 Author

**Adrian**
- GitHub: [@Gamsty](https://github.com/Gamsty)

## 🙏 Acknowledgments

- [Football-Data.org](https://www.football-data.org/) for providing the football API
- Premier League for the match data

---

⚽ Built with passion for football and machine learning
