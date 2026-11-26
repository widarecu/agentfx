---
paths:
  - "tests/**"
  - "portal/web/tests/**"
  - "pytest.ini"
  - "scripts/run-e2e-tests.sh"
  - "scripts/dump_indicator_fixtures.py"
  - "scripts/seed_*.py"
---

# Testing

Always run tests through `make`. The targets set `ENVIRONMENT=test` and `PYTHONPATH=src`, so `.env.test` is loaded and host tooling points at the Dockerised Postgres on `localhost:5433`. Docker must be running for integration and E2E.

| Need | Command | Notes |
|---|---|---|
| Unit (backend + frontend) | `make test-unit` | Runs `verify-fixtures` first, then pytest `-m unit` (mocked, no DB) and the portal Vitest suite (node, no browser). Both legs always run so every failure shows in one pass |
| Integration | `make test-integration` | Real Postgres: `make docker-up PROFILE=test` first |
| E2E | `make e2e` | Owns the whole lifecycle: builds the test stack, seeds users, runs Playwright, tears down. `E2E_HEADED=1` or `E2E_DEBUG=1` for a browser. `./scripts/run-e2e-tests.sh --keep` leaves the stack up |
| One file / pattern | `make test-file FILE=...` / `make test-pattern PATTERN=...` | |
| Indicator parity fixture | `make dump-indicator-fixtures` after editing `src/agentfx/core/dsl/indicators.py`; `make verify-fixtures` to check | CI also runs `verify-fixtures` |

**Never** run bare `pytest`, `python -m pytest`, `uv run pytest`, `npx vitest`, `cd portal/web && npm test` or `python scripts/dump_indicator_fixtures.py`. They load the wrong config or skip the fixture staleness check. The guard hook refuses the direct test runner commands. If you truly must call pytest directly: `ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest tests/unit/ -v`. `cd portal/web && npm run test:watch` is acceptable during TDD only.

## Markers

Tests are marked automatically by directory: `unit`, `integration`, `e2e`, plus `market_data`, `oanda_real` (needs real OANDA network and credentials), `slow`. Skip OANDA tests: `ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -m "not oanda_real"`.

## Synthetic data vs real OANDA data

| Pattern | Correct for | Not correct for |
|---|---|---|
| **Synthetic deterministic fixture** | Pure-computation parity ("implementation A agrees with B on the same input"). Example: `portal/web/tests/fixtures/indicator-values.json`, generated from a fixed-seed construction, checked in | Data-pipeline correctness, backtest realism, strategy fitness |
| **Synthetic seeded candles** (`scripts/seed_test_candles.py`) | E2E: `run-e2e-tests.sh` seeds them so timeframe-availability tests are deterministic, with ingestion off | Any assertion about market realism |
| **Real OANDA data** via `@pytest.mark.oanda_real` | Integration tests of the data pipeline, strategy backtests, anything where input realism drives the assertion | Tests that need reproducible input |

Do not use the `seed_test_candles` fixture, hardcoded prices or mocked market data in **integration** tests whose assertions depend on market realism. Seed scripts `seed_instruments.py` (6 FX pairs metadata) and `seed_test_data.py` (E2E users) create no price data.

## Writing tests

- A bug found anywhere other than CI means a test was missing: the fix must include a test that fails before the fix and passes after, and file a **Test gap** issue.
- Structure, naming and troubleshooting: `tests/README.md`. Strategy: `planning/architecture/TEST_STRATEGY.md`.
