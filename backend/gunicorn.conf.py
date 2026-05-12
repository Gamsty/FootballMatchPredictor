"""
Gunicorn configuration for production
"""

import os

# Bind to PORT env var (Azure Container Apps sets it via targetPort), default 5000
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# Worker count: respect the standard WEB_CONCURRENCY env var (set by the
# Container App env). Default to 2 — each worker loads ~500MB of model
# artifacts, so cpu_count*2+1 OOMs anything smaller than ~16GB of RAM.
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
worker_class = "sync"
worker_connections = 1000

# Restart workers after this many requests to avoid memory creep
max_requests = 1000
max_requests_jitter = 50

# Timeout
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "football-predictor-api"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None