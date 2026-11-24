# AgentFX

An autonomous FX trading platform. Agents propose strategy ideas, build and backtest them, validate them out-of-sample, and run the survivors on paper. A strategy that passes the validation gates is shown to the operator for approval before it ever trades real money.

```
Discovery → Optimizer → Validator → [human approval] → Paper → Live
    ↑                                                            |
    └──────────────── Evolution (feedback loop) ←────────────────┘
```

Broker and market data: OANDA (practice accounts for everything below live). Rollout is paper-first, with human approval for every live promotion.

## Quick start

Requires Docker Desktop (or Colima / OrbStack). Local Postgres is not supported.

```bash
cp .env.example .env.development     # then fill in OANDA practice credentials
make docker-up PROFILE=dev           # backend :8000, frontend :5173, postgres :5432
make docker-logs PROFILE=dev
make docker-down PROFILE=dev
```

For a local virtualenv (linting, unit tests, scripts): `make venv && make dev`.

There are three local profiles (dev, test, uat) with separate databases. Read [planning/runbooks/environments.md](planning/runbooks/environments.md) before using uat: it holds long-lived data and must never be used for E2E.

## Tests and checks

```bash
make lint            # ruff check + ruff format --check (same as CI)
make test-unit       # backend pytest + portal Vitest
make test-integration
make e2e             # full Docker test stack + Playwright (test profile only)
```

Always use the `make` targets; see [tests/README.md](tests/README.md).

## Backtests

```bash
make backtest PAIRS="AUDUSD,EURUSD" DAYS=2
make backtest-full PAIRS="AUDUSD,EURUSD" DAYS=2 STRATEGY=ma FAST=20 SLOW=50
```

## Deployment

| Component | Platform | URL |
|---|---|---|
| Backend | Railway | https://agentfx-staging.up.railway.app |
| Frontend | Vercel | https://agentfx-liart.vercel.app |
| Database | Railway PostgreSQL | internal, via `DATABASE_URL` |

A push to `main` runs `.github/workflows/deploy.yml`: E2E, then `railway up` for the backend and `vercel deploy --prod` for the frontend. Every change reaches `main` through a PR, and every merge deploys to staging; see [planning/runbooks/delivery-pipeline.md](planning/runbooks/delivery-pipeline.md).

The backend image (`Dockerfile`) runs migrations, seeds instrument metadata, then starts the API. Railway variables are set in its dashboard, notably `DATABASE_URL`, `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `BACKEND_CORS_ORIGINS` (must include the Vercel domain), `BACKFILL_DAYS` and `DEBUG`. Every setting is listed in `.env.example`.

## Repository layout

| Path | What |
|---|---|
| `src/agentfx/` | Python backend: `core/` pure domain logic, `adapters/` OANDA and storage, `agents/` optimizer, validator, evolution and the agent fleet, `services/` backtest, ingestion, paper trading, `api/` FastAPI app |
| `portal/web/` | React + TypeScript + Vite + Tailwind portal |
| `tests/` | unit, integration, e2e, performance |
| `migrations/` | Alembic migrations |
| `planning/` | roadmap, requirements, architecture standards, runbooks |
| `scripts/` | seeding, E2E runner, CI checks, fleet tooling |

## Work tracking

Work is tracked in Linear (team `AFX`). How issues move from idea to production: [planning/runbooks/linear-operating-model.md](planning/runbooks/linear-operating-model.md). `planning/BACKLOG.md` is a frozen snapshot from 2026-10-22.

## Agent guidance

Instructions for AI coding agents are in [CLAUDE.md](CLAUDE.md), with topic detail in `.claude/rules/`, workflows in `.claude/skills/`, and guard hooks in `.claude/hooks/`.
