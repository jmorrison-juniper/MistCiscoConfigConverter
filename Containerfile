# Containerfile - Compatible with both Podman and Docker
# Build: podman build --format docker -t mist-cisco-converter .
#    or: docker build -t mist-cisco-converter .
# Note: --format docker enables HEALTHCHECK support in Podman

FROM python:3.13-slim

LABEL maintainer="jmorrison"
LABEL description="Mist Cisco Config Converter - Web interface for config migration"

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create non-root user for security
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install dependencies first (cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=appuser:appgroup . .

# Create data, input, and output directories with proper permissions
RUN mkdir -p /app/data /app/input /app/output && chown -R appuser:appgroup /app/data /app/input /app/output

# Switch to non-root user
USER appuser

# Expose Gunicorn port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with Gunicorn using config file
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
