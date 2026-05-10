# Retraining job container — built from the project root.
# Build: docker build -t fotballpred-retrain:dev -f backend/jobs/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app backend/src/ ./src/
COPY --chown=app:app backend/jobs/ ./jobs/

USER app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

CMD ["python", "jobs/retrain.py"]
