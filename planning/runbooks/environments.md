# Local environments: dev, test, uat

There are **three local Docker profiles**. Each has its own containers, database volume and ports, so running one cannot disturb the data in another. They have different purposes and different durability.

| Profile | Purpose | Backend | Frontend | Postgres (host) | Volume | Durability |
|---|---|---|---|---|---|---|
| **dev** | Iterating on code with the full ingestion stack and watching logs | `:8000` | `:5173` | `:5432` | `postgres_data` | Long-lived; may be wiped on request for a clean rebuild |
| **test** | Automated tests and E2E (`make e2e`, `make test-integration`) | `:8001` | `:5174` | `:5433` | `postgres_test_data` | **Ephemeral.** Every `make e2e` wipes it |
| **uat** | Wilson's day-to-day exploratory stack | `:8002` | `:5175` | `:5434` | `postgres_uat_data` | **Long-lived.** Holds real OANDA backfills and Wilson's portal account. Never wiped automatically |

Each profile reads its own env file: `.env.development` (dev, database `agentfx_dev`), `.env.test` (test, `agentfx_test`), `.env.uat` (uat, `agentfx_uat`, with its own user and password). These files hold secrets, are gitignored, and agents must not edit them. New settings go into `.env.example`.

## Hard rule: never run `make e2e` against uat or dev

`make e2e` runs `scripts/run-e2e-tests.sh`, which starts with `docker compose down -v --remove-orphans`. That deletes the target profile's database volume. The script refuses any profile other than `test`. Do not add an override or weaken the guard. In Claude Code sessions, `.claude/hooks/guard-bash.py` also refuses the command before it runs.

**Incident, 2026-05-27:** an E2E run executed while the test stack was also being used for manual acceptance testing. The wipe destroyed 6 years of EUR_USD M1 OANDA candles and Wilson's portal account. The uat profile and the guard exist so this cannot happen again.

## Choosing a profile

- Manual portal walkthroughs, exploratory evolution runs, real OANDA data Wilson has loaded → **uat**.
- `make test-integration`, `make e2e`, anything that needs a fresh database every time → **test**.
- Iterating on a backend change with ingestion running → **dev**.

Dev and test can run at the same time; their ports don't overlap.

## Everyday commands

```bash
make docker-up PROFILE=dev            # start a stack (dev | test | uat)
make docker-down PROFILE=dev          # stop it
make docker-logs PROFILE=dev          # follow all logs
make docker-backend-logs PROFILE=dev  # backend logs only
make docker-rebuild PROFILE=dev       # rebuild the image and restart the backend
```

After changing anything under `src/agentfx/`, rebuild. `docker compose up --force-recreate` alone reuses the old image; pass `--build` (or use `make docker-rebuild`).

The test profile bind-mounts `scripts/` read-only into `backend-test`, so test-only seed scripts run without shipping in the production image:

```bash
docker compose --profile test exec backend-test python scripts/seed_test_data.py
```

## Ingestion settings across environments

`INGESTION_ENABLED`, `BACKFILL_DAYS`, `CANDLE_INSTRUMENTS` and `CANDLE_GRANULARITIES` are set in exactly one place per environment. The rules agents need are in `.claude/rules/ingestion-config.md`. Typical uses:

```bash
# E2E: ingestion off, synthetic candles. The test volume is wiped on
# every `make e2e`, so don't backfill real data here.
make docker-up PROFILE=test

# Exploratory: uat keeps its volume. Defaults to ingestion on.
make docker-up PROFILE=uat

# Targeted uat backfill: only EUR_USD M1, 6 years, to avoid an hour of
# unnecessary OANDA traffic.
CANDLE_INSTRUMENTS=EUR_USD CANDLE_GRANULARITIES=M1 \
  make docker-up PROFILE=uat BACKFILL_DAYS=2190
```
