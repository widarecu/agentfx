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
make docker-up PROFILE=dev   # Dev stack (backend:8000, frontend:5173, postgres:5432)
make docker-up PROFILE=test  # Test stack (backend:8001, frontend:5174, postgres:5433) — wiped by `make e2e`
make docker-up PROFILE=uat   # UAT stack (backend:8002, frontend:5175, postgres:5434) — long-lived, NEVER run e2e here
make docker-down PROFILE=dev # Stop a stack
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

## Environment isolation

There are **three local Docker profiles**, each with its own containers, database volume, and port range. They are deliberately isolated so that running one cannot disturb the data in another. They serve different purposes and have different durability guarantees.

| Profile | Purpose | Backend | Frontend | Postgres (host) | Volume | Durability |
|---|---|---|---|---|---|---|
| **dev** | Developer verification (Claude + Wilson when iterating locally) | `:8000` | `:5173` | `:5432` | `postgres_data` | Long-lived; may be wiped on demand for clean rebuilds |
| **test** | Automated tests and E2E (`make e2e`, `make test-integration`) | `:8001` | `:5174` | `:5433` | `postgres_test_data` | **Ephemeral.** `scripts/run-e2e-tests.sh` wipes this volume with `docker compose down -v` on every E2E run |
| **uat** | User Acceptance Testing — Wilson's day-to-day exploratory stack | `:8002` | `:5175` | `:5434` | `postgres_uat_data` | **Long-lived.** Holds real OANDA backfills and the user's portal account. Must never be wiped automatically |

### Hard rule: never run `make e2e` against UAT or dev

`make e2e` invokes `scripts/run-e2e-tests.sh`, which begins with `docker compose down -v --remove-orphans` — that destroys the target profile's named volume. The script is **hard-coded** to refuse anything other than `PROFILE=test`; if you ever see yourself reaching for an override, stop and use the right profile instead.

**Historical incident (2026-05-27):** an E2E run executed while the test stack was also serving as a manual acceptance stack — the volume wipe destroyed 6 years of EUR_USD M1 OANDA candles and the user's portal account. The UAT profile and the guard in `run-e2e-tests.sh` exist specifically to make that incident un-repeatable. Do not weaken the guard.

### How to choose a profile

- Manual portal walkthroughs, exploratory evolution runs, looking at real OANDA-backed data the user has loaded → **uat**.
- Running `make test-integration`, `make e2e`, or anything CI-shaped that needs a fresh DB every time → **test**.
- Iterating on a backend code change with the full ingestion stack and watching logs → **dev**.

UAT credentials live in `.env.uat`; the file uses an `agentfx_uat` database with a distinct user/password from the other two profiles.

## Testing

**Local runtime is Docker-only.** Postgres (and the backend/frontend during E2E) run in containers via `docker-compose.yml`. Installing local PostgreSQL is no longer supported or required.

### CANONICAL COMMANDS - ALWAYS USE THESE

| Test Type | Command | What it uses |
|-----------|---------|--------------|
| **Unit tests** | `make test-unit` | **Both runtimes**: backend pytest (`-m unit`, mocked, no DB) **+** frontend Vitest (parity / classifier / oscillator routing, runs in node, no browser). Runs `verify-fixtures` first so a stale JS↔Python parity fixture fails loudly before either test leg starts. Both legs always run regardless of the other's outcome so the operator sees every failure in one pass. |
| **Integration tests** | `make test-integration` | Dockerised Postgres on host port **5433** (`docker-compose --profile test`) |
| **E2E tests** | `make e2e` | Full Dockerised stack: postgres-test (5433), backend-test (8001), frontend-test (5174) |
| **Specific file** | `make test-file FILE=path/to/test.py` | Depends on file |
| **Pattern match** | `make test-pattern PATTERN="test_name"` | Depends on matches |
| **Regenerate parity fixture** | `make dump-indicator-fixtures` | Re-runs `scripts/dump_indicator_fixtures.py` to refresh `portal/web/tests/fixtures/indicator-values.json` after editing `src/agentfx/core/dsl/indicators.py`. |
| **Check parity fixture is current** | `make verify-fixtures` | Re-runs the dump and `git diff --exit-code`s the fixture. Invoked automatically by `make test-unit`; also runs in CI before the backend unit job. |

`make` targets automatically set `ENVIRONMENT=test` and `PYTHONPATH=src`, so `.env.test` is loaded and host-run tooling points at the Dockerised Postgres on `localhost:5433`. The frontend Vitest leg of `make test-unit` runs through `npm test` inside `portal/web/` — same Node/npm the dev stack uses; no separate dev dependency.

### DO NOT USE - ANTI-PATTERNS

| Bad Command | Why It Fails |
|-------------|--------------|
| `pytest tests/unit/` | Missing `ENVIRONMENT=test`, loads wrong config |
| `pytest tests/integration/` | Missing `ENVIRONMENT=test`, connects to wrong database |
| `python -m pytest tests/` | Uses system Python, wrong dependencies |
| `PYTHONPATH=src pytest tests/` | Still missing `ENVIRONMENT=test` |
| `cd portal/web && npm test` | Bypasses `make test-unit`; skips the parity-fixture staleness guard (`verify-fixtures`), so JS tests can pass against an outdated Python reference. Use `make test-unit`. |
| `npx vitest run` / `npx vitest` | Same problem as the above — bypasses the staleness guard. Use `make test-unit`, or `cd portal/web && npm run test:watch` only during iterative TDD when you accept the staleness risk. |
| `python scripts/dump_indicator_fixtures.py` | Missing `ENVIRONMENT=test` + `PYTHONPATH=src`. Use `make dump-indicator-fixtures`. |
| `brew services start postgresql@15` | Local Postgres is deprecated — use Docker |
| Manual uvicorn + npm for E2E | Bypasses Docker stack, seeding, cleanup |

**If you must run pytest directly, use the FULL command:**
```bash
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest tests/unit/ -v
```

### Test Categories

| Category | Command | Runtime | Description |
|----------|---------|---------|-------------|
| **Unit (backend + frontend)** | `make test-unit` | Mocked (pytest) + node (Vitest) | Fast, isolated tests with mocked dependencies. Backend pytest (`-m unit`, no DB) **plus** frontend Vitest (DSL indicator parity + classifier + oscillator routing — no browser, no DB). `verify-fixtures` runs first to catch parity-fixture staleness. |
| **Integration** | `make test-integration` | Dockerised Postgres (5433) | Tests with a real database connection |
| **E2E** | `make e2e` | Full Docker stack | Playwright against the containerised frontend/backend |

### Synthetic data vs real data — when each is correct

Two different test categories that both touch "fake" data — be careful which one applies before generating fixtures.

| Pattern | When it's correct | When it's NOT correct | Example |
|---|---|---|---|
| **Synthetic deterministic fixture** | Pure-computation parity tests where the goal is "implementation A agrees with implementation B on the same input". Real market data adds no value because the test isn't about market shape. | Anything testing data-pipeline correctness, backtest realism, or strategy fitness against historical conditions. | `portal/web/tests/fixtures/indicator-values.json` — JS↔Python indicator parity. Generated by `scripts/dump_indicator_fixtures.py` from a fixed-seed sine + linear-ramp OHLCV construction; checked in so the frontend Vitest suite needs no Python at run time. Re-generate via `make dump-indicator-fixtures` whenever Python indicator semantics change; `verify-fixtures` (run by `make test-unit` and CI) catches staleness. |
| **Real OANDA data via `@pytest.mark.oanda_real`** | Integration tests of the data pipeline, strategy backtests, or anything where the realism of the input drives the assertion. | Pure-computation tests where deterministic input is required for reproducibility. | See "OANDA Integration Tests" below. |

**The `seed_test_candles` fixture remains forbidden** for integration testing — fake market-shaped candles cannot stand in for OANDA data. That rule is about integration realism, not about all synthetic data; parity fixtures (above) are a separate category.

### Prerequisites

**Docker Desktop (or Colima/OrbStack) must be running.** That is the only dependency for Integration and E2E tests.

```bash
docker info                # Verify the Docker daemon is up
docker compose version     # Verify Compose v2 is available
```

For E2E, ensure the Docker test ports are free (`docker compose --profile test down` if a previous run is still up):
```bash
lsof -i :5433 -i :8001 -i :5174
```

### Ingestion toggle: three-surface model

`INGESTION_ENABLED`, `BACKFILL_DAYS`, `CANDLE_INSTRUMENTS`, and `CANDLE_GRANULARITIES` live on three independent execution surfaces with one source of truth each — there is **no shared file** between them. This is the architectural rule; if you find yourself wanting to set ingestion config in two places, you've taken a wrong turn.

| # | Surface | What runs | Source of truth | Value |
|---|---------|-----------|-----------------|-------|
| 1 | **Pytest** (host) | `make test-unit` / `make test-integration` / `make e2e` host-side | [tests/conftest.py](tests/conftest.py) — `os.environ["INGESTION_ENABLED"] = "false"` set at module load, before any settings import | always `false` (invariant — TestClient lifespan hangs on backfill otherwise) |
| 2 | **Local Docker dev/test profile** | `backend` / `backend-test` container | [docker-compose.yml](docker-compose.yml) `environment:` block with `${VAR:-default}` substitution | shell-overridable per invocation — defaults: dev `INGESTION_ENABLED=true BACKFILL_DAYS=1825`; test `INGESTION_ENABLED=false BACKFILL_DAYS=7` |
| 3 | **Railway** | Production / staging container | Railway service-variables dashboard | dashboard-set (typically `true` in prod, with full `BACKFILL_DAYS`) |

Why this matters: each surface has different correctness requirements (pytest must never backfill; E2E must be deterministic; production must ingest live). Funnelling all three through the same `.env.test` file forced a shared name with conflicting needs — that's the trap we're avoiding here.

**Practical commands:**

```bash
# E2E (default — ingestion off, synthetic candles). Test volume gets wiped
# every `make e2e` run, so don't backfill real data here.
make docker-up PROFILE=test

# Acceptance/exploratory — use the UAT profile (long-lived volume, never
# touched by `make e2e`). Defaults to ingestion on and 6-year backfill.
make docker-up PROFILE=uat

# Targeted UAT backfill (just EUR_USD M1, 6 years) — narrow scope to avoid
# an hour of unnecessary OANDA traffic
CANDLE_INSTRUMENTS=EUR_USD CANDLE_GRANULARITIES=M1 \
  make docker-up PROFILE=uat BACKFILL_DAYS=2190
```

`.env.test` and `.env.development` deliberately do **not** contain any of these four variables — putting them there shadows surface 2 and re-creates the very confusion this model eliminates. The Pydantic defaults in [api/core/config.py](src/agentfx/api/core/config.py) are the base; compose's substitution defaults mirror them so a reader of compose alone sees what the container will use.

### Running Unit Tests

```bash
make test-unit
# Or with verbose output:
make test-file FILE=tests/unit/market_data/test_market_data.py
```

### Running Integration Tests

Integration tests hit a real Postgres running inside Docker. Bring up the `test` profile stack (or at least `postgres-test`), then run pytest:

```bash
make docker-up PROFILE=test   # Starts postgres-test on host port 5433 (plus backend-test / frontend-test)
make test-integration
make docker-down PROFILE=test
```

`make test-integration` reads `.env.test` which points `DATABASE_URL` at `localhost:5433`, so no extra env setup is needed.

### Running E2E Tests

`make e2e` owns the full lifecycle — it builds and starts the `test` profile stack, seeds test users inside the `backend-test` container, runs Playwright on the host against the containerised frontend, then tears the stack down on exit:

```bash
make e2e                 # Standard run (stack up → seed → tests → stack down)
make e2e E2E_HEADED=1    # Show the browser
make e2e E2E_DEBUG=1     # Playwright debug mode
```

To keep the stack up after tests (useful for iterating on specs without rebuilding containers each time):
```bash
./scripts/run-e2e-tests.sh --keep
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
- Cause: Docker test Postgres not running
- Fix: `make docker-up PROFILE=test`

**Symptom: E2E fails immediately with "Cannot connect to the Docker daemon"**
- Cause: Docker Desktop not running
- Fix: start Docker Desktop, then re-run `make e2e`

**Symptom: E2E fails at startup with "port is already allocated" on 5433/8001/5174**
- Cause: a previous test stack is still up, or another service is bound to the port
- Fix: `make docker-down PROFILE=test` (or `docker compose --profile test down --remove-orphans`)

**Symptom: "relation 'users' does not exist"**
- Cause: Migrations didn't complete — the `backend-test` container's entrypoint runs `alembic upgrade head`; if you see this, the container didn't finish startup
- Fix: `make docker-logs PROFILE=test` and look for migration errors

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

Everything runs in Docker. The containers use the **same image** deployed to Railway, so local work stays in parity with production. Credentials are loaded from `.env.development` (dev profile) or `.env.test` (test profile) automatically.

```bash
# Development stack — full stack with ingestion worker
make docker-up PROFILE=dev     # backend:8000, frontend:5173, postgres:5432
make docker-down PROFILE=dev
make docker-logs PROFILE=dev          # Follow all logs
make docker-backend-logs PROFILE=dev  # Backend logs only

# Test stack — separate DB, separate ports (no conflicts with dev)
make docker-up PROFILE=test    # backend-test:8001, frontend-test:5174, postgres-test:5433
make docker-down PROFILE=test

# Rebuild after code changes
make docker-rebuild PROFILE=dev
```

**Important:** Dev and test profiles are fully isolated — different containers, databases, and host ports. They can run simultaneously if you need both.

`docker-compose.yml` defines both profiles:
- **dev**: Loads `.env.development`, ingestion enabled, database `agentfx_dev`
- **test**: Loads `.env.test`, database `agentfx_test`, `scripts/` is bind-mounted read-only into `backend-test` so test-only seed scripts (`seed_test_data.py`, `seed_test_candles.py`) are executable via `docker compose exec backend-test python scripts/…` without shipping them in the production image.

### Seed Test Data (Before E2E Tests)

`make e2e` handles this automatically. If you bring the test stack up yourself and need to seed test users manually:

```bash
docker compose --profile test exec backend-test python scripts/seed_test_data.py
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
1. E2E tests run (currently not a deploy blocker — `needs: e2e` is commented out on the backend/frontend deploy jobs).
2. Backend deploys to Railway (`railway up --detach --service agentfx`).
3. Frontend deploys to Vercel (`vercel deploy --prod`).

**Note (CI E2E vs local E2E):** The CI E2E job still provisions Postgres via a GitHub Actions service container on port 5432 and runs uvicorn/vite directly on host ports 5173/8000 — it does **not** yet use `docker compose --profile test` like the local flow. The workflow sets `PLAYWRIGHT_BASE_URL=http://localhost:5173` and `API_BASE_URL=http://localhost:8000/api/v1` to pin Playwright to those ports (the config default is the Docker test profile on 5174/8001). Migrating CI to `make e2e` for a single code path is a pending follow-up.

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
- `.env.uat` - User acceptance testing (loaded by Docker uat profile — Wilson's long-lived exploratory stack)
- Railway/Vercel - Environment variables set in platform dashboards, not `.env` files

Set `ENVIRONMENT=development|test|uat|staging|production` to select config file. Pydantic Settings in `src/agentfx/api/core/config.py` loads the matching `.env.{environment}` file. Environment variables override `.env` file values.

Key settings:
- `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` - Broker credentials
- `DATABASE_URL` - PostgreSQL connection string (`localhost` for venv, `postgres` service name for Docker)
- `INGESTION_ENABLED` - Enable/disable candle ingestion worker
- `BACKFILL_DAYS` - Days of historical candle data to backfill (7 for dev, 1825 for 5 years)
- `DEBUG` - `true` for DEBUG-level logging, `false` for INFO-level

## Work Tracking (Linear) — rules for every agent

**Linear is the source of truth for work items** (workspace `agentfx`, team key `AFX`). Use the `linear-server` MCP tools. The full operating model is in [planning/runbooks/linear-operating-model.md](planning/runbooks/linear-operating-model.md). Wilson is the only human and the only approver.

States: `Triage` → `Backlog` → `Ready` → `In Progress` → `In Review` → `Done` (plus `Canceled` / `Duplicate`).

**Filing issues**
1. Search first. If an open issue covers the same problem (or carries the same `Fingerprint:`), comment with the new evidence instead of filing.
2. File into **Triage** only. Use the matching template (Feature / Bug / Agent finding / Test gap), one label each from **Type**, **Area**, **Source** and **Risk**, plus the `agent-filed` flag. Bugs also get **Found in**.
3. Max 5 new issues per agent per day.
4. Never set priority, move an issue out of Triage, or change the scope of an approved issue. Propose changes as a comment or a new Triage issue.

**Working issues**
5. Only pick up issues in **Ready**, highest priority first. Make yourself the delegate (Wilson stays assignee) and move the issue to **In Progress**.
6. Branch `agent/AFX-<n>-<slug>`, and put `AFX-<n>` in the PR title so Linear links it.
7. Before opening the PR, `make lint` and `make test-unit` must be green. Comment on the issue with what was tested and the CI link.
8. **Risk: Money-path** (order execution, sizing, kill switch, credentials, deploy gates) → the PR never auto-merges. Add `needs-wilson`.
9. When blocked on a product decision, comment the question, add `needs-wilson`, and stop. Don't guess.
10. All other rules in this file still apply: `test` profile only, practice OANDA keys only, never push to `main`.

A bug found anywhere other than CI means a test was missing. File a **Test gap** issue alongside the fix.

## Planning System

`planning/BACKLOG.md` is a **frozen snapshot** as of 2026-10-22. Open items were imported to Linear. Don't add new items there. ROADMAP.md stays the narrative of milestones; Linear projects mirror its releases.

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

### Backlog hygiene — mandatory

The backlog is only useful if it matches reality. Two rules:

1. **When you ship a work item, close it in the SAME change.** For
   Linear issues this is automatic: `AFX-<n>` in the PR title moves it
   to Done on merge. For legacy BACKLOG.md items, flip the checkbox in the SAME
   commit. Don't defer the doc update. `[ ] TODO` → `[x] DONE`
   goes in the diff alongside the code change. If the change is
   spread across many commits, flip the checkbox in the last one
   that closes the item. Never leave "will update backlog later"
   as a mental TODO — the backlog rot from that pattern is what
   made Wilson lose an hour on 2026-08-02 when I quoted Slack
   notifications as unshipped (the code + tests + Railway env
   were all live; only the backlog line was stale).

2. **Before recommending or scheduling work, VERIFY the item is
   actually open.** The backlog is a starting point, not the
   final answer. For anything that touches shipped code, grep
   the codebase first:

     - Slack alerts → `ls src/agentfx/notifications/slack.py`
     - Env var → `grep -n VAR src/agentfx/api/core/config.py`
     - UI component → `ls portal/web/src/components/...`
     - Endpoint → `grep -rn "@router.get.*/path" src/`

   A shipped feature with an unchecked backlog line is common;
   don't be the one who re-plans it.

Both rules apply to Wilson and to Claude Code sessions equally.

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
