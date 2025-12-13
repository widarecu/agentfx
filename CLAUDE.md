# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentFX is an AI-powered autonomous FX trading platform. The ultimate goal is to build an agentic system capable of:

1. **Define** - Autonomously identify and formulate trading strategy hypotheses
2. **Build** - Implement strategies with proper risk controls and execution logic
3. **Test** - Validate strategies through backtesting and paper trading
4. **Evolve** - Continuously learn and improve strategies based on performance

The objective is to maximize profit autonomously. Once a strategy is identified as profitable through validation gates, it is surfaced to the end user for approval before being promoted to live trading with real capital.

The system integrates with OANDA for real-time market data and trading execution, following a strict paper-first rollout model with human-in-the-loop approval for live deployment.

## Common Commands

```bash
# Environment setup
make venv          # Create virtual environment
make install       # Install production dependencies
make dev           # Install dev dependencies + pre-commit hooks

# Code quality
make lint          # Run ruff + black --check
make format        # Run black formatter
make typecheck     # Run mypy on src/

# Backtest
make backtest PAIRS="AUDUSD,EURUSD" DAYS=2
make backtest-full PAIRS="AUDUSD,EURUSD" DAYS=2 STRATEGY=ma FAST=20 SLOW=50
```

## Testing

### Quick Reference

```bash
# Using Makefile (recommended)
make test               # Run all tests (unit + integration)
make test-unit          # Run unit tests only
make test-integration   # Run integration tests only
make e2e                # Run E2E tests (manages servers automatically)

# Using run_tests.sh (more options)
./run_tests.sh unit                    # Unit tests
./run_tests.sh integration             # Integration tests
./run_tests.sh e2e --headed            # E2E with visible browser
./run_tests.sh --file tests/unit/auth/test_security.py
./run_tests.sh --pattern "test_login"
```

### Test Environment Configuration

Tests require `ENVIRONMENT=test` to load the correct configuration from `.env.test`:

| Setting | Value | Purpose |
|---------|-------|---------|
| `ENVIRONMENT` | `test` | Selects `.env.test` config file |
| `DATABASE_URL` | `postgresql://...localhost:5432/agentfx_test` | Test PostgreSQL database |
| `TESTING` | `true` | Enables test mode in application |

**Important:** The Makefile targets automatically set `ENVIRONMENT=test`. Do not manually override `DATABASE_URL` unless you have a specific reason.

### Test Categories

| Category | Command | Database Required | Description |
|----------|---------|-------------------|-------------|
| **Unit** | `make test-unit` | No (mocked) | Fast, isolated tests with mocked dependencies |
| **Integration** | `make test-integration` | Yes (PostgreSQL) | Tests with real database connections |
| **E2E** | `make e2e` | Yes + Servers | Full-stack tests with Playwright |

### Running Unit Tests

Unit tests are fast and do not require a database:

```bash
make test-unit
# Or with verbose output:
make test-file FILE=tests/unit/market_data/test_market_data.py
```

### Running Integration Tests

Integration tests require PostgreSQL running on port 5432:

```bash
# Start PostgreSQL (choose one):
brew services start postgresql@15              # macOS local
docker-compose -f docker-compose.test.yml up -d  # Docker

# Run integration tests:
make test-integration
```

### Running E2E Tests

E2E tests require both backend and frontend servers. The `make e2e` target handles everything:

```bash
# Full automated E2E run (recommended):
make e2e

# With options:
make e2e E2E_HEADED=1    # Show browser
make e2e E2E_DEBUG=1     # Debug mode
```

### Running Specific Tests

```bash
# Single test file
make test-file FILE=tests/unit/auth/test_security.py

# Tests matching a pattern
make test-pattern PATTERN="test_login"

# Single test function
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest \
  tests/unit/auth/test_security.py::TestSecurity::test_verify_password -v
```

### Test Markers

Tests are auto-marked based on directory location. You can also use explicit markers:

```python
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.market_data
@pytest.mark.oanda_real   # Requires real OANDA API connection
@pytest.mark.slow         # Long-running tests
```

### Troubleshooting Tests

**Integration tests fail with database connection error:**
```bash
# Check PostgreSQL is running on port 5432
psql -U test_user -d agentfx_test -h localhost -p 5432 -c "SELECT 1"

# If using Docker, ensure container is up:
docker-compose -f docker-compose.test.yml up -d
```

**E2E tests fail to start servers:**
```bash
# Ensure ports 8000 and 5173 are free
lsof -i :8000
lsof -i :5173

# Kill any existing processes if needed
kill -9 $(lsof -t -i:8000)
```

**Config loading wrong environment file:**
```bash
# Verify ENVIRONMENT is set correctly
echo $ENVIRONMENT  # Should be 'test'

# Check which .env file will be loaded
python -c "from agentfx.api.core.config import get_env_file; print(get_env_file())"
```

## Starting Servers (IMPORTANT)

**ALWAYS use the startup scripts in `scripts/` to start servers.** Do NOT start uvicorn or npm dev directly, as this bypasses environment configuration and database setup.

### Available Scripts

```bash
scripts/start-backend.sh   # Start backend API server
scripts/start-frontend.sh  # Start frontend dev server
scripts/start-all.sh       # Start both backend and frontend
scripts/seed_test_data.py  # Seed test users for E2E tests
```

### Starting Servers by Environment

```bash
# Development environment (default)
ENVIRONMENT=development ./scripts/start-backend.sh
ENVIRONMENT=development ./scripts/start-all.sh

# Test environment (for E2E tests, integration tests)
ENVIRONMENT=test ./scripts/start-backend.sh
ENVIRONMENT=test ./scripts/start-all.sh

# Staging environment
ENVIRONMENT=staging ./scripts/start-backend.sh
```

### What the Scripts Do

The startup scripts handle:
1. **Environment selection** - Load correct `.env.{environment}` file
2. **Database connectivity check** - Verify PostgreSQL is accessible
3. **Automatic fallback to Docker** - Start DB container if local PostgreSQL unavailable
4. **Database migrations** - Run `alembic upgrade head` before starting
5. **Virtual environment activation** - Use correct Python from `.venv/`

### Why NOT to Start Servers Directly

```bash
# DON'T DO THIS:
PYTHONPATH=src .venv/bin/uvicorn agentfx.api.main:app --reload
cd portal/web && npm run dev

# DO THIS INSTEAD:
ENVIRONMENT=test ./scripts/start-backend.sh
ENVIRONMENT=test ./scripts/start-frontend.sh
```

Starting servers directly bypasses:
- Environment-specific configuration loading
- Database connectivity verification
- Automatic migration application
- Proper PYTHONPATH and environment variable setup

### Seed Test Data (Before E2E Tests)

```bash
# Seed test users to the database
DATABASE_URL=postgresql://test_user:test_password@localhost:5432/agentfx_test \
  PYTHONPATH=src .venv/bin/python scripts/seed_test_data.py
```

This creates the standard test users:
- `test@example.com` / `TestPass123!` (viewer)
- `viewer@example.com` / `secretpassword` (viewer)
- `trader@example.com` / `traderpassword` (trader)
- `admin@example.com` / `adminpassword` (admin)

## Agentic Architecture

AgentFX is designed as an **autonomous strategy factory**. The core loop:

```
Discovery Agent → Optimizer Agent → Validator Agent → [Human Approval] → Paper → Live
       ↑                                                                        |
       └────────────── Evolution Agent (feedback loop) ←────────────────────────┘
```

### Agent Types

| Agent | Module | Purpose |
|-------|--------|---------|
| **Optimizer** | `src/agentfx/agents/optimizer.py` | Grid/Bayesian search over strategy params |
| **Validator** | `src/agentfx/agents/validator.py` | Walk-forward validation with Gate B thresholds |
| **Discovery** | `src/agentfx/agents/discovery.py` | Generate strategy hypothesis variations |
| **Evolution** | `src/agentfx/agents/evolution.py` | Detect drift and trigger re-optimization |
| **Orchestrator** | `src/agentfx/agents/orchestrator.py` | Coordinate the full pipeline |

### Gate B Thresholds (from MVP_CONSTRAINTS.md)

Strategies must pass these thresholds before paper trading:
- Sharpe ratio >= 1.0
- Max drawdown <= 10%
- Hit rate >= 48%
- Minimum 300 trades in out-of-sample period

## Architecture

### Backend Module Layout (`src/agentfx/`)

- **core/**: Domain logic and policies (pure, no I/O)
  - `policy.py` - Risk/exposure/order-rate checks pre-order
  - `strategy.py` - Strategy abstractions
  - `metrics.py` - Performance metrics calculations

- **adapters/**: External system integrations
  - `oanda.py` - OANDA broker adapter
  - `storage/` - Database/storage adapters

- **agents/**: Autonomous trading agents
  - `optimizer.py` - Parameter optimization agent
  - `validator.py` - Strategy validation agent
  - `discovery.py` - Strategy hypothesis generation
  - `evolution.py` - Performance drift detection
  - `orchestrator.py` - Pipeline coordination

- **services/**: Orchestration and business logic
  - `backtest.py` - Backtesting engine
  - `cli.py` - CLI commands (backfill, backtest)
  - `ingest.py` - Data ingestion service
  - `paper_runner.py` - Paper trading execution

- **api/**: FastAPI application
  - `main.py` - App entry point with lifespan management
  - `core/config.py` - Pydantic settings (env-based config)
  - `core/database.py` - SQLAlchemy setup
  - `routers/` - REST endpoints (auth, market_data, runs, strategies)
  - `services/` - API-specific services (market_data, market_data_stream)
  - `schemas/` - Pydantic request/response models
  - `models/` - SQLAlchemy ORM models

### Frontend (`portal/web/src/`)

React 18 + TypeScript + Vite + Tailwind CSS:
- `pages/` - Route components (MarketData, Dashboard, etc.)
- `components/` - Reusable UI components
- `services/` - API clients and WebSocket handlers
- `contexts/` - React context providers

### Test Organization (`tests/`)

Tests are organized by type and auto-marked based on directory:
- `unit/` - Fast, isolated tests (mocked dependencies)
- `integration/` - Tests with real dependencies (DB, external services)
- `e2e/` - Full-stack tests
- `performance/` - Performance benchmarks

Key markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.market_data`, `@pytest.mark.oanda_real`

## Configuration

Environment-based config via `.env.{environment}` files:
- `.env.development` - Local development
- `.env.test` - Test environment
- `.env.staging` / `.env.production` - Deployed environments

Set `ENVIRONMENT=development|test|staging|production` to select config file.

Key settings in `src/agentfx/api/core/config.py`:
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` - Broker credentials
- `DATABASE_URL` - PostgreSQL connection string
- `WEBSOCKET_ENABLED` - Toggle real-time market data streaming

## Planning System

The `planning/` directory contains project governance:

- **BACKLOG.md** - Version-grouped task list with requirement links
- **ROADMAP.md** - Agentic milestone releases (v0.1.0 → v1.0.0)
- **requirements/** - Executable user stories with:
  - Given-When-Then format
  - Verification commands (pytest, API tests)
  - API contracts
  - Files involved
- **architecture/** - PRINCIPLES.md, STANDARDS.md, NFRS.md
- **archive/** - Historical planning documents (FEAT-, ADR-, TECH-)

### User Story Format

Each user story in `planning/requirements/` follows this executable format:

```markdown
## US-XXX: [Title]

### Given-When-Then
GIVEN [preconditions]
WHEN [action/trigger]
THEN [expected outcome]

### Verification Commands
PYTHONPATH=src pytest tests/unit/agents/test_optimizer.py -v

### API Contract
- Endpoint: POST /api/v1/optimize
- Request: { ... }
- Response: { ... }

### Files Involved
- src/agentfx/agents/optimizer.py
- tests/unit/agents/test_optimizer.py
```

This format enables AI agents to:
1. Develop features aligned to requirements
2. Generate tests that verify requirements
3. Self-validate work before submitting

## Key Constraints

From MVP_CONSTRAINTS.md:
- Risk per trade: <= 0.5% of capital
- Daily loss limit: 2% (auto-halt)
- Max drawdown: 10% (system halt)
- Major FX pairs only (EURUSD, GBPUSD, USDJPY, etc.)
- Paper validation required before live deployment
- Human approval required for strategy promotion to live trading
