#!/bin/bash

# AgentFX E2E Test Runner (Docker-only)
# Usage: ./scripts/run-e2e-tests.sh [options]
#
# Orchestrates the docker-compose `test` profile (postgres-test + backend-test +
# frontend-test on ports 5433 / 8001 / 5174), seeds test users, runs Playwright
# against the containerised stack, and tears it down on exit.
#
# Prerequisites: Docker Desktop (or equivalent) running. Nothing else.

set -e

HEADED=""
DEBUG=""
KEEP_STACK=""
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
        --keep)
            KEEP_STACK="1"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--headed] [--debug] [--keep]"
            exit 1
            ;;
    esac
done

echo "========================================"
echo "AgentFX E2E Test Runner (Docker)"
echo "========================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------
# Safety guard: this script wipes its target volume with `down -v` at
# startup for hermetic E2E. The `test` profile is the only safe target
# because its data is meant to be ephemeral. Refuse to run against
# `dev` or `uat` profiles to prevent destroying long-lived backfills
# and user accounts. See CLAUDE.md → "Environment isolation".
#
# History: on 2026-05-27 a `make e2e` invocation against a stack that
# was being used for acceptance testing wiped 6 years of EUR_USD M1
# candles + the user's portal account. This guard exists so that can
# never happen again.
# ---------------------------------------------------------------------
TARGET_PROFILE="${E2E_PROFILE:-test}"
if [ "$TARGET_PROFILE" != "test" ]; then
    echo "Error: run-e2e-tests.sh refuses to run against profile '$TARGET_PROFILE'."
    echo "This script wipes its target volume with 'docker compose down -v'."
    echo "Only the 'test' profile is safe — dev and uat hold long-lived data."
    echo "See CLAUDE.md → 'Environment isolation'."
    exit 1
fi
COMPOSE="docker compose --profile $TARGET_PROFILE"

cleanup() {
    if [ -n "$KEEP_STACK" ]; then
        echo ""
        echo "--keep specified — leaving Docker stack up."
        echo "To stop manually: docker compose --profile test down"
        return
    fi
    echo ""
    echo "Stopping Docker test stack..."
    $COMPOSE down --remove-orphans
    echo "Done."
}
trap cleanup EXIT SIGINT SIGTERM

if ! command -v docker > /dev/null 2>&1; then
    echo "Error: docker not found. Install Docker Desktop and retry."
    exit 1
fi
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker daemon not running. Start Docker Desktop and retry."
    exit 1
fi

echo "Wiping any previous test stack + volumes for a deterministic run..."
$COMPOSE down -v --remove-orphans > /dev/null 2>&1 || true

echo "Bringing up Docker test stack (postgres-test, backend-test, frontend-test)..."
$COMPOSE up --build -d

BACKEND_URL="http://localhost:8001"
FRONTEND_URL="http://localhost:5174"

echo "Waiting for backend ($BACKEND_URL/health)..."
max_attempts=60
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -sf "$BACKEND_URL/health" > /dev/null 2>&1; then
        echo "Backend is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ $attempt -eq $max_attempts ]; then
    echo "Error: Backend failed to start within ${max_attempts}s"
    $COMPOSE logs backend-test | tail -60
    exit 1
fi

echo "Waiting for frontend ($FRONTEND_URL)..."
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -sf "$FRONTEND_URL" > /dev/null 2>&1; then
        echo "Frontend is ready!"
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ $attempt -eq $max_attempts ]; then
    echo "Warning: Frontend may not be fully ready"
fi

echo "Seeding test users inside backend-test container..."
$COMPOSE exec -T backend-test python scripts/seed_test_data.py || {
    echo "Error: Test user seeding failed"
    exit 1
}

echo "Seeding test candle data inside backend-test container..."
$COMPOSE exec -T backend-test python scripts/seed_test_candles.py || {
    echo "Error: Test candle seeding failed"
    exit 1
}

echo ""
echo "========================================"
echo "Running E2E Tests (Playwright on host → Docker stack)"
echo "========================================"
echo ""

cd portal/web
PLAYWRIGHT_BASE_URL="$FRONTEND_URL" \
API_BASE_URL="$BACKEND_URL/api/v1" \
npx playwright test $HEADED $DEBUG
E2E_EXIT_CODE=$?
cd "$PROJECT_ROOT"

echo ""
echo "========================================"
echo "E2E Tests Complete"
echo "========================================"

exit $E2E_EXIT_CODE
