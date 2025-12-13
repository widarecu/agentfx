#!/bin/bash

# AgentFX Docker Server Startup Script
# Usage: ./start-server-docker.sh [environment]
# Environments: development, test, staging, production

set -e

# Default to development if no environment specified
ENVIRONMENT=${1:-development}

echo "🐳 Starting AgentFX Server in Docker - $ENVIRONMENT environment..."

# Validate environment
case $ENVIRONMENT in
    development|dev|test|testing|staging|stage|production|prod)
        echo "✅ Environment: $ENVIRONMENT"
        ;;
    *)
        echo "❌ Invalid environment: $ENVIRONMENT"
        echo "Valid environments: development, test, staging, production"
        exit 1
        ;;
esac

# Map environment to docker-compose file
case $ENVIRONMENT in
    development|dev)
        COMPOSE_FILE="docker-compose.development.yml"
        ;;
    test|testing)
        COMPOSE_FILE="docker-compose.test.yml"
        ;;
    staging|stage)
        COMPOSE_FILE="docker-compose.staging.yml"
        ;;
    production|prod)
        COMPOSE_FILE="docker-compose.production.yml"
        ;;
esac

echo "📦 Using Docker Compose file: $COMPOSE_FILE"

# Check if docker-compose file exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ Docker Compose file not found: $COMPOSE_FILE"
    exit 1
fi

# Start the database
echo "📦 Starting PostgreSQL database..."
docker-compose -f $COMPOSE_FILE up -d

# Wait for database to be ready
echo "⏳ Waiting for database to be ready..."
max_attempts=30
attempt=0

# Determine database user and name based on environment
case $ENVIRONMENT in
    development|dev)
        DB_USER="agentfx"
        DB_NAME="agentfx_dev"
        ;;
    test|testing)
        DB_USER="test_user"
        DB_NAME="agentfx_test"
        ;;
    staging|stage)
        DB_USER="agentfx_staging"
        DB_NAME="agentfx_staging"
        ;;
    production|prod)
        DB_USER="agentfx_prod"
        DB_NAME="agentfx_production"
        ;;
esac

while [ $attempt -lt $max_attempts ]; do
    if docker-compose -f $COMPOSE_FILE exec -T postgres pg_isready -U $DB_USER -d $DB_NAME > /dev/null 2>&1; then
        echo "✅ Database is ready!"
        break
    fi
    
    attempt=$((attempt + 1))
    echo "   Attempt $attempt/$max_attempts..."
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo "❌ Database failed to start after $max_attempts attempts"
    docker-compose -f $COMPOSE_FILE down
    exit 1
fi

# Apply database migrations
echo "🔧 Applying database migrations..."
export ENVIRONMENT=$ENVIRONMENT
source venv/bin/activate
alembic upgrade head

# Start the server
echo "🌐 Starting FastAPI server..."
echo "📍 Environment: $ENVIRONMENT"
echo "🔗 API will be available at: http://localhost:8000"
echo "📚 Docs will be available at: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Start uvicorn with the appropriate environment
export ENVIRONMENT=$ENVIRONMENT
python -m uvicorn src.agentfx.api.main:app --host 0.0.0.0 --port 8000 --reload
