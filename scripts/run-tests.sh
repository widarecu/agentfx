#!/bin/bash
# Test execution script for different testing scenarios

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}AgentFX Market Data Test Execution Script${NC}"
echo "============================================"

# Function to run tests with colored output
run_tests() {
    local description="$1"
    local command="$2"
    
    echo -e "\n${YELLOW}Running: $description${NC}"
    echo -e "${BLUE}Command: $command${NC}"
    echo "--------------------------------------------"
    
    if eval "$command"; then
        echo -e "${GREEN}✓ $description - PASSED${NC}"
    else
        echo -e "${RED}✗ $description - FAILED${NC}"
        exit 1
    fi
}

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate

# Development: Fast feedback (unit tests only, no slow tests)
run_tests "Development Tests (Fast Feedback)" \
    "python -m pytest tests/unit/ -m 'not slow' -v"

# PR Pipeline: No external dependencies (unit + integration, no real OANDA)
run_tests "PR Pipeline Tests (No External Dependencies)" \
    "python -m pytest tests/unit/ tests/integration/ -m 'not oanda_real and not slow' -v"

# Staging Pipeline: Include real OANDA tests (requires OANDA credentials)
if [ -n "$OANDA_INTEGRATION_TESTS_ENABLED" ]; then
    run_tests "Staging Pipeline Tests (With Real OANDA)" \
        "python -m pytest tests/unit/ tests/integration/ -m 'not slow' -v"
    
    run_tests "Real OANDA Integration Tests" \
        "python -m pytest tests/integration/ -m 'oanda_real' -v --tb=short"
else
    echo -e "\n${YELLOW}Skipping OANDA integration tests (OANDA_INTEGRATION_TESTS_ENABLED not set)${NC}"
    echo "To run OANDA integration tests, set:"
    echo "export OANDA_INTEGRATION_TESTS_ENABLED=true"
    echo "export OANDA_API_KEY=your_practice_api_key"
    echo "export OANDA_ACCOUNT_ID=your_practice_account_id"
    echo "export OANDA_STREAMING_HOST=stream-fxpractice.oanda.com"
    echo "export OANDA_API_HOST=api-fxpractice.oanda.com"
fi

# Coverage Report
run_tests "Coverage Report" \
    "python -m pytest tests/unit/ tests/integration/ -m 'not oanda_real and not slow' --cov=src --cov-report=term-missing --cov-report=html"

echo -e "\n${GREEN}All test scenarios completed successfully!${NC}"
echo -e "${BLUE}Coverage report available in htmlcov/index.html${NC}"

# Performance Tests (optional)
read -p "Run performance tests? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    run_tests "Performance Tests" \
        "python -m pytest tests/performance/ -v --tb=short"
fi

echo -e "\n${GREEN}Test execution completed!${NC}"
