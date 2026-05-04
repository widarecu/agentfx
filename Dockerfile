FROM python:3.11-slim

# Install system dependencies required for psycopg2 (libpq-dev) and compilation (gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies first to leverage Docker layer caching
COPY requirements.txt requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-api.txt

# Copy application source and migration assets
COPY src/ ./src/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY scripts/seed_instruments.py ./scripts/seed_instruments.py
COPY config/ ./config/

# Set PYTHONPATH so agentfx package is importable from src/
ENV PYTHONPATH=src

EXPOSE 8000

# Entrypoint sequence on every container start:
#   1. alembic upgrade head      — apply any pending migrations
#   2. seed_instruments.py        — idempotent metadata seed AND refresh of
#                                   OANDA financing rates (uses the static
#                                   fallback in config/financing_fallback.yaml
#                                   for pairs OANDA returns "0" for, e.g.
#                                   GBP/JPY pairs on practice accounts).
#                                   Failures are logged but do NOT block the
#                                   deploy, so an OANDA outage can't prevent
#                                   the service from coming up.
#   3. uvicorn (no --reload)      — production server
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_instruments.py && uvicorn agentfx.api.main:app --host 0.0.0.0 --port 8000"]
