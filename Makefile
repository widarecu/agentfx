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

.PHONY: venv install dev lock update precommit test test-unit test-integration test-e2e lint typecheck format backtest backtest-full portal-mock portal-mock-test portal-web-e2e

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

test:
	PYTHONPATH=src $(VENV)/bin/pytest -q

# Run only unit/integration/e2e tests (via markers)
test-unit:
	PYTHONPATH=src $(VENV)/bin/pytest -q -m unit

test-integration:
	PYTHONPATH=src $(VENV)/bin/pytest -q -m integration

test-e2e:
	PYTHONPATH=src $(VENV)/bin/pytest -q -m e2e

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
	@test -f .env || cp .env.example .env

# Usage: make run-backfill PAIRS="AUDUSD,EURUSD" DAYS=2
run-backfill: dev dotenv
	set -a; \
	source .env; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backfill --pairs $(PAIRS) --start-days $(DAYS)

# Convenience smoke for AUDUSD,EURUSD over 2 days
smoke:
	$(MAKE) run-backfill PAIRS="AUDUSD,EURUSD" DAYS=2

# Minimal backtest helper (uses env defaults for costs/policy)
# Usage: make backtest PAIRS="AUDUSD,EURUSD" DAYS=2 [PERSIST=1]
backtest: dev dotenv
	set -a; \
	source .env; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backtest --pairs "$(PAIRS)" --start-days $(DAYS) $(if $(PERSIST),--persist,)

# Full backtest helper with explicit flags (overrides env defaults)
# Usage: make backtest-full PAIRS="AUDUSD,EURUSD" DAYS=2 STRATEGY=ma FAST=20 SLOW=50 SPREAD=0.5 SLIP=0.5 MAXABS=1 MAXRATE=60 LOSS=0.02 [PERSIST=1]
backtest-full: dev dotenv
	set -a; \
	source .env; \
	set +a; \
	PYTHONPATH=src $(PYBIN) -m agentfx.services.cli backtest --pairs "$(PAIRS)" --start-days $(DAYS) --strategy $(STRATEGY) --fast $(FAST) --slow $(SLOW) --spread-pips $(SPREAD) --slippage-pips $(SLIP) --max-abs-position $(MAXABS) --max-trades-per-hour $(MAXRATE) --daily-loss-threshold $(LOSS) $(if $(PERSIST),--persist,)

# Run the FastAPI mock API for the portal
# Usage: make portal-mock [HOST=127.0.0.1 PORT=8000]
portal-mock: dev
	PYTHONPATH=src $(VENV)/bin/uvicorn agentfx.portal_mock.app:app --host $${HOST-127.0.0.1} --port $${PORT-8000} --reload

# Run only the portal mock API tests
portal-mock-test: dev
	PYTHONPATH=src $(VENV)/bin/pytest -q tests/test_portal_mock_api.py

# Run frontend E2E tests (requires Vite dev server and mock API running)
portal-web-e2e:
	cd portal/web && BASE_URL=http://localhost:5173 npx playwright test -c playwright.config.ts
