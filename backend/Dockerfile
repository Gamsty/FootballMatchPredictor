# ===== Builder =====
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --only-binary=:all: fails fast if a wheel is missing instead of compiling from source
# (which can take 15+ minutes for scikit-learn/xgboost and OOM Docker on Windows).
RUN pip install --user --no-cache-dir --only-binary=:all: -r requirements.txt

# ===== Runtime =====
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

COPY --from=builder /root/.local /home/app/.local

COPY --chown=app:app src/ ./src/
COPY --chown=app:app wsgi.py gunicorn.conf.py ./
COPY --chown=app:app models/ ./models/
# Include jobs/ for Container Apps Job entrypoints (retrain, backfill_predictions,
# snapshot_odds). Gunicorn itself doesn't touch this directory — only the
# job-mode containers override CMD to "python jobs/<name>.py". Same image
# serves both purposes.
COPY --chown=app:app jobs/ ./jobs/

# Writable runtime data directory for the file-backed odds cache
# (backend/data/odds_cache.json). Without the chown, /app stays root-owned
# and the unprivileged `app` user can't create the directory at runtime —
# _save_cache silently fails with PermissionError and the cache stays in-memory
# only, undermining the cross-restart persistence the 7-day TTL relies on.
RUN mkdir -p /app/data && chown app:app /app/data

USER app
ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
