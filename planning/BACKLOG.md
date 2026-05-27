# AgentFX Backlog

This backlog is organized by version milestone. Each item links to executable user stories in `planning/requirements/`.

**Status Values:** `TODO` | `IN_PROGRESS` | `BLOCKED` | `DONE`

---

## v0.1.0 - Foundation (COMPLETE)

**Theme:** Observable Infrastructure - Users can authenticate and view live market data
**Status:** COMPLETE
**Release Date:** 2025-12-13

### Delivered Capabilities
- [x] `DONE` User authentication (JWT, roles: VIEWER/TRADER/ADMIN)
- [x] `DONE` User registration with email validation
- [x] `DONE` Login/Register pages (functional)
- [x] `DONE` Market data REST API (candles, instruments, status, metrics)
- [x] `DONE` Dashboard with real user info and system status
- [x] `DONE` Market Data page with instruments dropdown and candle data table
- [x] `DONE` Candle ingestion worker (7-day backfill + continuous 60s polling)
- [x] `DONE` OANDA API integration for historical candle data
- [x] `DONE` Comprehensive test suite (unit, integration, E2E)

### Architecture Decisions
- **WebSocket removed from v0.1.0 scope** - Real-time streaming deferred to v0.2.0
- **Candle ingestion worker** - Single source of truth for all candle data
- **REST-only frontend** - Simpler architecture, WebSocket will be added in v0.2.0

### Test Results
| Test Type | Result |
|-----------|--------|
| Unit | 146 passed, 6 skipped (v0.2.0 features) |
| Integration | 68 passed |
| E2E | 21 passed |

### Exit Criteria (All Met)
- [x] All unit tests pass (0 failures)
- [x] Dashboard shows real user and system data
- [x] Market Data page loads instruments and candles without errors
- [x] No broken navigation links
- [x] E2E tests for auth flow pass

---

## v0.2.0 - Bayesian Parameter Optimizer Agent (FUNCTIONALLY COMPLETE)

**Theme:** First autonomous capability - optimize strategy parameters with Bayesian search
**Status:** All P0 + P1 delivered. Tests green (604 passed, 11 skipped, 0 failures across unit/integration/E2E as of 2026-05-04). P2 export not built. Coverage gate caveat below.

### P0 - Must Have
- [x] `DONE` Bayesian Optimizer Agent - Efficient parameter search
  - Req: [US-001](requirements/US-001-optimization-runs.md)
  - Files: `src/agentfx/agents/optimizer.py` (`BayesianOptimizerAgent`, GP + Matern + Expected Improvement, adaptive iterations `10 + 12*dims`)
  - Tests: `tests/unit/agents/test_bayesian_optimizer.py`, `tests/integration/optimization/test_optimization_pipeline.py`

- [x] `DONE` Trial persistence - Store runs with lineage and signals
  - Req: [US-001](requirements/US-001-optimization-runs.md)
  - Files: `src/agentfx/api/models/optimization.py` — `OptimizationJob`, `OptimizationTrial`, `OptimizationSignal`, `OptimizationTrade`

- [x] `DONE` Optimization API endpoints (`/api/v1/optimization-jobs`)
  - Req: [US-001](requirements/US-001-optimization-runs.md), [US-002](requirements/US-002-view-optimization.md)
  - Files: `src/agentfx/api/routers/optimization.py`, `src/agentfx/api/schemas/optimization.py`
  - Endpoints delivered: `POST` (202 Accepted, TRADER+), `GET list`, `GET detail`, `DELETE`, `GET /trials`, `GET /trials/top`, `GET /trials/{id}`, `GET /trials/{id}/signals`, `GET /trials/{id}/trades`
  - Tests: `tests/unit/routers/test_optimization_router.py`, `tests/integration/optimization/test_optimization_full_flow.py`

- [x] `DONE` Optimization portal pages - View jobs and results
  - Req: [US-002](requirements/US-002-view-optimization.md)
  - Files: `portal/web/src/pages/Optimization.tsx`, `portal/web/src/pages/OptimizationDetail.tsx`, `portal/web/src/router.tsx` (routes wired + protected)
  - Tests: `tests/e2e/optimization.spec.ts`, `tests/e2e/optimization_detail.spec.ts`

- [x] `DONE` Strategy visualization - Candlestick chart with signals
  - Req: [US-015](requirements/US-015-visualization.md)
  - Files: `portal/web/src/pages/TrialDetail.tsx`, `portal/web/src/components/StrategyChart.tsx`
  - Delivered: `lightweight-charts` candlesticks; long/short entry arrows; profit/loss exit circles; MA/Donchian overlays on right scale; RSI on left scale with oversold/overbought reference lines; zoom controls, indicator toggle, OHLC tooltip

- [x] `DONE` Backtest service - Read from PostgreSQL candles table
  - Req: [US-001](requirements/US-001-optimization-runs.md)
  - Files: `src/agentfx/services/backtest.py` — `load_price_df_from_db()` and `run_backtest_from_db()`

- [x] `DONE` Background job runner with crash recovery
  - Files: `src/agentfx/api/workers/optimization_runner.py`
  - Recovers QUEUED jobs at app startup via `recover_queued_jobs()` in `api/main.py` lifespan
  - Tests: `tests/unit/workers/test_optimization_runner.py`, `tests/integration/workers/test_optimization_runner.py`

- [x] `DONE` OANDA overnight financing (swap) costs in backtest
  - Files: `src/agentfx/services/backtest.py` — `compute_swap_returns()` applies cached long/short rates with weekday/Wednesday-3x rollover schedule
  - Falls back to zero swap if instrument financing rates not yet cached (logs warning)

### P1 - Should Have
- [x] `DONE` Configurable backfill days for 5-year historical data
  - Files: `src/agentfx/api/core/config.py` (`BACKFILL_DAYS` env-driven, default 7), `src/agentfx/api/workers/candle_ingestion.py`

- [x] `DONE` Per-job spread override
  - Files: `src/agentfx/services/cli.py` (`--spread-pips`), `src/agentfx/shared/config.py` (`backtest_default_spread_pips()`)

### P2 - Could Have
- [ ] `TODO` Optimization export - CSV/JSON download
  - Files: `src/agentfx/api/routers/optimization.py`

### Known Caveats (non-blocking, follow-up)
- **Global coverage gate (80%) fails** — unit run at 65.91%, integration at 60%. v0.2.0 modules themselves are well covered (optimizer.py 88.7%, optimization router 95.6%, optimization model 100%, optimization_runner 96.1%). Shortfall is from untested modules unrelated to v0.2.0 scope: `agents/discovery.py`, `agents/evolution.py`, `agents/validator.py`, `agents/orchestrator.py` (all 0% — v0.3.0+ stubs), `services/cli.py`, `services/ingest.py`, `services/run.py`, `tools/gen_wireframes.py`. Decision needed: lower the gate or add minimal stub coverage so `make test-*` exits 0.
- **Per-pair spread still not configurable** — single spread applies to all pairs in a job. EUR/USD vs JPY vs exotics realistically diverge; can skew Gate B pass/fail signal. Tracked in auto-memory.
- **sklearn GP runtime warnings** — `overflow/divide-by-zero/invalid in matmul` during BayesianOptimizer fits. Cosmetic, results unaffected, but worth filtering or stabilising the GP fit before declaring optimizer "production-ready."

### Deferred Items
- WebSocket streaming (US-013) - Deferred to TBD (use OANDA directly for real-time feeds)

---

## v0.3.0 - Autonomous Strategy Loop

**Theme:** First genuinely agentic increment — a scheduled closed loop that discovers, evolves, and validates trading strategies without human intervention. Discovery and Validation are absorbed into this milestone (no longer separate versions).

**Reference:** Full plan at `~/.claude/plans/i-think-we-need-functional-pony.md`. Architectural pillars summarised below.

**Key technology choices** (live registry — see plan's "Technology Choices" section for revisit triggers):
- Genetic algorithm: `deap>=1.4` + custom mutation/crossover on a Pydantic DSL
- LLM hypothesis injection: `anthropic>=0.40` SDK, `claude-sonnet-4-6` default, prompt caching mandatory, structured output via tool-use
- Agent-to-agent comms: Postgres-mediated, no shared in-memory state
- Orchestration: Railway Cron → `POST /api/v1/evolution-runs` → FastAPI BackgroundTasks → `multiprocessing.Process` per run with `resource.setrlimit()`
- Process architecture: modular monolith on one Railway service; pre-committed split point for `backtest-worker` when run wall-clock exceeds 6h or queue depth >200

### P0 - Must Have

- [ ] `TODO` Strategy DSL v1 + compiler + indicator catalog + gen-0 seeds
  - Req: [US-006](requirements/US-006-strategy-dsl.md)
  - Files: `src/agentfx/core/dsl/{grammar.py, compiler.py, indicators.py, seeds.py}`
  - Acceptance: existing MA/RSI/Donchian re-expressed as DSL specs; compiled-DSL signals match classic-class signals on identical data (DSL-completeness gate)

- [ ] `TODO` Extract `evaluate_gate_b()` to `core/gates.py`
  - Files: `src/agentfx/core/gates.py` (new); `src/agentfx/agents/optimizer.py` (import update)

- [ ] `TODO` Persistence: ORM models + Alembic migration
  - Tables: `evolution_run`, `generation`, `strategy_genome`, `validation_fold`, `decision_log`
  - Files: `src/agentfx/api/models/evolution.py`, `migrations/versions/<new>.py`

- [ ] `TODO` GA core (DEAP + custom DSL operators)
  - Req: [US-003](requirements/US-003-evolution-run-lifecycle.md)
  - Files: `src/agentfx/agents/evolution/{ga.py, fitness.py, population.py}`
  - Operators: tournament-3 selection, subtree crossover (p=0.6), 5 mutation types (p=0.3), 10% elitism, edit-distance ≤ 2 diversity penalty
  - Population: 30/generation

- [ ] `TODO` LLM hypothesis injector
  - Req: [US-005](requirements/US-005-llm-hypothesis-injection.md)
  - Files: `src/agentfx/agents/evolution/{llm_client.py, prompts.py}`
  - Anthropic SDK with prompt caching, structured output via `propose_strategy` tool, exponential-backoff retries, hard per-run cost cap (`EVOLUTION_LLM_BUDGET_USD` default 5.00)
  - Invoked every N=3 generations, replaces bottom-5 by fitness

- [ ] `TODO` Validator agent (replace stub)
  - Req: [US-004](requirements/US-004-walk-forward-validation.md)
  - Files: `src/agentfx/agents/validator.py`
  - K=4 contiguous walk-forward folds; final fold withheld from GA entirely; Gate B applied on OOS

- [ ] `TODO` Orchestrator agent (replace stub) + subprocess runner
  - Req: [US-003](requirements/US-003-evolution-run-lifecycle.md)
  - Files: `src/agentfx/agents/orchestrator.py`, `src/agentfx/api/workers/evolution_runner.py`
  - Drives the loop; forks `multiprocessing.Process` per run with `resource.setrlimit()` RAM (2 GiB) + CPU-time (6h) caps
  - Stop conditions: `max_generations`, `max_wall_clock_seconds`, `target_oos_sharpe`, `no_improvement_generations`, `llm_budget_exhausted`
  - Crash handling: parent detects child non-zero exit → marks run `FAILED` → continues serving

- [ ] `TODO` Backtest dispatch on DSL specs
  - Files: `src/agentfx/services/backtest.py:655-669` — extend `make_strategy()` to accept `{"type": "dsl", "spec": {...}}`

- [ ] `TODO` API surface: routers + schemas + service + lifespan recovery
  - Req: [US-003](requirements/US-003-evolution-run-lifecycle.md), [US-016](requirements/US-016-evolution-observability.md)
  - Files: `src/agentfx/api/routers/evolution.py`, `src/agentfx/api/schemas/evolution.py`, `src/agentfx/api/services/evolution.py`
  - Endpoints: `POST /evolution-runs` (TRADER+, 202), `GET list`, `GET detail`, `GET /generations`, `GET /genomes/{id}`, `GET /decisions`, `POST /stop`
  - `recover_queued_evolution_runs()` registered in `api/main.py` lifespan

- [ ] `TODO` Portal: Evolution pages + DSL tree renderer
  - Req: [US-016](requirements/US-016-evolution-observability.md)
  - Files: `portal/web/src/pages/{EvolutionRuns.tsx, EvolutionRunDetail.tsx, GenomeDetail.tsx}`, `portal/web/src/router.tsx`
  - GenomeDetail shows DSL spec as readable tree, walk-forward fold metrics, parent/child lineage, LLM reasoning text for `origin='llm'` genomes
  - Reuses existing `StrategyChart.tsx` for candle + signal rendering

- [ ] `TODO` Decision log on every agent action (observability)
  - Req: [US-016](requirements/US-016-evolution-observability.md)
  - Append-only `decision_log` rows from every agent step; LLM cost + latency emitted as Prometheus metrics via `api/core/metrics.py`

### P1 - Should Have

- [ ] `TODO` Backtest perf benchmark + budget realism check
  - Time a single backtest on 30d M1 data; if > 5s, reduce population/granularity for v0.3.0 or accelerate `run_backtest_from_db()`
  - If consistently > 6h wall-clock per run, the **pre-committed `backtest-worker` split** fires (see plan)

- [ ] `TODO` Add `anthropic>=0.40` and `deap>=1.4` to `requirements.in`; regenerate `requirements.txt`

- [ ] `TODO` Railway cron declaration in `railway.toml` for nightly evolution-run trigger

### P2 - Could Have

- [ ] `TODO` Diversity-injection variant using `claude-haiku-4-5-20251001` for cheap high-frequency seeding

- [ ] `TODO` Evolution-run export (CSV/JSON of full lineage)

### Out of Scope (deferred to v0.4.0+)

- Multi-asset / multi-pair within one run
- Regime-aware prompts
- Multi-objective fitness (Sharpe + capacity + turnover)
- Cross-run learning (deferred to v0.5.0)
- Paper / live trading
- Sentiment / news / orderbook data
- Custom user-supplied indicators

### Acceptance for "v0.3.0 done"

1. A scheduled Railway cron triggers a fresh evolution run nightly.
2. The run completes unattended, hits a stop condition, persists VALIDATED genomes for human review.
3. At least one VALIDATED genome differs structurally from the gen-0 seeds (i.e. system actually discovered something).
4. Decision log lets a reviewer reconstruct *why* each surviving genome was kept.
5. LLM cost stays under the configured per-run budget across 5 consecutive nightly runs.

---

## v0.4.0 - Capability Expansion

**Theme:** Grow the discovery surface area now that the loop works.

- [ ] `TODO` Expand DSL: more indicators (MACD, stochastic, ADX, OBV, ichimoku, …), time-of-day entry filters, multi-timeframe references
- [ ] `TODO` Multi-symbol portfolios within one evolution run
- [ ] `TODO` Per-pair configurable spread (carry-over from open memory item)
- [ ] `TODO` Regime tags as prompt context (volatility quintile, trend strength)
- [ ] `TODO` Optional Bayesian late-stage refiner — repurpose the existing `BayesianOptimizerAgent` to fine-tune numeric thresholds inside a winning genome shape after the GA selects it

---

## v0.5.0 - Self-Improvement

**Theme:** The platform learns across runs, not just within runs.

- [ ] `TODO` Evolution agent (real implementation, replacing the v0.3.0-era stub) — distils results across runs into prompt updates and learned DSL priors
- [ ] `TODO` Drift detection on currently-running validated strategies — when behaviour drifts from validation distribution, re-trigger evolution
- [ ] `TODO` Cross-run hypothesis lineage — LLM prompts include patterns observed across the last K runs, not just the current one
- [ ] `TODO` LLM prompt A/B testing infrastructure to measure prompt-quality improvements quantitatively

---

## v0.6.0 - Paper Trading

**Theme:** Promote VALIDATED genomes to OANDA Practice with human approval gate. (Was previously v0.5.0; pushed down a slot by the v0.3.0 reframe.)

- [ ] `TODO` Approval queue portal page — VALIDATED genomes awaiting human sign-off
- [ ] `TODO` Paper trading service — executes approved genomes on OANDA Practice
- [ ] `TODO` Live performance comparison vs. validation metrics
- [ ] `TODO` Email/Slack notifications for approval requests

---

## v1.0.0 - Live Trading + Portfolio

**Theme:** Self-sustaining strategy factory in production.

- [ ] `TODO` Live trading integration (OANDA Live) with second human approval gate (paper → live)
- [ ] `TODO` Multi-strategy portfolio management with capital allocation across approved genomes
- [ ] `TODO` Performance monitoring dashboard (live P&L, drift alerts, regime context)
- [ ] `TODO` Strategy versioning and rollback
- [ ] `TODO` Evaluate **Temporal** (or similar) for durable workflow signals at this point — see plan's "Orchestration & scheduling" revisit triggers

---

## Legacy Items (Archived)

The following items from the original backlog have been archived to `planning/archive/`:
- FEAT-0001 through FEAT-0006 (replaced by agentic milestones)
- ADR-0001, ADR-0002 (decisions preserved for reference)
- BUS-0001, SYS-0001, TECH-0001, TECH-0002, TEST-0001

These documents contain valuable context and are preserved for historical reference.
