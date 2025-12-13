#!/bin/bash

# Integration test runner with Docker PostgreSQL and Alembic migrations
# This script sets up the test database using migrations and runs real integration tests

set -e

echo "🚀 Starting Integration Tests with Real PostgreSQL Database and Migrations"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose is not installed or not in PATH"
    exit 1
fi

# Set test environment
export ENVIRONMENT=test

# Start the test database
echo "📦 Starting PostgreSQL test database..."
docker-compose -f docker-compose.test.yml up -d

# Wait for database to be ready
echo "⏳ Waiting for database to be ready..."
max_attempts=30
attempt=0

while [ $attempt -lt $max_attempts ]; do
    if docker-compose -f docker-compose.test.yml exec -T postgres-test pg_isready -U test_user -d agentfx_test > /dev/null 2>&1; then
        echo "✅ Database is ready!"
        break
    fi
    
    attempt=$((attempt + 1))
    echo "   Attempt $attempt/$max_attempts..."
    sleep 1
done

if [ $attempt -eq $max_attempts ]; then
    echo "❌ Database failed to start after $max_attempts attempts"
    docker-compose -f docker-compose.test.yml down
    exit 1
fi

# Apply database migrations using our settings system
echo "🔧 Applying database migrations..."
source venv/bin/activate
DATABASE_URL="postgresql://test_user:test_password@localhost:5433/agentfx_test" alembic upgrade head

# Run the integration tests with test environment
echo "🧪 Running integration tests..."
if [ $# -eq 0 ]; then
    # Run all integration tests
    pytest tests/integration/ -v --tb=short
else
    # Run specific test file or marker
    pytest tests/integration/ "$@" -v --tb=short
fi
TEST_EXIT_CODE=$?

# Clean up: Stop and remove the test database
echo "🧹 Cleaning up test database..."
docker-compose -f docker-compose.test.yml down -v

# Exit with the same code as the tests
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ All integration tests passed!"
else
    echo "❌ Some integration tests failed (exit code: $TEST_EXIT_CODE)"
fi

exit $TEST_EXIT_CODE
