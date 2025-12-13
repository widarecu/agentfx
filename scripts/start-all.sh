#!/bin/bash

# AgentFX Full Stack Startup Script
# Usage: ./scripts/start-all.sh [environment]
# Environments: development, test, staging
#
# This script starts both backend and frontend servers for the specified environment.
# Use Ctrl+C to stop both servers.

set -e

# Use ENVIRONMENT env var, then positional arg, then default to development
ENVIRONMENT=${ENVIRONMENT:-${1:-development}}

echo "========================================"
echo "AgentFX Full Stack Startup"
echo "========================================"
echo "Environment: $ENVIRONMENT"
echo "========================================"

# Validate environment
case $ENVIRONMENT in
    development|dev|test|testing|staging|stage)
        ;;
    production|prod)
        echo "Error: Use production deployment scripts for production."
        exit 1
        ;;
    *)
        echo "Error: Invalid environment: $ENVIRONMENT"
        echo "Valid environments: development, test, staging"
        exit 1
        ;;
esac

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down servers..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    exit 0
}

# Set trap for cleanup
trap cleanup SIGINT SIGTERM

# Start backend in background
echo "Starting backend server..."
"$SCRIPT_DIR/start-backend.sh" $ENVIRONMENT &
BACKEND_PID=$!

# Wait for backend to be ready
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
    echo "Warning: Backend may not be fully ready"
fi

# Start frontend in background
echo "Starting frontend server..."
"$SCRIPT_DIR/start-frontend.sh" $ENVIRONMENT &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "AgentFX is running!"
echo "========================================"
echo "Backend API: http://localhost:8000"
echo "Backend Docs: http://localhost:8000/docs"
echo "Frontend App: http://localhost:5173"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
