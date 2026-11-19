

# Test Suite

## 📋 Documentation

For detailed test architecture, standards, and guidelines, see:
- **Test Strategy**: `planning/architecture/TEST_STRATEGY.md`
- **Test Structure**: `planning/architecture/TEST_STRUCTURE_EVOLUTION.md`
- **Migration Record**: `planning/architecture/TEST_MIGRATION_RECORD.md`
- **General Standards**: `planning/architecture/STANDARDS.md`

## 🚀 Quick Start

> **IMPORTANT:** Always use the Makefile targets. They set `ENVIRONMENT=test` and `PYTHONPATH=src`, so `.env.test` is loaded and tests reach the Dockerised Postgres on `localhost:5433`. The full command reference for agents is `.claude/rules/testing.md`.

### Prerequisites

Docker Desktop (or Colima / OrbStack) must be running; that is the only dependency for integration and E2E tests. Local PostgreSQL is not supported.

```bash
docker info                     # Docker daemon is up
docker compose version          # Compose v2 is available
lsof -i :5433 -i :8001 -i :5174 # E2E ports are free
```

```bash
# Run all unit tests (fast feedback): backend pytest + portal Vitest
make test-unit

# Run all integration tests (needs the test stack: make docker-up PROFILE=test)
make test-integration

# Run all backend tests (unit + integration)
make test

# Run E2E tests (handles servers automatically)
make e2e

# Run specific test file
make test-file FILE=tests/unit/auth/test_security.py

# Run tests matching pattern
make test-pattern PATTERN="test_login"
```

### DO NOT USE (Common Mistakes)
```bash
# These commands are UNSAFE - they don't set ENVIRONMENT=test
pytest tests/unit/           # WRONG - missing environment
pytest tests/integration/    # WRONG - will use wrong database
python -m pytest tests/      # WRONG - uses system Python
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

Test markers can be used with Makefile commands:

```bash
# Run tests by marker using make
make test-unit                    # Fast tests (unit marker)
make test-integration             # Medium tests (integration marker)
make e2e                          # E2E tests

# For marker filtering, use ENVIRONMENT-safe direct pytest:
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -m auth -v
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -m "not slow" -v
```

Available markers: `unit`, `integration`, `e2e`, `auth`, `strategies`, `market_data`, `backtest`, `slow`, `performance`

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

## 👤 E2E Test Users

`make e2e` seeds these automatically. To seed a test stack you started yourself:
`docker compose --profile test exec backend-test python scripts/seed_test_data.py`

| Email | Password | Role |
|-------|----------|------|
| test@example.com | TestPass123! | VIEWER |
| viewer@example.com | secretpassword | VIEWER |
| trader@example.com | traderpassword | TRADER |
| admin@example.com | adminpassword | ADMIN |
| inactive@example.com | inactivepassword | VIEWER (inactive) |

`seed_instruments.py` (6 FX pairs metadata) and `seed_test_data.py` create metadata and users only, never price data.

## 🐛 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'agentfx'` | `PYTHONPATH` not set | Use `make test-unit` |
| Tests use the wrong database or see dev data | `ENVIRONMENT=test` not set, so `.env.development` loaded | Use `make test-*` |
| `could not connect to server` on integration tests | Test Postgres not running | `make docker-up PROFILE=test` |
| E2E fails with `Cannot connect to the Docker daemon` | Docker not running | Start Docker, re-run `make e2e` |
| E2E fails with `port is already allocated` on 5433/8001/5174 | A previous test stack is still up | `make docker-down PROFILE=test` |
| `relation "users" does not exist` | Migrations didn't finish in `backend-test` | `make docker-logs PROFILE=test`, look for migration errors |

Quick fixes:
```bash
# Check test discovery
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest --collect-only

# Run with verbose output (use Makefile)
make test-file FILE=tests/unit/auth/test_auth.py

# Stop on first failure
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -x

# Run specific test file (RECOMMENDED)
make test-file FILE=tests/unit/auth/test_auth.py
```

