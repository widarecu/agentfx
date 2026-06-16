PY?=python3
VENV?=.venv
PIP?=$(VENV)/bin/pip
PYBIN?=$(VENV)/bin/python

# Backtest defaults (can be overridden at invocation)
STRATEGY?=ma
FAST?=20
SLOW?=50
SPREAD?=0.5
SLIP?=0.5
MAXABS?=1
MAXRATE?=60
LOSS?=0.02

.PHONY: venv install dev lock update precommit test test-unit test-integration test-e2e dump-indicator-fixtures verify-fixtures lint typecheck format backtest backtest-full portal-mock portal-mock-test portal-web-e2e start-backend start-frontend start-all e2e docker-up docker-down docker-logs docker-backend-logs docker-restart docker-rebuild

venv:
	$(PY) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv
	$(PIP) install -r requirements.txt

lock: venv
	$(PIP) install pip-tools
	$(VENV)/bin/pip-compile -o requirements.txt requirements.in
	$(VENV)/bin/pip-compile -o requirements-dev.txt requirements-dev.in

update: venv
	$(PIP) install pip-tools
	$(VENV)/bin/pip-compile --upgrade -o requirements.txt requirements.in
	$(VENV)/bin/pip-compile --upgrade -o requirements-dev.txt requirements-dev.in

dev: install
	$(PIP) install -r requirements-dev.txt
	$(VENV)/bin/pre-commit install

precommit:
	$(VENV)/bin/pre-commit run --all-files

# ============================================================================
# TEST TARGETS
# ============================================================================
# Test execution requires proper environment configuration:
# - Unit tests: ENVIRONMENT=test (uses .env.test but mocks database)
# - Integration tests: ENVIRONMENT=test (uses .env.test with real PostgreSQL)
# - E2E tests: Requires running backend/frontend servers (use 'make e2e')
#
# The ENVIRONMENT variable tells config.py which .env.* file to load.
# ============================================================================

# Run all tests (unit + integration). Unit = backend pytest + frontend
# vitest (parity / classifier / oscillator routing). Integration = pytest
# against Dockerised Postgres on 5433.
# Note: For E2E tests, use 'make e2e' which handles server lifecycle.
test: test-unit test-integration

# Unit tests: Fast, isolated tests with mocked dependencies.
# Covers BOTH runtimes — there is no separate frontend stage:
#   - backend pytest (-m unit) — no DB, no Docker, mocked.
#   - frontend vitest — parity tests for the JS DSL indicator
#     implementations against a Python-generated fixture, archetype
#     classifier, oscillator routing. Runs in node, no DB, no browser.
# verify-fixtures runs first so a stale parity fixture fails loudly
# rather than letting the JS tests pass against outdated Python output.
# Both runtimes always run regardless of the other's outcome so the
# operator sees every failure in one pass (a backend coverage gripe
# does not silently skip frontend tests, etc.).
test-unit: verify-fixtures
	@set +e; rc=0; \
	  echo "--- backend pytest ---"; \
	  ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -q -m unit || rc=$$?; \
	  echo "--- frontend vitest ---"; \
	  (cd portal/web && npm test --silent) || rc=$$?; \
	  exit $$rc

# Regenerate the JS↔Python indicator parity fixture from the Python
# reference implementation. Re-run whenever
# src/agentfx/core/dsl/indicators.py changes; the JSON is checked in so
# the frontend test suite does not need Python at run time.
dump-indicator-fixtures:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/python scripts/dump_indicator_fixtures.py

# Guard against fixture staleness: regenerate the parity fixture and
# fail if it changed. Catches "someone edited indicators.py without
# re-running dump-indicator-fixtures", which would otherwise let the
# frontend parity tests pass against an outdated Python reference.
verify-fixtures: dump-indicator-fixtures
	@git diff --exit-code -- portal/web/tests/fixtures/indicator-values.json \
	  || (echo "ERROR: parity fixture is stale. Run 'make dump-indicator-fixtures' \
and commit the regenerated portal/web/tests/fixtures/indicator-values.json." && exit 1)

# Integration tests: Tests with real database (Dockerised PostgreSQL on port 5433)
# Requires: docker-compose --profile test up first.
# Optional extra pytest args via PYTEST_ARGS, e.g.:
#   make test-integration PYTEST_ARGS="-v --timeout=60"
test-integration:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -q -m integration $(PYTEST_ARGS)

# E2E tests via pytest markers (for individual test runs)
# Note: Prefer 'make e2e' for full E2E suite with server management
test-e2e:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -q -m e2e

# Run tests with verbose output (useful for debugging)
test-verbose:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -v --ignore=tests/e2e

# Run tests without coverage (faster for quick checks)
test-fast:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -q --no-cov --ignore=tests/e2e

# Run a specific test file
# Usage: make test-file FILE=tests/unit/market_data/test_market_data.py
test-file:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -v $(FILE)

# Run tests matching a pattern
# Usage: make test-pattern PATTERN="test_auth"
test-pattern:
	ENVIRONMENT=test PYTHONPATH=src $(VENV)/bin/pytest -v -k "$(PATTERN)" --ignore=tests/e2e

lint:
	$(VENV)/bin/ruff .
	$(VENV)/bin/black --check .

typecheck:
	$(VENV)/bin/mypy src || true

format:
	$(VENV)/bin/black .

# --- .env workflow and smoke run ---
.PHONY: dotenv run-backfill smoke

dotenv:
	@test -f .env.development || cp .env.example .env.development

# Usage: make run-backfill PAIRS="AUDUSD,EURUSD" DAYS=2
run-backfill: dev dotenv
	set -a; \
	export ENVIRONMENT=development; \
	source .env.development; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backfill --pairs $(PAIRS) --start-days $(DAYS)

# Convenience smoke for AUDUSD,EURUSD over 2 days
smoke:
	$(MAKE) run-backfill PAIRS="AUDUSD,EURUSD" DAYS=2

# Minimal backtest helper (uses env defaults for costs/policy)
# Usage: make backtest PAIRS="AUDUSD,EURUSD" DAYS=2 [PERSIST=1]
backtest: dev dotenv
	set -a; \
	export ENVIRONMENT=development; \
	source .env.development; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backtest --pairs "$(PAIRS)" --start-days $(DAYS) $(if $(PERSIST),--persist,)

# Full backtest helper with explicit flags (overrides env defaults)
# Usage: make backtest-full PAIRS="AUDUSD,EURUSD" DAYS=2 STRATEGY=ma FAST=20 SLOW=50 SPREAD=0.5 SLIP=0.5 MAXABS=1 MAXRATE=60 LOSS=0.02 [PERSIST=1]
backtest-full: dev dotenv
	set -a; \
	export ENVIRONMENT=development; \
	source .env.development; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backtest --pairs "$(PAIRS)" --start-days $(DAYS) --strategy $(STRATEGY) --fast $(FAST) --slow $(SLOW) --spread-pips $(SPREAD) --slippage-pips $(SLIP) --max-abs-position $(MAXABS) --max-trades-per-hour $(MAXRATE) --daily-loss-threshold $(LOSS) $(if $(PERSIST),--persist,)

# Run the FastAPI mock API for the portal
# Usage: make portal-mock [HOST=127.0.0.1 PORT=8000]
portal-mock: dev
	export ENVIRONMENT=development; \
	PYTHONPATH=src $(VENV)/bin/uvicorn agentfx.portal_mock.app:app --host $${HOST-127.0.0.1} --port $${PORT-8000} --reload

# Run only the portal mock API tests
portal-mock-test: dev
	PYTHONPATH=src $(VENV)/bin/pytest -q tests/test_portal_mock_api.py

# Run frontend E2E tests (requires Vite dev server and mock API running)
portal-web-e2e:
	cd portal/web && BASE_URL=http://localhost:5173 npx playwright test -c playwright.config.ts

# --- Server Startup ---
# Start backend server (development mode)
# Usage: make start-backend [ENV=development]
ENV?=development
start-backend:
	ENVIRONMENT=$(ENV) ./scripts/start-backend.sh

# Start frontend server (development mode)
# Usage: make start-frontend [ENV=development]
start-frontend:
	ENVIRONMENT=$(ENV) ./scripts/start-frontend.sh

# Start both backend and frontend servers
# Usage: make start-all [ENV=development]
start-all:
	ENVIRONMENT=$(ENV) ./scripts/start-all.sh

# --- Docker Development ---
# Run the full stack in Docker (same image as Railway)
#
# Usage:
#   make docker-up PROFILE=dev    # Development stack (ports 8000/5173/5432)
#   make docker-up PROFILE=test   # Test stack (ports 8001/5174/5433, ingestion OFF — E2E default)
#   make docker-up PROFILE=uat    # UAT stack (ports 8002/5175/5434, ingestion ON, 6y backfill default)
#
# Credentials loaded from .env.development / .env.test / .env.uat automatically.
# The INGESTION_ENABLED / BACKFILL_DAYS toggles are read by docker-compose.yml
# via ${VAR:-default} substitution, so the same compose file serves all
# profiles with profile-appropriate defaults.
#
# Environment isolation rules (see CLAUDE.md → "Environment isolation"):
#   • test → wiped by `make e2e` every run; never store long-lived data here.
#   • uat  → long-lived; NEVER run `make e2e` against this profile.
#            scripts/run-e2e-tests.sh has a guard that refuses to run
#            anywhere other than the `test` profile.
PROFILE?=dev
INGESTION_ENABLED?=false
BACKFILL_DAYS?=7

# Resolve the right backend service name per profile (used by logs/restart/rebuild).
BACKEND_SERVICE=$(if $(filter test,$(PROFILE)),backend-test,$(if $(filter uat,$(PROFILE)),backend-uat,backend))

docker-up:
	INGESTION_ENABLED=$(INGESTION_ENABLED) BACKFILL_DAYS=$(BACKFILL_DAYS) docker compose --profile $(PROFILE) up --build -d

docker-down:
	docker compose --profile $(PROFILE) down

docker-logs:
	docker compose --profile $(PROFILE) logs -f

docker-backend-logs:
	docker compose logs -f $(BACKEND_SERVICE)

docker-restart:
	docker compose restart $(BACKEND_SERVICE)

docker-rebuild:
	INGESTION_ENABLED=$(INGESTION_ENABLED) BACKFILL_DAYS=$(BACKFILL_DAYS) docker compose --profile $(PROFILE) up --build -d $(BACKEND_SERVICE)

# --- E2E Testing ---
# Run full E2E test suite (starts servers, runs tests, cleans up)
# Usage: make e2e [E2E_HEADED=1] [E2E_DEBUG=1]
e2e:
	./scripts/run-e2e-tests.sh $(if $(E2E_HEADED),--headed,) $(if $(E2E_DEBUG),--debug,)
