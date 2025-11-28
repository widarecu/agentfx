

# Test Suite

## 📋 Documentation

For detailed test architecture, standards, and guidelines, see:
- **Test Strategy**: `planning/architecture/TEST_STRATEGY.md`
- **Test Structure**: `planning/architecture/TEST_STRUCTURE_EVOLUTION.md`
- **Migration Record**: `planning/architecture/TEST_MIGRATION_RECORD.md`
- **General Standards**: `planning/architecture/STANDARDS.md`

## 🚀 Quick Start

```bash
# Run all unit tests (fast feedback)
pytest tests/unit/

# Run all integration tests
pytest tests/integration/

# Run all backend tests
pytest tests/unit/ tests/integration/

# Run E2E tests
cd portal/web && npm run test:e2e

# Run tests by feature
pytest tests/unit/auth/ tests/integration/auth/ -k "auth"
pytest tests/unit/strategies/ tests/integration/strategies/ -k "strategies"
```

## 📁 Structure

```
tests/
├── unit/           # Fast, isolated component tests
│   ├── auth/       # Authentication logic
│   ├── strategies/ # Trading strategy logic
│   ├── market_data/ # Market data processing
│   └── backtest/   # Backtesting engine
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

## 🏷️ Test Markers

```bash
# By test type
pytest -m unit                    # Fast tests
pytest -m integration             # Medium tests
pytest -m e2e                     # Slow tests

# By feature
pytest -m auth                    # Authentication tests
pytest -m strategies              # Strategy tests
pytest -m market_data             # Market data tests
pytest -m backtest                # Backtest tests

# By speed
pytest -m "not slow"              # Skip slow tests
pytest -m performance             # Performance tests only
```

## 📝 Writing New Tests

Follow the established structure in `planning/architecture/TEST_STRATEGY.md`:

1. **Choose the right category**: unit/integration/e2e
2. **Place in feature directory**: auth/strategies/market_data/backtest
3. **Use appropriate markers**: See pytest.ini for available markers
4. **Follow naming conventions**: `test_[feature]_[scenario].py`

## 🔧 Configuration

- **pytest.ini**: Test configuration and markers
- **conftest.py**: Shared fixtures and configuration
- **fixtures/**: Reusable test data (version controlled)
- **utils/**: Test helpers and utilities

## 📊 Test Results & Data

### Results Location
- **Playwright results**: `../test-results/` (screenshots, traces, reports)
- **Coverage reports**: `htmlcov/` (HTML coverage)
- **Python coverage**: `.coverage` (coverage data)
- **Test cache**: `.pytest_cache/` (pytest cache)

### Test Data Storage
- **Fixtures**: `tests/fixtures/` (static test data, versioned)
- **Generated data**: `../test-data/` (temporary, generated during tests)
  - `test-data/market_data/` - Sample market data
  - `test-data/backtest_results/` - Backtest outputs
  - `test-data/temp/` - Temporary files
  - `test-data/performance/` - Performance test data

### Cleanup Commands
```bash
# Clean all test artifacts
rm -rf test-results/ test-data/ htmlcov/ .coverage .pytest_cache/

# Clean specific types
pytest --cache-clear              # Clear pytest cache
rm -rf test-results/screenshots/  # Clear screenshots
rm -rf test-data/temp/            # Clear temporary files
```

## 🐛 Troubleshooting

For detailed troubleshooting guides, see `planning/architecture/TEST_STRATEGY.md`.

Quick fixes:
```bash
# Check test discovery
pytest --collect-only

# Run with verbose output
pytest -v

# Stop on first failure
pytest -x

# Run specific test file
pytest tests/unit/auth/test_auth.py -v
```