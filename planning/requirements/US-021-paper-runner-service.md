# US-021: Paper Runner Service + Subprocess Model

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (the core capability of the milestone)
**Status:** TODO

## Given-When-Then

**GIVEN** a TRADER user has approved a VALIDATED genome from `/paper/approvals`
  **AND** the approval created a `runs` row with `approval_status=APPROVED`, `environment=PAPER`, `strategy_genome_id`, `user_id`, `risk_config`
**WHEN** the API layer enqueues the run via `schedule_paper_run(run_id)`
**THEN** the system:

1. Forks a child process via `multiprocessing.Process` targeting `paper_runner.run_paper_run(run_id)`
2. Applies resource limits: `resource.setrlimit(RLIMIT_AS, 2 GiB)`, `resource.setrlimit(RLIMIT_CPU, 24 * 3600)` per-day CPU cap as safety net
3. The child:
   - Loads the `Run` row and owning user's `user_oanda_credentials`
   - Decrypts the API key via `secrets.decrypt_secret()`
   - Constructs a scoped `OandaClient` with the decrypted credentials
   - Loads `strategy_genomes.dsl_json`, deserialises to `StrategySpec`, calls `compile_dsl_spec()` → `Strategy`
   - Transitions `approval_status` from `APPROVED` to `RUNNING`
   - Enters the main loop
4. Parent records the child PID on `runs.process_pid`, spawns a watchdog thread that polls `proc.is_alive()` every 5 seconds
5. On child crash (non-zero exit): watchdog marks `runs.status=ERROR`, `stop_reason=process_crashed`, attempts a best-effort broker position-close using the credentials from step 3

## Main loop specification

At a cadence matching the genome's granularity (M1 = every 60s, M5 = every 5m, H1 = every 1h):

1. **Fetch latest candles** from the shared `candles` table (populated by the shared candle ingestion worker; not per-user).
2. **Compute signals** via `strategy.generate_signals(df)`.
3. **Read current OANDA position** via `client.get_position(instrument)`.
4. **Diff signal vs position**:
   - Signal = +1, current position = 0 → `client.place_market_order(instrument, units=risk_config.max_position_size, stop_loss_pips=…, take_profit_pips=…)`
   - Signal = 0, current position ≠ 0 → `client.close_all_positions(instrument)`
   - Signal changes sign → close current, then open new
   - Signal unchanged, position matches → hold
5. **Pre-order risk checks** via `src/agentfx/core/policy.py`:
   - Reject order if projected position size > `risk_config.max_position_size`
   - Reject order if cumulative daily loss < `risk_config.daily_loss_threshold` (halts run with `stop_reason=DAILY_LOSS_LIMIT`)
   - Reject order if `runs.kill_requested_at` is set (halt path)
6. **Persist orders + trades**: on order fill, insert `orders` row and (once closed) `trades` row. Update `runs.current_balance`, `runs.pnl`.
7. **Poll `runs.kill_requested_at`** — if non-null AND older than the 3-second undo window on the frontend has fired, close all positions and exit with `approval_status=HALTED_MANUAL`, `stop_reason=USER_KILL`.
8. **Log every decision** to `decision_logs` with `user_id`, action types: `SIGNAL_COMPUTED`, `ORDER_PLACED`, `ORDER_FILLED`, `POSITION_CLOSED`, `KILL_ACKNOWLEDGED`, etc.
9. **Metrics**: emit timing histogram of loop iteration; alert if p95 > 1 second (NFR target).

## Acceptance Criteria

### Schema — REUSE existing `runs/orders/trades` tables

- [ ] Alembic migration adds to `runs`:
  - `strategy_genome_id` VARCHAR FK → `strategy_genomes.id` NULLABLE (so legacy rows still work)
  - `oanda_environment` ENUM(`PRACTICE`, `LIVE`) NULLABLE (distinct from `RunEnvironment{DEV, PAPER, LIVE}` — see design note below)
  - `user_id` INT FK → `users.id` NULLABLE
  - `stop_reason` VARCHAR — union of `USER_KILL`, `DAILY_LOSS_LIMIT`, `BROKER_CIRCUIT`, `CREDENTIALS_REVOKED`, `process_crashed`, `COMPLETED`
  - `kill_requested_at` TIMESTAMP NULLABLE
  - `approved_at` TIMESTAMP NULLABLE
  - `approved_by_user_id` INT FK → `users.id` NULLABLE
  - `approval_status` ENUM(`AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, `RUNNING`, `COMPLETED`, `HALTED_MANUAL`, `ERROR`) NULLABLE
  - `risk_config` JSON NULLABLE (schema: `{max_position_size, daily_loss_threshold, initial_balance}`)
  - `rejected_reason` VARCHAR NULLABLE
- [ ] Index `(user_id, environment, status)` for hot-path list query
- [ ] Design note in migration docstring: "reuses legacy `runs` table rather than parallel `paper_run` — the legacy `RunEnvironment{DEV,PAPER,LIVE}` enum was designed for this. Parallel tables rejected because they duplicate schema and block v1.0.0 live-trading reuse."

### Service

- [ ] New `src/agentfx/services/paper_runner.py` — `run_paper_run(run_id: str)` subprocess entry point
- [ ] `src/agentfx/api/workers/paper_runner.py` (or same module) — parent-side `schedule_paper_run(run_id)`, `record_child_pid`, watchdog thread, startup recovery of orphaned `RUNNING` runs
- [ ] Watchdog + crash-recovery mirror `src/agentfx/api/workers/evolution_runner.py` structure
- [ ] Registered in `src/agentfx/api/main.py` lifespan: `recover_queued_paper_runs()` on startup

### API endpoints (new `src/agentfx/api/routers/paper.py`)

- [ ] `POST /api/v1/paper-runs/enqueue` — from `GenomeDetail.tsx` "Send to approval queue" button. Requires `validator_passed=true` on genome. Creates `runs` row with `approval_status=AWAITING_APPROVAL`. Returns 202 + queue position.
- [ ] `GET /api/v1/paper-runs/approval-queue` — user's rows where `approval_status=AWAITING_APPROVAL`
- [ ] `POST /api/v1/paper-runs/{run_id}/approve` body `{name, instrument, granularity, initial_balance, risk_config}` — transitions row to `approval_status=APPROVED`, records `approved_at`, `approved_by_user_id`, enqueues subprocess via `schedule_paper_run`. Requires credentials `verified_at` non-null.
- [ ] `POST /api/v1/paper-runs/{run_id}/reject` body `{reason}` — transitions to `approval_status=REJECTED`
- [ ] `GET /api/v1/paper-runs` — user-scoped list (see US-018 for RBAC)
- [ ] `GET /api/v1/paper-runs/{run_id}` — RBAC-enforced detail
- [ ] `POST /api/v1/paper-runs/{run_id}/kill` — sets `kill_requested_at=now()`; returns 202. Runner picks up within one loop cycle.
- [ ] `DELETE /api/v1/paper-runs/{run_id}` — only when status terminal

### Tests

- [ ] `tests/unit/services/test_paper_runner.py` — signal→order diff logic, risk-check rejection paths, kill-switch acknowledgement (with mocked `OandaClient` and pre-recorded candles)
- [ ] `tests/integration/test_paper_runner_e2e.py` (respx-based fake OANDA server): deploy a genome, drive 20 candle-arrival events, assert orders → trades → `runs.pnl` updated; hit kill switch, assert positions close within one loop cycle
- [ ] Crash recovery test: kill the child process mid-run; assert watchdog marks `status=ERROR`, `stop_reason=process_crashed`, and attempts a best-effort position close

## Performance target

- Decision loop p95 < 1 second (NFR target from `planning/architecture/NFRS.md`)
- Emit `paper_runner_loop_seconds` timing histogram to structured logs; alert if p95 exceeds 1s

## Files Involved

- `src/agentfx/services/paper_runner.py` (new — decision loop)
- `src/agentfx/api/workers/paper_runner.py` (new — subprocess management)
- `src/agentfx/api/routers/paper.py` (new)
- `src/agentfx/api/schemas/paper.py` (new — Pydantic request / response)
- `src/agentfx/api/services/paper.py` (new — orchestration)
- `src/agentfx/api/models/run.py` (edit — add columns above)
- `src/agentfx/core/policy.py` (edit — add paper-trading pre-order risk checks)
- `src/agentfx/api/main.py` (edit — register router + recovery)
- `migrations/versions/2026_07_XX_add_paper_trading_schema.py` (new)
- `tests/unit/services/test_paper_runner.py` (new)
- `tests/integration/test_paper_runner_e2e.py` (new)

## Verification

1. `make test-unit` — paper runner signal-diff suite passes
2. `make test-integration` — end-to-end respx suite passes
3. Manual UAT: log in as TRADER, approve a validated genome, watch `runs.status` transition `APPROVED → RUNNING`; confirm order appears on OANDA Practice dashboard; watch `runs.pnl` update on portal; hit kill switch, confirm position closes on OANDA and status → `HALTED_MANUAL` within 5s
4. Crash recovery UAT: send `kill -9` to the child process PID; confirm watchdog marks `stop_reason=process_crashed` and (best-effort) closes any open OANDA position under that user's credentials
