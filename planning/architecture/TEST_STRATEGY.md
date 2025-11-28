# Test Strategy and Standards

## Overview

This document defines the testing strategy aligned with the project's engineering standards and architectural principles.

## Alignment with Engineering Standards

As defined in `STANDARDS.md`:
- **Testing**: unit + integration tests required for PR merge
- **Repository layout**: tests/ mirror module layout with unit/integration
- **Virtual environments**: mandatory for dev/test/build
- **Feature flags**: enable/disable strategies, exploration mode, environment stage

## Test Structure

### Feature-First Organization

Tests are organized by business capabilities rather than technology layers:

```
tests/
├── unit/           # Fast, isolated component tests
│   ├── auth/       # Authentication logic
│   ├── strategies/ # Trading strategy logic
│   ├── market_data/ # Market data processing
│   ├── backtest/   # Backtesting engine
│   └── shared/     # Shared utilities and fixtures
├── integration/    # Multi-component tests
│   ├── auth/       # API integration
│   ├── strategies/ # Strategy execution
│   ├── market_data/ # WebSocket & data pipelines
│   └── api/        # Endpoint integration
├── e2e/           # User journey tests (Playwright)
├── performance/   # Load and stress tests
├── contracts/     # API contract tests
├── fixtures/      # Shared test data
└── utils/         # Test utilities
```

### Test Categories

#### Unit Tests
- **Purpose**: Test individual components in isolation
- **Speed**: Fast (< 1 second per test)
- **Dependencies**: Mocked external services
- **Coverage**: Business logic, calculations, data processing
- **Requirements**: Must pass for PR merge

#### Integration Tests
- **Purpose**: Test multiple components working together
- **Speed**: Medium (1-10 seconds per test)
- **Dependencies**: Real databases, external APIs
- **Coverage**: API endpoints, workflows, data pipelines
- **Requirements**: Must pass for PR merge

#### E2E Tests
- **Purpose**: Test complete user workflows
- **Speed**: Slow (10+ seconds per test)
- **Dependencies**: Full application stack
- **Coverage**: User journeys, UI interactions
- **Requirements**: Must pass before main branch merge

#### Performance Tests
- **Purpose**: Test system performance under load
- **Speed**: Very slow (minutes per test)
- **Dependencies**: Load testing tools
- **Coverage**: API load tests, strategy performance
- **Requirements**: Must pass before production deployment

## Testing Standards

### Code Coverage Requirements
- **Unit tests**: Minimum 80% line coverage for business logic
- **Integration tests**: Critical path coverage
- **E2E tests**: User journey coverage

### Test Data Management
- **Fixtures**: Versioned test data in `tests/fixtures/`
- **Environments**: Separate test databases per environment
- **Cleanup**: Automatic cleanup after each test
- **Isolation**: Tests must not depend on each other

### Mock and External Service Standards
- **Broker APIs**: Mocked for unit tests, real for integration
- **Market Data**: Sample data for unit tests, real feeds for integration
- **Database**: In-memory for unit tests, test instance for integration

## Feature Testing Requirements

### Strategy Testing
```python
# Unit test strategy logic
@pytest.mark.unit
@pytest.mark.strategies
def test_strategy_signal_generation():
    # Test pure strategy logic with mocked data
    pass

# Integration test strategy execution
@pytest.mark.integration
@pytest.mark.strategies
@pytest.mark.slow
def test_strategy_execution():
    # Test strategy with real broker and market data
    pass

# E2E test strategy workflow
@pytest.mark.e2e
def test_strategy_configuration_workflow():
    # Test complete user workflow for strategy setup
    pass
```

### Authentication Testing
```python
# Unit test auth logic
@pytest.mark.unit
@pytest.mark.auth
def test_token_validation():
    # Test token validation logic
    pass

# Integration test auth API
@pytest.mark.integration
@pytest.mark.auth
def test_auth_endpoints():
    # Test authentication endpoints
    pass
```

### Market Data Testing
```python
# Unit test data processing
@pytest.mark.unit
@pytest.mark.market_data
def test_market_data_processing():
    # Test data processing with sample data
    pass

# Integration test WebSocket
@pytest.mark.integration
@pytest.mark.market_data
@pytest.mark.slow
def test_websocket_connection():
    # Test real WebSocket connection
    pass
```

## Environment Strategy

### Test Environments
- **Unit**: In-memory, mocked dependencies
- **Integration**: Test database, mocked external APIs
- **E2E**: Full stack with test services
- **Performance**: Dedicated performance environment

### Feature Flags in Testing
```python
@pytest.mark.unit
def test_strategy_with_feature_flag():
    with feature_flag('new_strategy_enabled', True):
        # Test new strategy behavior
        pass
    
    with feature_flag('new_strategy_enabled', False):
        # Test fallback behavior
        pass
```

## Quality Gates

### PR Requirements
- All unit tests pass
- All integration tests pass
- Minimum coverage thresholds met
- No new test failures

### Release Requirements
- All PR requirements met
- E2E tests pass
- Performance tests meet thresholds
- Security tests pass

## Running Tests

### Development Workflow
```bash
# Fast feedback during development
pytest tests/unit/ -m "not slow"

# Full test suite before PR
pytest tests/unit/ tests/integration/

# E2E tests before main branch
cd portal/web && npm run test:e2e
```

### CI/CD Pipeline
```bash
# Pull Request
pytest tests/unit/ tests/integration/ --maxfail=1

# Main Branch
pytest tests/unit/ tests/integration/ --maxfail=1
npm run test:e2e

# Release
pytest tests/unit/ tests/integration/ --maxfail=1
npm run test:e2e
pytest tests/performance/ --maxfail=1
```

## Test Data and Results Management

### Test Results Storage

Test results and artifacts should be organized as follows:

```
project-root/
├── .coverage                    # Python coverage data (git ignored)
├── .pytest_cache/              # Pytest cache (git ignored)
├── htmlcov/                    # HTML coverage reports (git ignored)
├── test-results/               # Playwright test results (git ignored)
│   ├── screenshots/           # Failed test screenshots
│   ├── traces/                 # Playwright traces
│   └── reports/                # Test execution reports
├── test-data/                  # Generated test data (git ignored)
│   ├── market_data/           # Sample market data for tests
│   ├── backtest_results/      # Backtest output files
│   └── temp/                  # Temporary test files
├── test_agentfx.db            # Test database (git ignored)
├── agentfx_api.log            # Test logs (git ignored)
└── tests/                     # Test source code (version controlled)
```

### Git Ignore Configuration

Ensure `.gitignore` includes test artifacts:
```
# Test results and coverage
.coverage
htmlcov/
.pytest_cache/
test-results/
test-data/
test_*.db
*_test.log
```

### Test Data Categories

#### Fixtures (Version Controlled)
- **Location**: `tests/fixtures/`
- **Purpose**: Static test data that should be version controlled
- **Examples**: User configurations, sample strategy parameters, test scenarios
- **Version**: Yes - these are part of the test suite

#### Generated Test Data (Temporary)
- **Location**: `test-data/`
- **Purpose**: Data generated during test execution
- **Examples**: Downloaded market data, backtest results, temporary files
- **Version**: No - generated during test runs

#### Test Results (Temporary)
- **Location**: `test-results/`
- **Purpose**: Test execution artifacts
- **Examples**: Screenshots, traces, reports, logs
- **Version**: No - regenerated each test run

### Environment-Specific Data

#### Test Databases
- **Unit tests**: In-memory SQLite
- **Integration tests**: `test_agentfx.db` (separate from development)
- **E2E tests**: Dedicated test database instance

#### Configuration
- **Test configs**: Environment-specific test configurations
- **Feature flags**: Test feature flag configurations
- **API endpoints**: Test API endpoint configurations

### Cleanup Strategies

#### Automatic Cleanup
```python
# conftest.py - Automatic cleanup fixtures
@pytest.fixture(autouse=True)
def cleanup_test_data():
    yield
    # Clean up temporary files after each test
    if os.path.exists('test-data/temp'):
        shutil.rmtree('test-data/temp')
```

#### Manual Cleanup Commands
```bash
# Clean all test artifacts
make clean-tests

# Clean specific types
pytest --cache-clear  # Clear pytest cache
rm -rf test-results/  # Clear test results
rm -rf test-data/     # Clear generated test data
```

### Performance Test Data

#### Load Test Data
- **Location**: `test-data/performance/`
- **Purpose**: Data for performance and load testing
- **Examples**: Large datasets, stress test scenarios
- **Retention**: Keep recent runs, archive old results

#### Benchmark Results
- **Location**: `test-data/benchmarks/`
- **Purpose**: Performance baseline comparisons
- **Format**: JSON/CSV for easy analysis
- **Retention**: Keep historical data for trend analysis

## Test Utilities and Helpers

### Fixtures
- **User fixtures**: Test users with different roles
- **Market data fixtures**: Sample market data for testing
- **Strategy fixtures**: Strategy configurations for testing

### Helpers
- **Auth helpers**: Login/logout utilities
- **Data generators**: Market data generation
- **Mock servers**: Test server utilities

## Documentation Requirements

### Test Documentation
- **README**: Quick start and structure overview
- **Test files**: Descriptive names and docstrings
- **Coverage reports**: Generated and reviewed
- **Test plans**: For complex features

### Architecture Alignment
- **ADR compliance**: Tests must support architectural decisions
- **NFR compliance**: Performance and security requirements
- **Principle adherence**: Follow project architectural principles

## Continuous Improvement

### Metrics
- **Test execution time**: Monitor and optimize
- **Coverage trends**: Maintain or improve coverage
- **Flaky tests**: Identify and fix flaky tests
- **Test effectiveness**: Review bug detection rate

### Reviews
- **Monthly**: Test strategy review
- **Quarterly**: Tooling and infrastructure review
- **Annually**: Standards and process review

This test strategy ensures alignment with engineering standards while providing comprehensive coverage for the trading platform's requirements.
