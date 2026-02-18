"""
Gunicorn configuration for production deployment.

Usage: gunicorn -c gunicorn.conf.py app:app
"""

import multiprocessing
import os

# Server socket
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
backlog = 2048

# Worker processes
# Container-friendly: cap at 4 workers regardless of CPU count
workers = min(int(os.environ.get("GUNICORN_WORKERS", 4)), 4)
worker_class = "gevent"
worker_connections = 1000
timeout = 30
keepalive = 2

# Limit request sizes for security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
accesslog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "mist-cisco-converter"

# SSL (optional - uncomment for HTTPS)
# keyfile = "/path/to/key.pem"
# certfile = "/path/to/cert.pem"


def on_starting(server):
    """Called before the master process is initialized."""
    pass


def on_reload(server):
    """Called before reloading the configuration."""
    pass


def worker_int(worker):
    """Called when a worker receives SIGINT or SIGQUIT."""
    pass


def worker_abort(worker):
    """Called when a worker receives SIGABRT."""
    pass
