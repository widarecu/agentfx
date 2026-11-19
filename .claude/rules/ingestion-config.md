---
paths:
  - "docker-compose*.yml"
  - ".env.example"
  - "src/agentfx/api/core/config.py"
  - "tests/conftest.py"
  - "scripts/check_env_example.py"
---

# Configuration and the ingestion settings

## New settings

Every setting added to `src/agentfx/api/core/config.py` must also be added to `.env.example` in the same PR (`make check-env-example` enforces this). Never edit `.env.development`, `.env.test` or `.env.uat`: they hold secrets and are gitignored. Railway and Vercel values are set in their dashboards.

`ENVIRONMENT=development|test|uat|staging|production` selects which `.env.<environment>` file Pydantic loads; real environment variables override the file.

## Ingestion settings: one source of truth per environment

`INGESTION_ENABLED`, `BACKFILL_DAYS`, `CANDLE_INSTRUMENTS` and `CANDLE_GRANULARITIES` are set in exactly one place for each of three environments. There is no shared file between them. If you want to set these in two places, you have taken a wrong turn.

| Environment | Source of truth | Value |
|---|---|---|
| **Pytest** on the host (`make test-*`, `make e2e` host side) | `tests/conftest.py` sets `INGESTION_ENABLED=false` at import, before any settings import | Always `false`. TestClient start-up hangs on backfill otherwise |
| **Local Docker** dev / test / uat | `docker-compose.yml` `environment:` block, `${VAR:-default}` | Overridable from the shell per run. Defaults: dev `true`, `BACKFILL_DAYS=1825`; test `false`, `7` |
| **Railway** | Service variables in the Railway dashboard | Usually `true`, full `BACKFILL_DAYS` |

Each environment needs something different: pytest must never backfill, E2E must be deterministic, production must ingest live. Routing all three through `.env.test` forced one value to serve conflicting needs.

So `.env.test` and `.env.development` must **not** contain these four variables; adding them there would override the compose defaults. The Pydantic defaults in `config.py` are the base, and the compose defaults mirror them so a reader of compose alone sees what the container will use.

Usage examples: `planning/runbooks/environments.md`.
