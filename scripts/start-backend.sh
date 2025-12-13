#!/bin/bash

# AgentFX Backend Server Startup Script
# Usage: ./start-server.sh [environment]
# Environments: development, test, staging, production
#
# Configuration is loaded from .env.{environment} files automatically.
# This script only sets the ENVIRONMENT variable - all other config
# comes from the environment files.

set -e

# Use ENVIRONMENT env var, then positional arg, then default to development
ENVIRONMENT=${ENVIRONMENT:-${1:-development}}

echo "========================================"
echo "AgentFX Backend Server"
echo "========================================"

# Validate environment
case $ENVIRONMENT in
    development|dev)
        ENVIRONMENT=development
        DOCKER_COMPOSE_FILE=docker-compose.development.yml
        ;;
    test|testing)
        ENVIRONMENT=test
        DOCKER_COMPOSE_FILE=docker-compose.test.yml
        ;;
    staging|stage)
        ENVIRONMENT=staging
        DOCKER_COMPOSE_FILE=docker-compose.staging.yml
        ;;
    production|prod)
        ENVIRONMENT=production
        DOCKER_COMPOSE_FILE=docker-compose.production.yml
        ;;
    *)
        echo "Error: Invalid environment: $ENVIRONMENT"
        echo "Valid environments: development, test, staging, production"
        exit 1
        ;;
esac

echo "Environment: $ENVIRONMENT"
echo "Config file: .env.$ENVIRONMENT"
echo "Docker Compose: $DOCKER_COMPOSE_FILE"
echo "========================================"

# Set environment variable - config.py will load .env.{ENVIRONMENT}
export ENVIRONMENT=$ENVIRONMENT
export PYTHONPATH=src

# Source the environment-specific .env file to set all environment variables
# This ensures environment variables override any existing values
ENV_FILE=".env.$ENVIRONMENT"
if [ -f "$ENV_FILE" ]; then
    echo "Sourcing environment file: $ENV_FILE"
    set -a  # Auto-export all variables
    source "$ENV_FILE"
    set +a
else
    echo "Warning: Environment file not found: $ENV_FILE"
fi

# Detect virtual environment directory
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
elif [ -d "venv" ]; then
    VENV_DIR="venv"
else
    echo "Error: Virtual environment not found."
    echo "Please run 'make dev' first."
    exit 1
fi

echo "Using virtual environment: $VENV_DIR"

# Activate virtual environment
source $VENV_DIR/bin/activate

# Check if dependencies are installed
if ! python -c "import fastapi" 2>/dev/null; then
    echo "Error: Dependencies not installed."
    echo "Please run 'make dev' first."
    exit 1
fi

# Check if database is already accessible (local PostgreSQL)
echo "Checking database connectivity..."
DB_READY=false

# Determine port and credentials based on environment
# Note: Local PostgreSQL typically runs on default port 5432
if [ "$ENVIRONMENT" = "test" ] || [ "$ENVIRONMENT" = "testing" ]; then
    DB_PORT=5432
    DB_USER=test_user
    DB_NAME=agentfx_test
    DB_PASSWORD=test_password
else
    DB_PORT=5432
    DB_USER=agentfx
    DB_NAME=agentfx_dev
    DB_PASSWORD=agentfx
fi

# Try connecting via psql if available
if command -v psql > /dev/null 2>&1 || [ -x "/opt/homebrew/opt/postgresql@15/bin/psql" ]; then
    PSQL_BIN="${PSQL_BIN:-$(command -v psql 2>/dev/null || echo '/opt/homebrew/opt/postgresql@15/bin/psql')}"
    if PGPASSWORD=test_password $PSQL_BIN -h localhost -p $DB_PORT -U $DB_USER -d $DB_NAME -c "SELECT 1" > /dev/null 2>&1; then
        echo "Local PostgreSQL is ready on port $DB_PORT!"
        DB_READY=true
    fi
fi

# Fall back to Docker if local PostgreSQL not available
if [ "$DB_READY" != "true" ]; then
    echo "Local PostgreSQL not accessible, trying Docker..."
    echo "Starting database container..."
    if docker-compose -f $DOCKER_COMPOSE_FILE up -d 2>/dev/null; then
        echo "Database container started"

        # Wait for Docker database to be ready
        echo "Waiting for database..."
        max_attempts=30
        attempt=0
        while [ $attempt -lt $max_attempts ]; do
            if docker-compose -f $DOCKER_COMPOSE_FILE exec -T $(docker-compose -f $DOCKER_COMPOSE_FILE config --services 2>/dev/null | head -1) pg_isready > /dev/null 2>&1; then
                echo "Database is ready!"
                DB_READY=true
                break
            fi
            attempt=$((attempt + 1))
            sleep 1
        done
    else
        echo "Warning: Could not start database container"
    fi
fi

if [ "$DB_READY" != "true" ]; then
    echo "Warning: Could not verify database readiness"
    echo "Continuing anyway - database configuration from .env.$ENVIRONMENT will be used"
fi

# Apply database migrations
echo "Applying database migrations..."
if alembic upgrade head; then
    echo "Migrations applied successfully"
else
    echo "Warning: Migration command returned non-zero"
fi

# Seed instrument metadata (for development and test environments)
# NOTE: This only seeds instrument metadata (symbols, pip locations, etc.)
# Candle data is handled by the ingestion worker when the server starts.
if [ "$ENVIRONMENT" = "development" ] || [ "$ENVIRONMENT" = "test" ]; then
    echo ""
    echo "Seeding instrument metadata..."
    if PYTHONPATH=src $VENV_DIR/bin/python scripts/seed_instruments.py; then
        echo "Instrument metadata seeding complete"
    else
        echo "Warning: Instrument seeding returned non-zero"
    fi
fi

# Start the server
echo ""
echo "========================================"
echo "Starting FastAPI server..."
echo "API: http://localhost:8000"
echo "Docs: http://localhost:8000/docs"
echo "Health: http://localhost:8000/health"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Start uvicorn
# The Settings class in config.py will automatically load .env.{ENVIRONMENT}
exec $VENV_DIR/bin/uvicorn agentfx.api.main:app --host 0.0.0.0 --port 8000 --reload
