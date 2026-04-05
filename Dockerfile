FROM python:3.11-slim

# Install system dependencies required for psycopg2 (libpq-dev) and compilation (gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
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

# Set PYTHONPATH so agentfx package is importable from src/
ENV PYTHONPATH=src

EXPOSE 8000

# Run migrations then start the application server.
# Using 'sh -c' keeps the entrypoint a single layer with no extra script file.
# --no-reload is intentional: reload is a development-only convenience that
# causes unnecessary worker restarts in production.
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_instruments.py && uvicorn agentfx.api.main:app --host 0.0.0.0 --port 8000"]
