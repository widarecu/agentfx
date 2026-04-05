#!/bin/bash

# AgentFX E2E Test Runner
# Usage: ./scripts/run-e2e-tests.sh [options]
#
# This script:
# 1. Checks database connectivity (local PostgreSQL or Docker)
# 2. Starts the backend server in test mode
# 3. Starts the frontend server in test mode
# 4. Runs Playwright E2E tests
# 5. Cleans up all servers

set -e

# Parse options
HEADED=""
DEBUG=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --headed)
            HEADED="--headed"
            shift
            ;;
        --debug)
            DEBUG="--debug"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--headed] [--debug]"
            exit 1
            ;;
    esac
done

echo "========================================"
echo "AgentFX E2E Test Runner"
echo "========================================"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo "Done."
}

# Set trap for cleanup
trap cleanup EXIT SIGINT SIGTERM

# Detect virtual environment
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
elif [ -d "venv" ]; then
    VENV_DIR="venv"
else
    echo "Error: Virtual environment not found"
    exit 1
fi

# Set environment
export ENVIRONMENT=test
export PYTHONPATH=src
export INGESTION_ENABLED=false

# Database configuration
DB_USER="test_user"
DB_PASSWORD="test_password"
DB_NAME="agentfx_test"
DB_HOST="localhost"
DB_PORT="5432"  # Default for local PostgreSQL

# Find psql binary (portable across systems)
PSQL_BIN=""
if command -v psql > /dev/null 2>&1; then
    PSQL_BIN="psql"
elif [ -x "/opt/homebrew/opt/postgresql@15/bin/psql" ]; then
    PSQL_BIN="/opt/homebrew/opt/postgresql@15/bin/psql"
elif [ -x "/usr/local/bin/psql" ]; then
    PSQL_BIN="/usr/local/bin/psql"
fi

# Check for local PostgreSQL
if [ -n "$PSQL_BIN" ]; then
    echo "Checking local PostgreSQL (port 5432)..."
    if $PSQL_BIN -U $DB_USER -d $DB_NAME -h $DB_HOST -p 5432 -c "SELECT 1" > /dev/null 2>&1; then
        echo "Local PostgreSQL is ready!"
        DB_PORT="5432"
        DB_READY=true
    else
        echo "Local PostgreSQL not accessible."
        DB_READY=false
    fi
else
    echo "psql not found in PATH."
    DB_READY=false
fi

if [ "$DB_READY" != "true" ]; then
    echo "Error: Local PostgreSQL not available"
    echo "Please start local PostgreSQL: brew services start postgresql@15"
    echo ""
    echo "To set up the test database:"
    echo "  createdb -U postgres agentfx_test"
    echo "  createuser -U postgres test_user"
    echo "  psql -U postgres -c \"ALTER USER test_user WITH PASSWORD 'test_password';\""
    echo "  psql -U postgres -c \"GRANT ALL PRIVILEGES ON DATABASE agentfx_test TO test_user;\""
    exit 1
fi

# Export DATABASE_URL based on detected database
export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "Using database: $DATABASE_URL"

# Apply migrations
echo "Applying migrations..."
source $VENV_DIR/bin/activate
if ! alembic upgrade head 2>&1; then
    echo "Error: Migration failed"
    echo "Check database connectivity and migration files"
    exit 1
fi
echo "Migrations applied successfully"

# Seed instrument metadata (required for market data)
echo "Seeding instrument metadata..."
if ! $VENV_DIR/bin/python scripts/seed_instruments.py 2>&1; then
    echo "Warning: Instrument seeding failed (may already exist)"
fi

# Seed test user data
echo "Seeding test user data..."
if ! $VENV_DIR/bin/python scripts/seed_test_data.py 2>&1; then
    echo "Warning: Test data seeding failed (may already exist)"
fi

# Note: Candle data is handled by the ingestion worker on server startup
# The ingestion worker will automatically backfill missing candle data

# Start backend server
echo "Starting backend server..."
$VENV_DIR/bin/uvicorn agentfx.api.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Wait for backend
echo "Waiting for backend..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "Backend is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo "Error: Backend failed to start"
    exit 1
fi

# Start frontend server
echo "Starting frontend server..."
cd portal/web
npm run dev:test &
FRONTEND_PID=$!
cd "$PROJECT_ROOT"

# Wait for frontend
echo "Waiting for frontend..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s http://localhost:5173 > /dev/null 2>&1; then
        echo "Frontend is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo "Warning: Frontend may not be fully ready"
fi

# Run E2E tests
echo ""
echo "========================================"
echo "Running E2E Tests"
echo "========================================"
echo ""

# Run Playwright from portal/web directory where the config is located
cd portal/web
npx playwright test $HEADED $DEBUG
E2E_EXIT_CODE=$?
cd "$PROJECT_ROOT"

echo ""
echo "========================================"
echo "E2E Tests Complete"
echo "========================================"

# Preserve the exit code from Playwright
exit $E2E_EXIT_CODE
