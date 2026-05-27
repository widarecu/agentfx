# US-003: Evolution Run Lifecycle

**Version:** v0.3.0 - Autonomous Strategy Loop
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** a Railway cron job triggers `POST /api/v1/evolution-runs` on schedule (or a TRADER manually triggers it)
  **AND** the request body specifies the FX pair, granularity, historical window, and stop-condition overrides
**WHEN** the API receives the request
**THEN** the system:
1. Persists an `evolution_run` row with status `QUEUED` and returns 202 Accepted with `run_id`
2. The orchestrator forks a child process via `multiprocessing.Process` and applies `resource.setrlimit()` caps inside the child (RAM: 2 GiB; CPU time: `max_wall_clock_seconds` as OS-level backstop)
3. The child:
   - Initialises a population of 30 genomes (gen-0 seeds + random DSL fillers)
   - Loops: evaluate fitness on folds 1..3, persist `generation` + `strategy_genome` rows, every N=3 generations invoke LLM injection (US-005), apply GA operators to produce next generation, write `decision_log` entries
   - Stops when ANY of: `max_generations`, `max_wall_clock_seconds`, `target_oos_sharpe`, `no_improvement_generations`, `llm_budget_exhausted`
   - Invokes the Validator (US-004) on top-K genomes — walk-forward + Gate B on the held-out final fold
   - Marks survivors `status=VALIDATED, awaiting_approval=true`
   - Exits cleanly; parent records `evolution_run.status=COMPLETED` with `stop_reason`
4. If the child crashes (non-zero exit, OOM, hang past CPU cap), the parent marks the run `FAILED` with `stop_reason='process_crashed'` and continues serving API requests
5. On app startup `recover_queued_evolution_runs()` re-queues any runs left `QUEUED` or orphaned `RUNNING` from a previous process

## Acceptance Criteria

- [ ] `POST /api/v1/evolution-runs` returns 202 Accepted with `{run_id, status, status_url, estimated_max_generations, created_at}` immediately
- [ ] TRADER or ADMIN role required to create runs (RBAC enforced same as `optimization-jobs`)
- [ ] Orchestrator forks a child process per run (not threads, not asyncio tasks) — process isolation per "Process architecture" choice in the plan
- [ ] `resource.setrlimit(RLIMIT_AS, …)` applied inside child for 2 GiB RAM cap; `resource.setrlimit(RLIMIT_CPU, …)` for CPU-time cap matching `max_wall_clock_seconds`
- [ ] Parent records the child PID on `evolution_run.process_pid` so `POST /api/v1/evolution-runs/{id}/stop` can `os.kill(pid, SIGTERM)`
- [ ] All five stop conditions are individually testable and trigger correctly in isolation
- [ ] Crash recovery: process kill mid-run → on app restart, run is recovered, resumes from last persisted generation (not from scratch)
- [ ] `recover_queued_evolution_runs()` is registered in [src/agentfx/api/main.py](../../src/agentfx/api/main.py) lifespan, mirroring the existing `recover_queued_jobs()` pattern from [src/agentfx/api/workers/optimization_runner.py](../../src/agentfx/api/workers/optimization_runner.py)
- [ ] Population: 30 genomes / generation; gen-0 = MA + RSI + Donchian seeds + 27 random DSL fillers
- [ ] Every agent decision (population init, genome evaluation, LLM call, mutation, selection, validation) writes a `decision_log` row (US-016)

## API Contract

### Create Evolution Run
```
POST /api/v1/evolution-runs
Content-Type: application/json
Authorization: Bearer <JWT>

{
  "instrument": "EUR_USD",
  "granularity": "M1",
  "data_years": 5,
  "stop_conditions": {
    "max_generations": 30,
    "max_wall_clock_seconds": 21600,
    "target_oos_sharpe": 1.5,
    "no_improvement_generations": 5,
    "llm_budget_usd": 5.00
  },
  "population_size": 30,
  "llm_injection_every_n": 3
}
```

### Response (202)
```
HTTP 202 Accepted

{
  "run_id": "evorun_20260505_xyz789",
  "status": "queued",
  "status_url": "/api/v1/evolution-runs/evorun_20260505_xyz789",
  "instrument": "EUR_USD",
  "granularity": "M1",
  "estimated_max_generations": 30,
  "created_at": "2026-05-05T22:00:00Z"
}
```

### Get Run Detail
```
GET /api/v1/evolution-runs/{run_id}
```

### Response
```json
{
  "run_id": "evorun_20260505_xyz789",
  "status": "running|completed|failed|stopped",
  "stop_reason": null,
  "current_generation": 12,
  "best_genome_id": "gen_…",
  "best_oos_sharpe": null,
  "llm_cost_usd": 1.42,
  "process_pid": 12345,
  "started_at": "...",
  "completed_at": null
}
```

### List Runs
```
GET /api/v1/evolution-runs?status=running&page=1&page_size=10
```

### Stop a Run
```
POST /api/v1/evolution-runs/{run_id}/stop
```

Sends SIGTERM to the child process; orchestrator detects clean shutdown and writes `stop_reason='manual_stop'`.

### Generations & Genomes (see US-016 for full observability surface)
```
GET /api/v1/evolution-runs/{run_id}/generations
GET /api/v1/evolution-runs/{run_id}/genomes/{genome_id}
GET /api/v1/evolution-runs/{run_id}/decisions
```

## Database Schema (additions)

```sql
CREATE TABLE evolution_run (
    id                       VARCHAR(50) PRIMARY KEY,    -- evorun_{date}_{short_uuid}
    status                   VARCHAR(20) NOT NULL,       -- queued|running|completed|failed|stopped
    instrument               VARCHAR(10) NOT NULL,
    granularity              VARCHAR(5) NOT NULL,
    config_json              JSONB NOT NULL,             -- full request body for reproducibility
    stop_conditions_json     JSONB NOT NULL,
    stop_reason              VARCHAR(40),
    current_generation       INT DEFAULT 0,
    best_genome_id           UUID,
    llm_cost_usd             NUMERIC(10,4) DEFAULT 0,
    process_pid              INT,
    started_at               TIMESTAMPTZ,
    completed_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    created_by               UUID REFERENCES users(id)
);

CREATE TABLE generation (
    id                  UUID PRIMARY KEY,
    run_id              VARCHAR(50) REFERENCES evolution_run(id) ON DELETE CASCADE,
    index               INT NOT NULL,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    llm_invoked         BOOLEAN DEFAULT FALSE,
    llm_call_id         VARCHAR(80),
    best_fitness        FLOAT,
    UNIQUE (run_id, index)
);

CREATE TABLE strategy_genome (
    id                  UUID PRIMARY KEY,
    generation_id       UUID REFERENCES generation(id) ON DELETE CASCADE,
    dsl_json            JSONB NOT NULL,                  -- the StrategySpec
    parent_ids          JSONB DEFAULT '[]',              -- [uuid, …]
    origin              VARCHAR(20) NOT NULL,            -- seed|crossover|mutation|llm
    fitness             FLOAT,
    validator_passed    BOOLEAN,
    validation_failures JSONB DEFAULT '[]',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_genome_generation ON strategy_genome(generation_id);
CREATE INDEX idx_genome_fitness ON strategy_genome(fitness DESC NULLS LAST);
```

(`validation_fold` and `decision_log` schemas in US-004 and US-016 respectively.)

## Files Involved

### Create
- `src/agentfx/agents/evolution/__init__.py`
- `src/agentfx/agents/evolution/ga.py` — selection, crossover, mutation operators (DEAP wiring + custom DSL ops)
- `src/agentfx/agents/evolution/fitness.py` — fitness function: runs backtest on training folds, applies diversity penalty
- `src/agentfx/agents/evolution/population.py` — `Population` container, `Generation` lineage
- `src/agentfx/api/models/evolution.py` — SQLAlchemy ORM for the new tables
- `src/agentfx/api/schemas/evolution.py` — Pydantic request/response schemas
- `src/agentfx/api/services/evolution.py` — service layer (CRUD, queries)
- `src/agentfx/api/routers/evolution.py` — FastAPI endpoints
- `src/agentfx/api/workers/evolution_runner.py` — `run_evolution(run_id)`, `recover_queued_evolution_runs()`, subprocess fork + setrlimit
- `migrations/versions/<new>_evolution_tables.py`

### Modify
- `src/agentfx/agents/orchestrator.py` — replace stub with evolution loop driver
- `src/agentfx/api/main.py` — register evolution router; register `recover_queued_evolution_runs()` in lifespan
- `requirements.in` — add `deap>=1.4`

## Verification Commands

```bash
# Unit tests
make test-file FILE=tests/unit/agents/evolution/test_ga.py
make test-file FILE=tests/unit/agents/evolution/test_population.py
make test-file FILE=tests/unit/agents/evolution/test_fitness.py
make test-file FILE=tests/unit/agents/test_orchestrator.py
make test-file FILE=tests/unit/api/test_evolution_router.py

# Integration
make test-file FILE=tests/integration/evolution/test_evolution_pipeline.py
make test-file FILE=tests/integration/evolution/test_orchestrator_stop_conditions.py
make test-file FILE=tests/integration/evolution/test_evolution_runner_recovery.py
```

## Authorization Matrix

| Action | Minimum Role |
|---|---|
| Create evolution run | TRADER |
| View runs / generations / genomes / decisions | VIEWER |
| Stop own run | TRADER |
| Stop any run | ADMIN |
| Delete a run | ADMIN |

## Dependencies

- US-006 (Strategy DSL) — population is made of `StrategySpec` instances
- US-005 (LLM Hypothesis Injection) — invoked every N generations
- US-004 (Walk-forward Validation) — invoked at end of run
- US-016 (Decision log) — every step writes to it
- `deap>=1.4`
- Existing `run_backtest_from_db()` at [src/agentfx/services/backtest.py:269](../../src/agentfx/services/backtest.py)

## Notes

- The orchestrator does *not* call any agent's in-memory state. Inter-agent contracts go via Postgres rows. This is the architectural rule that makes the monolith split-able later.
- Process isolation is the brittleness mitigation. A pandas leak inside one run cannot affect the API or other in-flight runs. The parent treats the child as fungible.
- The 6h CPU cap is an OS-level backstop in case the in-Python `max_wall_clock_seconds` check hangs (e.g. inside a tight numerical loop holding the GIL).
- Stop reason granularity is important for analytics: distinguishing `target_reached` from `budget_exhausted` from `process_crashed` tells you whether to celebrate or investigate.
- Recovery is *not* idempotent at the per-genome level — it resumes from the last completed generation. A genome being evaluated when the process died is re-evaluated. This is acceptable because backtests are deterministic.
