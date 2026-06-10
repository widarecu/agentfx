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

## v0.3.0 - Autonomous Strategy Loop (FUNCTIONALLY COMPLETE)

**Status:** 4 of 5 acceptance criteria met. The loop runs end-to-end nightly on Railway. **Acceptance #3 (a VALIDATED genome that differs structurally from the gen-0 seeds) has never fired** — root cause documented in `v0.4.0 - Capability Expansion` below (evidence: 8 of 9 recent M10 runs produced training Sharpe +1.6 to +3 but OOS Sharpe −0.4 to −1.8 on the same Apr-Jul 2024 OOS slice).

**Theme:** First genuinely agentic increment — a scheduled closed loop that discovers, evolves, and validates trading strategies without human intervention. Discovery and Validation are absorbed into this milestone (no longer separate versions).

**Reference:** Full plan at `~/.claude/plans/i-think-we-need-functional-pony.md`. Architectural pillars summarised below.

**Key technology choices** (live registry — see plan's "Technology Choices" section for revisit triggers):
- Genetic algorithm: `deap>=1.4` + custom mutation/crossover on a Pydantic DSL
- LLM hypothesis injection: `anthropic>=0.40` SDK, `claude-sonnet-4-6` default, prompt caching mandatory, structured output via tool-use
- Agent-to-agent comms: Postgres-mediated, no shared in-memory state
- Orchestration: Railway Cron → `POST /api/v1/evolution-runs` → FastAPI BackgroundTasks → `multiprocessing.Process` per run with `resource.setrlimit()`
- Process architecture: modular monolith on one Railway service; pre-committed split point for `backtest-worker` when run wall-clock exceeds 6h or queue depth >200

### P0 - Must Have

- [x] `DONE` Strategy DSL v1 + compiler + indicator catalog + gen-0 seeds
  - 9 indicators shipped: `close, sma, ema, rsi, atr, bb_upper, bb_lower, donchian_high, donchian_low`. Existing MA/RSI/Donchian re-expressed as DSL seeds.

- [x] `DONE` Extract `evaluate_gate_b()` to `core/gates.py`

- [x] `DONE` Persistence: ORM models + Alembic migration
  - All five tables live in Postgres (`evolution_runs`, `generations`, `strategy_genomes`, `validation_folds`, `decision_logs`)

- [x] `DONE` GA core (tournament-3 + subtree crossover + 5 mutation types + 10% elitism + diversity penalty + 30-genome population)

- [x] `DONE` LLM hypothesis injector — Anthropic SDK, prompt caching, structured output, per-run budget cap, every-N=3-generation injection

- [x] `DONE` Validator agent — K=4 walk-forward, final fold withheld from GA, Gate B applied on OOS

- [x] `DONE` Orchestrator agent + subprocess runner with `resource.setrlimit()` RAM (6 GiB after PR-era raise) + CPU-time caps. All five stop conditions wired.

- [x] `DONE` Backtest dispatch on DSL specs

- [x] `DONE` API surface (`POST/GET/DELETE /evolution-runs`, `POST /stop`, sub-resources for generations / genomes / decisions; `recover_queued_evolution_runs()` in lifespan)
  - DELETE landed in v0.4.0-era PR #6 — TRADER+, refuses with 409 when run is queued or running

- [x] `DONE` Portal: Evolution pages + DSL tree renderer + LineageExplainer + LlmInjectionCard + RunNarrative + FitnessCurveChart + WalkForwardFoldTable + per-fold metrics + fitness breakdown

- [x] `DONE` Decision log on every agent action (observability) — UI surfaces it on the run-detail page

### P1 - Should Have

- [x] `DONE` Backtest perf is comfortable (runs complete in minutes, not hours). Formal `backtest-worker` split has not fired — wall-clock and queue depth stay far under the 6h / 200-job triggers documented in the plan.

- [x] `DONE` `anthropic>=0.40` and `deap>=1.4` in requirements; production deploys use them

- [x] `DONE` Railway nightly cron declared and firing — `scripts/trigger_nightly_evolution.py`

### P2 - Could Have

- [ ] `TODO` Diversity-injection variant using `claude-haiku-4-5-20251001` for cheap high-frequency seeding (deferred — current Sonnet 4.6 cost is well inside the per-run budget)

- [ ] `TODO` Evolution-run export (CSV/JSON of full lineage) — deferred

### Out of Scope (deferred to v0.4.0+)

- Multi-asset / multi-pair within one run
- Regime-aware prompts
- Multi-objective fitness (Sharpe + capacity + turnover)
- Cross-run learning (deferred to v0.5.0)
- Paper / live trading
- Sentiment / news / orderbook data
- Custom user-supplied indicators

### Acceptance for "v0.3.0 done"

1. [x] A scheduled Railway cron triggers a fresh evolution run nightly.
2. [x] The run completes unattended, hits a stop condition, persists VALIDATED genomes for human review.
3. [ ] **OPEN** — At least one VALIDATED genome differs structurally from the gen-0 seeds. *No genome has ever cleared Gate B.* Evidence (12 recent UAT runs analysed): training-fold Sharpes of +1.5 to +3 routinely become OOS Sharpes of −0.4 to −1.8. Three independent runs on the same Apr-Jul 2024 OOS slice all produced negative Sharpe with hit rates 0.35-0.41 — i.e. systematically inverse to training, which is a regime-shift signature, not random GA overfit. Mitigation moved into v0.4.0 (rolling walk-forward + regime tagging).
4. [x] Decision log lets a reviewer reconstruct *why* each surviving genome was kept.
5. [x] LLM cost stays under the configured per-run budget across 5 consecutive nightly runs.

---

## v0.4.0 - Capability Expansion (IN PROGRESS)

**Theme:** Two things at once — close v0.3.0 acceptance #3 (regime-aware validation) AND grow the discovery surface (richer DSL).

**Status:** Partially shipped (per-pair spread snapshot, `min_hold_bars` field, OOS-split tabs, trade-ledger metrics, cost-model rewrite to OANDA AU schedule, deletable runs). Indicators / TOD / multi-symbol / regime-aware validation still to come.

### Recently shipped (May-June 2026)

- [x] `DONE` Per-pair configurable spread snapshot at run-start
- [x] `DONE` `min_hold_bars` DSL field — minimum bars between flips
- [x] `DONE` OOS-split tabs on backtest page + per-trade cost columns
- [x] `DONE` Trade-ledger metrics (compounded, risk-sized) alongside bar-level unit-position metrics
- [x] `DONE` Cost-model rewrite — OANDA AU 21:00 UTC daily rollover, no Wed 3×, slippage default 0.0 (PR #6)
- [x] `DONE` Delete invalid evolution runs from UI (PR #6)
- [x] `DONE` Scrollable evolution form modal (PR #8) + EUR_USD form pin in CI (PR #7)
- [x] `DONE` Read-only investigation script comparing current model against OANDA empirical fills (`scripts/investigate_cost_model_gap.py`)

### P0 — Close the OOS gap (regime-aware validation)

Surfaced by the 2026-06-07 fold analysis: the GA produces consistent training Sharpe but the *single* OOS fold systematically inverts when the most-recent 25% of the data is a different regime. Pure search-space expansion doesn't fix this — we need to change *how we validate*.

- [ ] `TODO` Rolling walk-forward instead of fixed-25%-tail OOS
  - Multiple overlapping OOS folds, e.g. 5 × {train 60% of window → validate next 10%, slide forward}
  - Fitness = MEDIAN OOS Sharpe across folds, not mean training Sharpe
  - **Direct fix for the "good train, bad OOS" pattern** — the average across overlapping OOS slices is less brittle than betting on one
- [ ] `TODO` Regime tagging on training AND OOS folds
  - Compute per-fold features at run-start: realised vol (quintile), trend strength (Hurst or ADX), session mix
  - Reject runs early when train/OOS regime tags disagree past a threshold (saves 30 generations of wasted compute)
- [ ] `TODO` Regime tags as LLM prompt context (was already on the backlog; promoted here)
  - LLM sees "training period regime tag was X, OOS regime tag is Y, propose strategies whose edge survives both"

### P0 — Expand DSL surface

- [ ] `TODO` 5 more indicators: MACD, stochastic, ADX, OBV, ichimoku
- [ ] `TODO` Time-of-day entry filter — restrict entry signals to chosen UTC hour ranges (London/NY session, news-blackout)
- [ ] `TODO` Multi-timeframe references — allow a M5 strategy to reference an H1 indicator value (richer signal hierarchy)

### P1 — Should Have

- [ ] `TODO` Multi-symbol portfolios within one evolution run — design decision pending (portfolio-of-strategies vs. generalised strategy applied per pair)
- [ ] `TODO` Optional Bayesian late-stage refiner — repurpose the existing `BayesianOptimizerAgent` to fine-tune numeric thresholds inside a winning genome shape after the GA selects it

### Acceptance for "v0.4.0 done"

1. At least one VALIDATED genome clears Gate B on a real nightly run (carries over v0.3.0 acceptance #3, finally closed).
2. DSL grammar covers ≥ 12 indicators and time-of-day filters.
3. Rolling walk-forward produces a stable median OOS Sharpe across at least 3 non-overlapping data slices.
4. Per-pair spread accuracy verified against historical OANDA spread data on EUR_USD + GBP_USD + USD_JPY.

---

## v0.5.0 - Self-Improvement (IN PROGRESS)

**Theme:** The platform learns across runs, not just within runs.

### Recently shipped (May-June 2026)

- [x] `DONE` Randomised `rng_seed` per run (was hardcoded 42 — all runs were converging on bit-identical DSL)
- [x] `DONE` Fold-consistency fitness: `mean(per_fold_sharpe) − 0.5·std(per_fold_sharpe)` instead of mean alone
- [x] `DONE` Per-genome training-fold metrics + fitness-breakdown panel in `GenomeDetail`
- [x] `DONE` LLM-injection cards on `EvolutionRunDetail` show per-proposal DSL + reasoning + Δ vs incumbent
- [x] `DONE` Archetype badges on the genome hero (trend-following / mean-reverting / breakout / mixed / degenerate)

### TODO

- [ ] `TODO` Real Evolution agent — distils results across runs into prompt updates and learned DSL priors
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
