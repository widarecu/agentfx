#!/bin/bash

# AgentFX Frontend Server Startup Script
# Usage: ./scripts/start-frontend.sh [environment]
# Environments: development, test, staging, production
#
# This script starts the Vite dev server with the appropriate mode
# which loads the corresponding .env.{mode} file.

set -e

# Use ENVIRONMENT env var, then positional arg, then default to development
ENVIRONMENT=${ENVIRONMENT:-${1:-development}}

echo "========================================"
echo "AgentFX Frontend Server"
echo "========================================"

# Validate and normalize environment
case $ENVIRONMENT in
    development|dev)
        ENVIRONMENT=development
        NPM_SCRIPT="dev"
        ;;
    test|testing)
        ENVIRONMENT=test
        NPM_SCRIPT="dev:test"
        ;;
    staging|stage)
        ENVIRONMENT=staging
        NPM_SCRIPT="dev:staging"
        ;;
    production|prod)
        echo "Error: Cannot run production in dev mode."
        echo "Use 'npm run build' for production builds."
        exit 1
        ;;
    *)
        echo "Error: Invalid environment: $ENVIRONMENT"
        echo "Valid environments: development, test, staging"
        exit 1
        ;;
esac

echo "Environment: $ENVIRONMENT"
echo "NPM Script: $NPM_SCRIPT"
echo "Config file: .env.$ENVIRONMENT"
echo "========================================"

# Navigate to frontend directory
cd "$(dirname "$0")/../portal/web"

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

# Start the development server
echo ""
echo "========================================"
echo "Starting Vite development server..."
echo "App: http://localhost:5173"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

exec npm run $NPM_SCRIPT
