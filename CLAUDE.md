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
# Environment setup (venv-based, for local dev without Docker)
make venv          # Create virtual environment
make install       # Install production dependencies
make dev           # Install dev dependencies + pre-commit hooks

# Docker development (preferred — matches Railway deployment)
make docker-up PROFILE=dev   # Start dev stack (backend:8000, frontend:5173, postgres:5432)
make docker-up PROFILE=test  # Start test stack (backend:8001, frontend:5174, postgres:5433)
make docker-down PROFILE=dev # Stop dev stack
make docker-logs PROFILE=dev # Follow logs
make docker-rebuild PROFILE=dev # Rebuild and restart backend

# Code quality
make lint          # Run ruff + black --check
make format        # Run black formatter
make typecheck     # Run mypy on src/

# Backtest
make backtest PAIRS="AUDUSD,EURUSD" DAYS=2
make backtest-full PAIRS="AUDUSD,EURUSD" DAYS=2 STRATEGY=ma FAST=20 SLOW=50
```

## Testing

### CANONICAL COMMANDS - ALWAYS USE THESE

| Test Type | Command | Database Required |
|-----------|---------|-------------------|
| **Unit tests** | `make test-unit` | No |
| **Integration tests** | `make test-integration` | Yes (PostgreSQL:5432) |
| **E2E tests** | `make e2e` | Yes + Servers (managed) |
| **All tests** | `make test` | Yes (PostgreSQL:5432) |
| **Specific file** | `make test-file FILE=path/to/test.py` | Depends on file |
| **Pattern match** | `make test-pattern PATTERN="test_name"` | Depends on matches |

**Why Makefile:** All `make` commands automatically set `ENVIRONMENT=test` and `PYTHONPATH=src`. This ensures the correct `.env.test` configuration is loaded.

### DO NOT USE - ANTI-PATTERNS

**These commands will cause configuration problems:**

| Bad Command | Why It Fails |
|-------------|--------------|
| `pytest tests/unit/` | Missing `ENVIRONMENT=test`, loads wrong config |
| `pytest tests/integration/` | Missing `ENVIRONMENT=test`, connects to wrong database |
| `python -m pytest tests/` | Uses system Python, wrong dependencies |
| `PYTHONPATH=src pytest tests/` | Still missing `ENVIRONMENT=test` |
| Manual uvicorn + npm for E2E | Bypasses migrations, seeding, cleanup |

**If you must run pytest directly, use the FULL command:**
```bash
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest tests/unit/ -v
```

### Test Categories

| Category | Command | Database Required | Description |
|----------|---------|-------------------|-------------|
| **Unit** | `make test-unit` | No (mocked) | Fast, isolated tests with mocked dependencies |
| **Integration** | `make test-integration` | Yes (PostgreSQL) | Tests with real database connections |
| **E2E** | `make e2e` | Yes + Servers | Full-stack tests with Playwright |

### Prerequisites

**For Integration and E2E tests, PostgreSQL must be running:**
```bash
# Verify PostgreSQL is accessible
psql -U test_user -d agentfx_test -h localhost -p 5432 -c "SELECT 1"

# If not running, start it:
brew services start postgresql@15
```

**For E2E tests, ensure ports are free:**
```bash
lsof -i :8000  # Should show nothing
lsof -i :5173  # Should show nothing

# Kill stuck processes if needed:
kill -9 $(lsof -t -i:8000) 2>/dev/null || true
kill -9 $(lsof -t -i:5173) 2>/dev/null || true
```

### Running Unit Tests

```bash
make test-unit
# Or with verbose output:
make test-file FILE=tests/unit/market_data/test_market_data.py
```

### Running Integration Tests

```bash
# Ensure PostgreSQL is running first, then:
make test-integration
```

### Running E2E Tests

The `make e2e` target handles server lifecycle automatically:

```bash
make e2e                 # Standard run
make e2e E2E_HEADED=1    # Show browser
make e2e E2E_DEBUG=1     # Debug mode
```

### Running Specific Tests

```bash
# Single test file
make test-file FILE=tests/unit/auth/test_security.py

# Tests matching a pattern
make test-pattern PATTERN="test_login"
```

### Troubleshooting Tests

**Symptom: "ModuleNotFoundError: No module named 'agentfx'"**
- Cause: `PYTHONPATH` not set
- Fix: Use `make test-unit` (sets automatically)

**Symptom: Tests use wrong database / see development data**
- Cause: `ENVIRONMENT=test` not set, config loaded `.env.development`
- Fix: Use `make test-*` commands (set automatically)

**Symptom: "could not connect to server" on integration tests**
- Cause: PostgreSQL not running
- Fix: `brew services start postgresql@15`

**Symptom: E2E tests fail with "Target closed" / "Connection refused"**
- Cause: Ports 8000/5173 blocked by previous processes
- Fix: Kill processes and retry:
  ```bash
  kill -9 $(lsof -t -i:8000) 2>/dev/null || true
  kill -9 $(lsof -t -i:5173) 2>/dev/null || true
  make e2e
  ```

**Symptom: "relation 'users' does not exist"**
- Cause: Migrations not applied
- Fix: `ENVIRONMENT=test .venv/bin/alembic upgrade head`

### Test Markers

Tests are auto-marked based on directory location:

```python
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.market_data
@pytest.mark.oanda_real   # Requires real OANDA API connection
@pytest.mark.slow         # Long-running tests
```

## Test Data Strategy

### Data by Test Type

| Test Type | Market Data | User Data | Database |
|-----------|-------------|-----------|----------|
| **Unit** | Mocked fixtures | In-memory fixtures | None (mocked) |
| **Integration** | Real OANDA API | Fixtures or seeded | Real PostgreSQL |
| **E2E** | Real OANDA API | Seeded via script | Real PostgreSQL |

### Seed Scripts

| Script | Purpose | What It Creates |
|--------|---------|-----------------|
| `seed_instruments.py` | **Metadata only** (symbol names, pip locations) | 6 FX pairs: EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CHF, USD_CAD |
| `seed_test_data.py` | Test user accounts for E2E auth | 5 users with different roles |

**IMPORTANT:** These scripts create **metadata and users only**, NOT price/candle data. Market data comes from OANDA API.

### OANDA Integration Tests

Tests that need real market data should:
1. Use `@pytest.mark.oanda_real` marker
2. Fetch live data from OANDA API
3. Require valid OANDA credentials in `.env.test`

```python
@pytest.mark.oanda_real
def test_fetch_live_prices():
    """This test calls real OANDA API - requires network + credentials."""
    ...
```

Run OANDA-dependent tests: `make test-integration` (includes oanda_real tests)
Skip OANDA tests: `ENVIRONMENT=test PYTHONPATH=src pytest -m "not oanda_real"`

### DO NOT USE for OANDA Integration

- `seed_test_candles` fixture - Creates synthetic/fake candles
- Hardcoded price data in tests
- Any mocked market data for integration tests

These defeat the purpose of integration testing against real OANDA.

### Test Users (E2E)

| Email | Password | Role |
|-------|----------|------|
| test@example.com | TestPass123! | VIEWER |
| viewer@example.com | secretpassword | VIEWER |
| trader@example.com | traderpassword | TRADER |
| admin@example.com | adminpassword | ADMIN |

## Starting Servers

### Docker (Preferred)

Docker runs the **same image** deployed to Railway, ensuring environment parity. All credentials are loaded from `.env.development` or `.env.test` automatically.

```bash
# Development stack — full stack with ingestion worker
make docker-up PROFILE=dev     # Start (backend:8000, frontend:5173, postgres:5432)
make docker-down PROFILE=dev   # Stop
make docker-logs PROFILE=dev   # Follow all logs
make docker-backend-logs PROFILE=dev  # Backend logs only

# Test stack — separate DB, separate ports
make docker-up PROFILE=test    # Start (backend:8001, frontend:5174, postgres:5433)
make docker-down PROFILE=test  # Stop

# Rebuild after code changes
make docker-rebuild PROFILE=dev
```

**Important:** Dev and test profiles are fully isolated — different containers, databases, and ports. Only one profile's services start at a time.

The `docker-compose.yml` defines both profiles:
- **dev**: Loads `.env.development`, ingestion enabled, PostgreSQL `agentfx_dev`
- **test**: Loads `.env.test`, separate PostgreSQL `agentfx_test` on port 5433

### Venv (Alternative — for fast iteration with hot-reload)

Use the shell scripts when you need hot-reload or want to run individual components:

```bash
# Development environment (default)
ENVIRONMENT=development ./scripts/start-backend.sh
ENVIRONMENT=development ./scripts/start-all.sh

# Test environment
ENVIRONMENT=test ./scripts/start-backend.sh
```

The startup scripts handle environment selection, database checks, migrations, and venv activation. **Do NOT start uvicorn or npm dev directly** — it bypasses configuration and migrations.

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

## Deployment

### Infrastructure

| Component | Platform | URL |
|-----------|----------|-----|
| **Backend** | Railway | `https://agentfx-staging.up.railway.app` |
| **Frontend** | Vercel | `https://agentfx-liart.vercel.app` |
| **Database** | Railway PostgreSQL | Internal (via `DATABASE_URL`) |

### CI/CD Pipeline

Defined in `.github/workflows/deploy.yml`. On push to `main`:
1. E2E tests run (currently disabled, re-enable with `needs: e2e`)
2. Backend deploys to Railway (`railway up --detach --service agentfx`)
3. Frontend deploys to Vercel (`vercel deploy --prod`)

### Dockerfile

The backend `Dockerfile` is the single source of truth for the production image. It runs:
1. `alembic upgrade head` — apply migrations
2. `python scripts/seed_instruments.py` — seed FX pair metadata
3. `uvicorn agentfx.api.main:app` — start the API server

The same Dockerfile is used by `docker-compose.yml` locally, ensuring parity.

### Railway Configuration

Environment variables are set in Railway's dashboard (not `.env` files). Key variables:
- `DATABASE_URL` — injected by Railway PostgreSQL plugin
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` — OANDA credentials
- `BACKEND_CORS_ORIGINS` — must include the Vercel frontend domain
- `BACKFILL_DAYS` — controls historical data depth (default 7)
- `DEBUG` — set to `false` for INFO-level logging

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
- `.env.development` - Local development (loaded by Docker dev profile and venv scripts)
- `.env.test` - Test environment (loaded by Docker test profile and E2E scripts)
- Railway/Vercel - Environment variables set in platform dashboards, not `.env` files

Set `ENVIRONMENT=development|test|staging|production` to select config file. Pydantic Settings in `src/agentfx/api/core/config.py` loads the matching `.env.{environment}` file. Environment variables override `.env` file values.

Key settings:
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` - Broker credentials
- `DATABASE_URL` - PostgreSQL connection string (`localhost` for venv, `postgres` service name for Docker)
- `INGESTION_ENABLED` - Enable/disable candle ingestion worker
- `BACKFILL_DAYS` - Days of historical candle data to backfill (7 for dev, 1825 for 5 years)
- `DEBUG` - `true` for DEBUG-level logging, `false` for INFO-level

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
