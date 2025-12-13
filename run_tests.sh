#!/bin/bash
# ==============================================================================
# AgentFX Test Runner
# ==============================================================================
# A standardized script for running tests with proper environment configuration.
#
# Usage:
#   ./run_tests.sh [test-type] [options]
#
# Test Types:
#   unit         Run unit tests only (fast, mocked dependencies)
#   integration  Run integration tests only (requires PostgreSQL)
#   e2e          Run E2E tests (requires backend + frontend servers)
#   all          Run all tests (unit + integration, excludes e2e)
#   <empty>      Same as 'all'
#
# Options:
#   -v, --verbose     Verbose output
#   -f, --fast        Skip coverage (faster execution)
#   --file FILE       Run specific test file
#   --pattern PATTERN Run tests matching pattern
#   --headed          E2E: Run with browser visible
#   --debug           E2E: Run in debug mode
#   -h, --help        Show this help message
#
# Examples:
#   ./run_tests.sh unit                          # Run all unit tests
#   ./run_tests.sh integration                   # Run all integration tests
#   ./run_tests.sh integration -v                # Run integration tests verbosely
#   ./run_tests.sh --file tests/unit/auth/test_security.py
#   ./run_tests.sh --pattern "test_login"
#   ./run_tests.sh e2e --headed                  # E2E with visible browser
#
# Environment:
#   This script automatically sets ENVIRONMENT=test to load .env.test
#   which configures the correct database connection and test settings.
#
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory and project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

# Detect virtual environment
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
elif [ -d "venv" ]; then
    VENV_DIR="venv"
else
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo "Run 'make dev' to create the virtual environment first."
    exit 1
fi

# Default values
TEST_TYPE="all"
VERBOSE=""
FAST=""
FILE=""
PATTERN=""
E2E_HEADED=""
E2E_DEBUG=""

# Help message
show_help() {
    head -n 40 "$0" | tail -n +2 | sed 's/^# //' | sed 's/^#//'
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        unit|integration|e2e|all)
            TEST_TYPE="$1"
            shift
            ;;
        -v|--verbose)
            VERBOSE="-v"
            shift
            ;;
        -f|--fast)
            FAST="--no-cov"
            shift
            ;;
        --file)
            FILE="$2"
            shift 2
            ;;
        --pattern)
            PATTERN="$2"
            shift 2
            ;;
        --headed)
            E2E_HEADED="--headed"
            shift
            ;;
        --debug)
            E2E_DEBUG="--debug"
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Set environment variables
export ENVIRONMENT=test
export PYTHONPATH=src

# Print header
echo ""
echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}      AgentFX Test Runner${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""
echo -e "Environment: ${GREEN}test${NC}"
echo -e "Config file: ${GREEN}.env.test${NC}"

# Source the test environment file for display
if [ -f ".env.test" ]; then
    DB_URL=$(grep "^DATABASE_URL=" .env.test | cut -d'=' -f2)
    echo -e "Database:    ${GREEN}${DB_URL}${NC}"
else
    echo -e "${YELLOW}Warning: .env.test file not found${NC}"
fi
echo ""

# Function to check database connectivity
check_database() {
    echo -e "${BLUE}Checking database connectivity...${NC}"

    # Extract connection info from .env.test
    DB_HOST=$(grep "^TEST_DATABASE_HOST=" .env.test 2>/dev/null | cut -d'=' -f2 || echo "localhost")
    DB_PORT=$(grep "^TEST_DATABASE_PORT=" .env.test 2>/dev/null | cut -d'=' -f2 || echo "5432")
    DB_NAME=$(grep "^TEST_DATABASE_NAME=" .env.test 2>/dev/null | cut -d'=' -f2 || echo "agentfx_test")
    DB_USER=$(grep "^TEST_DATABASE_USER=" .env.test 2>/dev/null | cut -d'=' -f2 || echo "test_user")

    # Try to connect using psql
    PSQL_BIN=""
    if command -v psql > /dev/null 2>&1; then
        PSQL_BIN="psql"
    elif [ -x "/opt/homebrew/opt/postgresql@15/bin/psql" ]; then
        PSQL_BIN="/opt/homebrew/opt/postgresql@15/bin/psql"
    elif [ -x "/usr/local/bin/psql" ]; then
        PSQL_BIN="/usr/local/bin/psql"
    fi

    if [ -n "$PSQL_BIN" ]; then
        if $PSQL_BIN -U "$DB_USER" -d "$DB_NAME" -h "$DB_HOST" -p "$DB_PORT" -c "SELECT 1" > /dev/null 2>&1; then
            echo -e "${GREEN}Database connection successful!${NC}"
            return 0
        fi
    fi

    echo -e "${YELLOW}Warning: Cannot connect to database at ${DB_HOST}:${DB_PORT}${NC}"
    echo -e "${YELLOW}Integration tests may fail.${NC}"
    echo ""
    echo "To start the test database, run one of:"
    echo "  1. Local PostgreSQL: brew services start postgresql@15"
    echo "  2. Docker: docker-compose -f docker-compose.test.yml up -d"
    echo ""
    return 1
}

# Function to run pytest with common options
run_pytest() {
    local MARKER="$1"
    local EXTRA_ARGS="$2"

    local CMD="$VENV_DIR/bin/pytest"

    # Add marker if specified
    if [ -n "$MARKER" ]; then
        CMD="$CMD -m $MARKER"
    fi

    # Add verbose flag
    if [ -n "$VERBOSE" ]; then
        CMD="$CMD -v"
    else
        CMD="$CMD -q"
    fi

    # Add fast flag (skip coverage)
    if [ -n "$FAST" ]; then
        CMD="$CMD $FAST"
    fi

    # Add extra args
    if [ -n "$EXTRA_ARGS" ]; then
        CMD="$CMD $EXTRA_ARGS"
    fi

    echo -e "${BLUE}Running: $CMD${NC}"
    echo ""

    # Execute pytest
    $CMD
    return $?
}

# Execute based on test type
case $TEST_TYPE in
    unit)
        echo -e "${GREEN}Running Unit Tests${NC}"
        echo "These tests use mocked dependencies and do not require a database."
        echo ""
        run_pytest "unit" ""
        ;;

    integration)
        echo -e "${GREEN}Running Integration Tests${NC}"
        echo "These tests require PostgreSQL to be running."
        echo ""
        check_database || true
        run_pytest "integration" ""
        ;;

    e2e)
        echo -e "${GREEN}Running E2E Tests${NC}"
        echo "These tests require backend and frontend servers."
        echo ""

        # Delegate to the dedicated E2E runner script
        if [ -f "scripts/run-e2e-tests.sh" ]; then
            ./scripts/run-e2e-tests.sh $E2E_HEADED $E2E_DEBUG
        else
            echo -e "${RED}Error: scripts/run-e2e-tests.sh not found${NC}"
            exit 1
        fi
        ;;

    all)
        if [ -n "$FILE" ]; then
            echo -e "${GREEN}Running Specific Test File: $FILE${NC}"
            echo ""
            # Check if it's an integration test file
            if [[ "$FILE" == *"integration"* ]]; then
                check_database || true
            fi
            run_pytest "" "$FILE"
        elif [ -n "$PATTERN" ]; then
            echo -e "${GREEN}Running Tests Matching: $PATTERN${NC}"
            echo ""
            run_pytest "" "-k \"$PATTERN\" --ignore=tests/e2e"
        else
            echo -e "${GREEN}Running All Tests (Unit + Integration)${NC}"
            echo "Excludes E2E tests. Use './run_tests.sh e2e' for E2E tests."
            echo ""
            check_database || true
            run_pytest "" "--ignore=tests/e2e"
        fi
        ;;
esac

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}======================================${NC}"
    echo -e "${GREEN}      Tests Passed!${NC}"
    echo -e "${GREEN}======================================${NC}"
else
    echo -e "${RED}======================================${NC}"
    echo -e "${RED}      Tests Failed (exit code: $EXIT_CODE)${NC}"
    echo -e "${RED}======================================${NC}"
fi

exit $EXIT_CODE
