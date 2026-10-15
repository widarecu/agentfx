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

## v0.4.0 - Regime-Aware Validation + Core Anti-Overfitting (FUNCTIONALLY COMPLETE)

**Theme:** Close v0.3.0 acceptance #3 (regime-aware validation) AND replace the Sharpe-only fitness function with something aligned to Gate B. DSL / TOD / multi-symbol expansion re-scoped to v0.7.0 — the regime + fitness work was the binding constraint, not DSL surface.

**Status:** Functionally complete for the v0.6.0 handoff. Gate-B clearance carries into v0.6.0 as the paper-trading acceptance signal.

### Shipped (PRs #6 – #19, May – June 2026)

**Cost model + backtest baseline (PRs #6–#8):**
- [x] `DONE` Per-pair configurable spread snapshot at run-start
- [x] `DONE` `min_hold_bars` DSL field — minimum bars between flips
- [x] `DONE` OOS-split tabs on backtest page + per-trade cost columns
- [x] `DONE` Trade-ledger metrics (compounded, risk-sized) alongside bar-level unit-position metrics
- [x] `DONE` Cost-model rewrite — OANDA AU 21:00 UTC daily rollover, no Wed 3×, slippage default 0.0 (PR #6)
- [x] `DONE` Delete invalid evolution runs from UI (PR #6)
- [x] `DONE` Scrollable evolution form modal (PR #8) + EUR_USD form pin in CI (PR #7)
- [x] `DONE` Read-only investigation script comparing current model against OANDA empirical fills (`scripts/investigate_cost_model_gap.py`)

**Rolling walk-forward + regime work (PRs #9, #12):**
- [x] `DONE` Rolling walk-forward validator: 3 training + 5 OOS folds instead of fixed-25%-tail single fold
- [x] `DONE` Per-fold regime tagging on training AND OOS: `volatility × trend × direction` (vol quintiles low/med/high; trend strength ratio; direction ±0.5%)
- [x] `DONE` Regime classifier bug fix on sub-daily granularities (PR #12) — trend ratio was dominated by tick noise on M1; now resampled to daily. Verified against known-directional windows including Q2 2025 EUR/USD +8.4% rally.
- [x] `DONE` Aggregated-OOS Sharpe alongside per-fold median (both surfaced in `WalkForwardFoldTable`)
- [x] `DONE` Sample-size badge on per-fold metrics — grays out per-fold Sharpe when trade count is below reliability threshold

**Warm-start cascade fix trilogy (PRs #13, #16, #17):**
- [x] `DONE` PR #13 canonical-spec exclusion — `find_prior_winners` filters any spec matching a plateau-run best genome
- [x] `DONE` PR #16 deferred warm-start injection — warm-start picks enter the population at gen 10 via periodic injection, not gen 0 elitism
- [x] `DONE` PR #17 champion-by-OOS end-of-run selection — `best_genome_id` reflects OOS robustness, not just training fitness; `top_n_to_validate` bumped 5 → 15
- [x] `DONE` Warm-start source diversification (originally deferred v0.5.0 item, subsumed by PR #16)

**Gate-B-proximity fitness (PRs #18, #19):**
- [x] `DONE` PR #18 fitness function rewrite — raw fitness = 0.35·CAGR_term + 0.25·MDD_term + 0.20·Sharpe_term + 0.10·Hit_term + 0.10·Trades_term − 0.5·std(fold Sharpe), each term normalised so 0 = at Gate B threshold. Retires the mean(sharpe) − λ·std formula.
- [x] `DONE` PR #18 penalty stack cleanup: trade-shortfall / drawdown-breach / Gate-B-dilution removed (folded into proximity terms); zero-trade hard penalty and diversity penalty preserved.
- [x] `DONE` PR #18 LLM prompt updated so proposers optimise against the new fitness definition.
- [x] `DONE` PR #19 `FitnessBreakdown` UI shows explicit fold-consistency penalty row so `weighted sum → −0.5·std → raw fitness` arithmetic is visible.

### Deferred to v0.7.0 (Capability Expansion)

- [ ] `DEFERRED to v0.7.0` 5+ new indicators (MACD/stochastic/ADX/OBV/ichimoku already shipped in PR #10 as part of grammar expansion; further indicators tracked as an extension)
- [ ] `DEFERRED to v0.7.0` Time-of-day entry filter — restrict entry signals to chosen UTC hour ranges
- [ ] `DEFERRED to v0.7.0` Multi-timeframe references — allow a M5 strategy to reference an H1 indicator value
- [ ] `DEFERRED to v0.7.0` Multi-symbol portfolios within one evolution run
- [ ] `DEFERRED to v0.7.0` Optional Bayesian late-stage refiner
- [ ] `DEFERRED pending paper feedback` Bootstrap-consistency penalty on training folds — cheapest incremental anti-overfitting move. Deferred deliberately: further backtest sophistication is a guess without paper-trading feedback. Revisit as v0.6.1 or v0.7.0 based on what paper data reveals.
- [ ] `DEFERRED to v0.7.0` Combinatorial Purged Cross-Validation (López de Prado ch. 7 / ch. 12) — reserved if paper trading confirms training-OOS gap still dominates after Gate-B-proximity + bootstrap-consistency

### Acceptance for "v0.4.0 done"

1. [x] Rolling walk-forward produces a stable median OOS Sharpe across ≥ 3 non-overlapping folds (satisfied by PR #9 5-fold design)
2. [x] Regime classifier correctly labels sub-daily granularities (PR #12)
3. [x] Fitness function directly rewards Gate B clearance criteria (PR #18)
4. [x] Warm-start no longer cascades exhausted specs across runs (PRs #13/#16/#17)
5. [ ] **CARRIES INTO v0.6.0** — at least one VALIDATED genome clears Gate B on OOS. Best current UAT OOS Sharpe is +0.84 (H1 50f984c8), still below the 1.0 threshold. Paper trading is the informative next signal.

---

## v0.5.0 - Self-Improvement (CANCELLED 2026-10-05 — subsumed by v0.8.x Agent Fleet)

**Status (2026-10-05):** CANCELLED as a standalone milestone. The scope (diagnostic failure classification, plateau detection, cross-run distillation, drift detection) is reimplemented as the multi-agent **Agent Fleet** at v0.8.x — Quant Analyst Agent + Research Agent + Portfolio Manager + Platform Engineer, orchestrated via Claude Agent SDK with Managed Agents workspaces for autonomous code iteration. See `## v0.8.0 - Agent Fleet` below. Items in the "Recently shipped" subsection remain valid.

**Theme (historical):** The platform learns across runs, not just within runs.

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
- [ ] `TODO` **Warm-start source diversification** — `find_prior_winners` currently picks top-K unique specs by fitness across the whole pool. A run that finds a strong elite produces many parametric variants (different float thresholds, slightly different indicator windows) that all dedup as distinct under byte-comparison but represent the same strategy family. Result: when one historical run is meaningfully stronger than the others, warm-start can fill all K slots with near-elite variants from that one run, missing the elites of other runs. Concrete fix: in `_add_until_full`, first pass takes at most 1 spec per `source_run_id`, then top up by fitness only if K isn't met. ~10 lines. **Why deferred:** the binding constraint right now is "no genome has cleared Gate B," not "we have too many winners to mix well." Once we have at least one Gate-B-passing strategy, the diversity-vs-depth trade-off in warm-start becomes the real concern. See the 2026-06-13 thread in conversation history for the full debate. (Surfaced by the user when reviewing the post-step-2 runs `evorun_20260613_8575da19` / `evorun_20260613_4e96b20a`.)

### Diagnostic Evaluation Agent — the missing feedback layer (2026-06-13 vision)

The current evolve → evaluate → repeat loop is optimization without learning. The missing piece is an agent that examines each underperforming or rejected genome and answers *why* it failed, then converts that diagnosis into a concrete next-iteration instruction. Below is the breakdown of what that agent needs to ship. See the ROADMAP.md "North Star" section + v0.5.0 description for the full framing.

- [ ] `TODO` **LLM-driven failure classifier** — given a genome + its rolling-fold metrics + regime tags, output a structured diagnosis: `flawed_dsl` | `regime_mismatch` | `cost_dominated` | `risk_bottlenecked` | `unknown`. Reasoning text persisted to `decision_log` for audit. Uses Anthropic SDK with prompt caching (rolling state is the only varying input).
- [ ] `TODO` **Action proposer** — each diagnosis maps to a concrete instruction for the next iteration:
  - `regime_mismatch` → "promote to conditional-edge portfolio for regime X, exit active evolution loop"
  - `cost_dominated` → "propose a coarser-timeframe variant of this DSL"
  - `flawed_dsl` → "this spec violates principle P; the next LLM proposal must avoid pattern Q"
  - `risk_bottlenecked` → "stop / target / size needs rebalance; suggest specific changes"
  - `unknown` → flag for human review
- [ ] `TODO` **Feedback ingestion into LLM injection** — the next-generation injection prompt sees recent diagnoses as context, not just recent top genomes. The model proposes strategies that explicitly address the diagnosed weaknesses.
- [ ] `TODO` **Per-genome diagnosis surfaced in the portal** — next to the fitness breakdown and conditional-edge badge. A human can audit the agent's reasoning by clicking through.
- [ ] `TODO` **Plateau detection** — N consecutive cycles with no improvement AND no diagnostic-driven search-space change triggers a "we've exhausted this configuration" signal. The orchestrator halts re-evolution and either asks the human or escalates to a higher-level pivot (different instrument / timeframe / DSL extension).
- [ ] `TODO` **Negative-progress detection** — if diagnostic-proposed changes consistently produce worse OOS results across N cycles, the diagnostic agent itself becomes the subject of review. Surfaces the agent's prompt / assumptions for human inspection. The platform must distrust its own feedback loop when evidence demands it.
- [ ] `TODO` **Graduation logic** — a genome that clears Gate B AND has a stable regime-conditional activation policy moves out of the active evolution loop into the v0.6.0 portfolio queue. Further evolution effort shifts to filling regime coverage gaps (regimes the portfolio doesn't yet cover), not re-validating proven slots.

**Specific failure modes the agent must learn to classify** (added 2026-06-13 from analysis of `evorun_20260613_ddf434f8` / `evorun_20260613_f753385e`):

- [ ] `TODO` **`no_directional_thesis`** — the DSL has no signal that determines *which way* to trade, only filters (e.g. `atr(20) < atr(63)` is a vol filter, not a direction signal; firing it as a long trigger means "go long whenever vol is contracting" with no price-direction logic). The high training Sharpe is coincidence between the filter's firing pattern and the training-window drift; OOS reveals it as 50/50 noise. The diagnostic agent should detect this structurally — flag specs where the long or short side lacks any price-direction component — and reject the genome before it reaches the validator.
- [ ] `TODO` **`small_sample_overconfidence`** — when per-fold OOS trade counts are small (e.g. 20–30), individual Sharpe estimates have wide confidence intervals (~±2 around the true value with 25 trades). Median across 5 noisy folds is still noisy. The agent should compute the *aggregated-OOS Sharpe* (one Sharpe on the concatenated OOS trade ledger, 100+ trades) alongside the per-fold median, and flag any genome where the per-fold median differs meaningfully from the aggregated Sharpe — that gap is the noise signature. Also surface this in the conditional-edge classifier: "5 folds tagged 'down' had Sharpe ranging −4.4 to +5.1, mostly sample noise" is a *different* finding from "5 folds tagged 'down' consistently positive."
- [ ] `TODO` **`ga_selection_bias_on_noisy_signal`** — when training-fold Sharpe range is large (e.g. 2.0 spread across 3 training folds with ~100 trades each), the GA's selection across N candidates ≈ picking the top of a noisy distribution. Training fitness is then a biased upper estimate, not the true expected value. The agent should detect this by checking training-fold *variance* against expected noise variance for the trade count, and discount fitness when the variance is consistent with pure noise. Concretely: a genome with training Sharpes [+2.3, +3.5, +0.7] and ~100 trades/fold has Sharpe variance > what 100-trade samples of a true-Sharpe-2.0 process would produce, so the +2.0 mean is partly noise-selected.

**Statistical-power improvements to the validator** (post-v0.4.0, pre- or part-of v0.5.0):

- [ ] `TODO` **Aggregated-OOS Sharpe** — alongside median across per-fold Sharpes, compute one Sharpe on the concatenated OOS trade list. Statistically much more powerful (1 estimate on 100+ trades vs 5 estimates on 25 each). Show both in the UI. When the aggregated-OOS Sharpe is positive but the median is noisy, that's a stronger signal of real edge than the median alone. When they agree, confidence is highest.
- [ ] `TODO` **Sample-size badge on per-fold metrics** — UI shows a small confidence-interval indicator (or grays out) per-fold Sharpe when trade count is below a threshold where the estimate is reliable. Stops reviewers from over-reading individual fold numbers.
- [ ] `TODO` **Gate B trade-count threshold revisit under rolling-walk-forward** — the existing 300-trade minimum was calibrated for the v0.3.0 K=4 single-OOS-fold design where 300 trades in one contiguous slice provided statistical power for the Sharpe estimate. Under the v0.4.0 rolling 5-fold design, summing across folds preserves total-trade arithmetic but changes the statistical interpretation: 300 distributed across 5 folds = 60/fold has lower per-fold confidence than 300 contiguous. Surfaced by `evorun_20260614_433dfbdb` (2026-06-14) where a clean RSI-overbought short-only mean-reversion strategy clears every other Gate B threshold (Sharpe +2.44, CAGR 21.7%, hit 51.4%) and fails ONLY on `Trade Count 202 < 300`. **Don't lower the threshold to make this specific case pass** — the principled answer is to *raise* the bar by also requiring aggregated-OOS Sharpe (see preceding bullet) to clear Gate B with appropriate sample-size adjustment, so a strategy passes only when BOTH the per-fold median AND the concatenated-trade Sharpe are statistically meaningful. Calibrate the new dual threshold against the body of validated genomes once we have ≥ 10 to compare.
- [ ] `TODO` **Training-fold variance penalty** — extend the fold-consistency penalty (`mean − 0.5·std`) to factor in expected variance at the training sample size. A wide training-fold spread on small samples may be pure noise; a wide spread on large samples is real instability. Currently we treat both the same.

**Acceptance for diagnostic agent v1:**
1. Every failed/underperforming genome has a structured diagnosis with audit-trail reasoning in the portal.
2. At least one end-to-end cycle exists where a diagnosis produced a concrete change that demonstrably improved the next cycle's OOS result (measurable feedback loop closure, not anecdotal).
3. Plateau detection successfully halts a stuck configuration without manual intervention.
4. The classifier handles the three specific failure modes documented above (`no_directional_thesis`, `small_sample_overconfidence`, `ga_selection_bias_on_noisy_signal`) — verified on at least one historical example of each.

---

## v0.6.0 - Paper Trading MVP — UX-first, multi-tenant, per-user credentials

**Theme:** Any TRADER user can securely deploy any of their VALIDATED genomes to their own OANDA Practice account, monitor live P&L vs backtest, and kill-switch the run — with zero cross-user data leakage.

**Delivery order (hard-gated, sequential):** planning docs → UX design (mockups signed off) → RBAC/isolation refactor → per-user credential storage → OANDA order execution → paper runner service → portal pages → integration + E2E + UAT deploy. Backend PRs do not open until Wilson signs off on the UX mockups. See the plan in `/Users/wilson/.claude/plans/i-think-we-need-functional-pony.md` for phase-by-phase detail.

**User-approved decisions** (locked before UX design):
- Concurrent paper runs per user: **unlimited (soft cap)**
- Approval workflow: **separate approval queue** (`VALIDATED → AWAITING_APPROVAL → APPROVED → RUNNING`)
- Kill switch: **big red button + 3-second undo toast** (no modal, no type-name confirmation)
- Notifications: **deferred to v0.6.1** (no Email / Slack in MVP)

### User stories

- [x] `DONE` [US-017 — Paper Trading UX design lockdown](requirements/US-017-paper-trading-ux-design.md)
- [x] `DONE` [US-018 — Per-user RBAC filter on runs, jobs, genomes](requirements/US-018-rbac-user-isolation.md) — closed the pre-existing security bug where any authenticated user saw all runs. `api/routers/paper.py` `require_admin_for_scope_all` + `created_by_filter`; `list_approval_queue_for_user(user_id)`.
- [x] `DONE` [US-019 — Per-user OANDA credentials, encrypted at rest](requirements/US-019-per-user-oanda-credentials.md) — `api/models/user_credentials.py` (Fernet `oanda_api_key_encrypted`, `key_version=1`), `api/core/secrets.py`, `CredentialsForm.tsx`.
- [x] `DONE` [US-020 — OANDA order-execution adapter methods](requirements/US-020-oanda-order-execution.md) — `client.place_market_order` wired at `paper_runner.py`; tuning in `b84644c`, `398a6c0`.
- [x] `DONE` [US-021 — Paper runner service + subprocess model](requirements/US-021-paper-runner-service.md) — `services/paper_runner.py` (2400+ LOC) + `api/workers/paper_runner.py` supervisor.
- [x] `DONE` [US-022 — Paper trading portal pages (`/settings`, `/paper`, `/paper/approvals`, `/paper/:id`)](requirements/US-022-paper-trading-portal.md) — `PaperRuns.tsx`, `PaperRunDetail.tsx`, `Settings.tsx`, `ApprovalQueue.tsx` plus mobile variants.
- [x] `DONE` [US-023 — Kill switch + risk controls + approval state machine](requirements/US-023-kill-switch-risk-controls.md) — `KillSwitchButton.tsx`; `models/run.py` `kill_requested_at`, `risk_config`, `stop_reason=USER_KILL`.

### Deferred to v0.6.1

- [x] `DONE (v0.6.2)` ~~Email / Slack notifications~~ **Email notifications** for approval requests, kill events, risk-control triggers. Ship history: Slack-webhook first (v0.6.2, since removed) → replaced with SendGrid SMTP email in 2026-09-29 (`352b1e4`, `19dc281`, `92f1dc6`). `notifications/email.py`, env-tagged subjects (`[AgentFX UAT|STAGING|PROD]`). Kill + halt + crash events wired; approval-request notifications intentionally NOT emailed (they land in the in-portal queue, no operator interruption warranted).
- [ ] `DEFERRED to v0.6.1` Live-vs-validation drift alerts as a separate UI surface (basic overlay chart is in MVP)
- [ ] `DEFERRED to v0.6.1+` OANDA v20 pricing stream (WebSocket) — replaces 60s polling if latency becomes a blocker
- [ ] `DEFERRED to v0.7.0` Fernet key rotation → `key_version=2` on `user_oanda_credentials`
- [ ] `DEFERRED to v0.7.0` ADMIN visibility of other users' *masked* credential status (for support)

## v0.6.1 - Paper Trading Live Execution & Observability

**Theme:** Turn the v0.6.0 paper platform from a UX skeleton into a working strategy evaluator — the runner actually trades, and the run-detail page surfaces what it did.

**Plan document:** [V0.6.1_IMPLEMENTATION_PLAN.md](V0.6.1_IMPLEMENTATION_PLAN.md) — phase-by-phase PR sequencing + user-approved decisions.

**UAT verification runbook (exit gate before tagging):** [runbooks/v0.6.1-uat-verification.md](runbooks/v0.6.1-uat-verification.md) — step-by-step checklist Wilson executes on the UAT stack + 48-hour observation window.

**Genesis:** The 24-hour UAT smoke-test on 2026-07-08 flushed out that `paper_runner._execute_loop` shipped as a monitoring skeleton — polls balance + risk gates but never fetches candles, compiles the DSL, or places orders. Wilson's real paper run halted on 2026-07-09 with `stop_reason='USER_KILL_v0.6.0_gap'` after 24h with zero trades. Every other observability gap the smoke-test surfaced (empty Trades table, missing decision log, curve resets on reload, placeholder backtest overlay) presupposes the runner is executing, so v0.6.1 is a single coherent live-execution sprint plus the validator write-back bug that keeps VALIDATED genomes out of the approval queue.

### User stories

- [x] `DONE` [US-024 — Paper runner live DSL execution](requirements/US-024-paper-runner-live-execution.md) — `paper_runner.py` imports `StrategySpec/compile_spec`, validates DSL, calls `place_market_order`. Walk-driven signal tick shipped in `398a6c0` (677-line rewrite).
- [x] `DONE` [US-025 — Trades / decisions / equity read endpoints](requirements/US-025-paper-run-read-endpoints.md) — `/paper-runs/{id}/trades`, `/decisions`, `/equity` in `api/routers/paper.py`; frontend `PaperRunTradesTable.tsx`, `PaperRunDecisionLog.tsx`.
- [x] `DONE` [US-026 — Persisted equity snapshots + backtest OOS overlay](requirements/US-026-persisted-equity-and-backtest-overlay.md) — migration `2026_07_10_1000_add_paper_equity_snapshots.py`; endpoint `/paper-runs/{id}/backtest-overlay`; `LivePnLChart.tsx` (UAT-driven fixes 2026-07-24 to 2026-07-28).
- [x] `DONE` [US-027 — Approval queue diagnostics + ADMIN validator override](requirements/US-027-approval-queue-diagnostics.md) — 2026-07-09 audit found the validator write-back is working (255 correct `false` verdicts on Wilson's UAT DB). The queue is empty because 0/2,040 OOS folds ever cleared Gate B, not a bug. Shipped: empty-state diagnostic banner (`_pickEmptyBannerScenario` at `ApprovalQueue.tsx:168`), `ApprovalQueueMeta` counts + `top_close_to_passing` list, ADMIN-only `USER_VALIDATOR_OVERRIDE` DecisionLog audit trail. Frontend tests (`ValidatorOverrideModal.test.tsx` + `ApprovalQueue.test.tsx`) still not written — v0.7.0 housekeeping backlog item.
- [x] `DONE` [US-028 — DeployPaperModal SL/TP fields](requirements/US-028-deploy-modal-sl-tp-fields.md) — SL/TP NumericField rows shipped under "Advanced risk controls"; `run.risk_config.stop_loss_pips` + `take_profit_pips` passed to `client.place_market_order`.

### Deferred to v0.6.2

- [x] `DONE (v0.6.2)` LLM-suggested deployment name on DeployPaperModal (`c2db0a5`, 2026-08-03). Endpoint `POST /paper-runs/suggest-name` (`api/routers/paper.py`); service `api/services/paper.py::suggest_deployment_name`; UI in `DeployPaperModal.tsx`.
- [x] `DONE (v0.6.2 → migrated 2026-09)` Terminal + crash-event notifications. Initial ship was Slack (`notifications/slack.py`, `SLACK_WEBHOOK_URL`) — hit mobile-push reliability issues on Wilson's phone. **Replaced with email** on 2026-09-29 (`352b1e4`, `19dc281`, `92f1dc6`): `notifications/email.py` (SendGrid SMTP via `SMTP_HOST/PORT/USER/PASSWORD/EMAIL_FROM/EMAIL_TO`); subject prefixed with `[AgentFX <ENV>]`; 5xx rejections classified non-retryable. Idempotency invariant preserved via `run.parameters['notified']`. `SLACK_WEBHOOK_URL` retained as deprecated no-op for rollback; slated for removal in v0.8.0 housekeeping.
- [ ] `DEFERRED to v0.6.2` OANDA v20 streaming pricing (replaces `get_pricing` polling on the signal tick).
- [ ] `DEFERRED to v0.6.2` Live-vs-validation drift dashboard as a separate UI surface.

---

## v0.6.3 — Paper Runner Reliability (2026-08 → 2026-09)

**Theme:** Turn the paper runner from "works on the happy path" into
"survives real UAT wall-clock" — subprocess crashes recover
automatically, OANDA API weirdness doesn't strand positions, and the
DB never disagrees with the broker without an operator seeing an alert.

**Genesis:** A UAT paper run halted with the DB marked flat while
OANDA still held units (phantom-flat state). Root cause chain:
`close_all_positions` short-circuited to success when a stale
`get_position` returned 0/0 during OANDA maintenance; runner marked
trades closed; subprocess later died silently; supervisor's zombie
detection didn't fire; mismatch reconciler never reached its check.
Six commits over August/September closed the loop.

### Shipped

- [x] `DONE` Auto-recovery: wide retry + supervisor restart scan (`3bb47bf`) — `api/workers/paper_runner.py::scan_and_restart_transient_errors` + `scan_and_restart_zombie_running` scheduled from `api/main.py` (`paper-runner-auto-restart-supervisor`).
- [x] `DONE` Stranded-position + zombie-runner recovery scaffolding (`da63de7`) — heartbeat + pid tracking in `services/paper_runner.py`; `run.parameters['last_heartbeat_at']`, `run.process_pid`.
- [x] `DONE` Position-mismatch check fires + stranded-position auto-adopt (`44368c9`) — `_check_position_mismatch` + `_adopt_stranded_position` in the reconciler; creates fresh Trade row or reopens phantom-closed one.
- [x] `DONE` Verify close actually closed before marking DB flat (`9bdaba6`) — `_flatten_and_halt` cross-checks `close_all_positions` return dict + `get_position` re-fetch; writes `USER_POSITION_STRANDED` if broker still holds units.
- [x] `DONE` Unconditional log line in position-mismatch check (`d63622a`) — diagnostic to catch cases where the check silently doesn't fire.
- [x] `DONE` Survive OANDA balance-refresh timeouts + rename `daily_loss_pct` (`29fb008`).

### v0.6.3 additional shipped items

- [x] `DONE` DSL `eval_mode: "close" | "wick"` (`fc87230`) — new `Literal["close", "wick"]` field on `core/dsl/grammar.py`, wick-touch SL/TP semantics in `core/dsl/compiler.py`, aligned runner path in `paper_runner.py`. Closes the runner-vs-backtest divergence surfaced by UAT 2026-07-28.
- [x] `DONE` OANDA account exclusivity guards + missing run-name display (`248da49`) — prevents two paper runs from sharing the same OANDA account; adds `OandaAccountsTable.tsx`.
- [x] `DONE` Broker order carries only wide fail-safe SL, no TP (`b84644c`) — broker touch-SL vs backtest close-SL divergence fix.
- [x] `DONE` Walk-driven signal tick — restores backtest fidelity during `min_hold` (`398a6c0`).
- [x] `DONE` Relabel loss-cap fields as soft/hard halt tiers (`3135d06`) — frontend copy across four pages.

### Open

- [ ] `TODO` Post-mortem the phantom-flat DB incident properly in `planning/runbooks/` — cause, detection, fix, and what to look for if it recurs. Currently only lives in commit messages + this session's transcript.
- [ ] `TODO` Alert on stranded-position adoption events specifically (not just the generic halt reason). Wilson should know the moment the runner had to reopen a phantom-closed trade — currently rolls into the generic email.
- [x] `DONE (v0.6.2)` Remove `max_position_size_units` editable input from DeployPaperModal (Wilson UAT 2026-07-19). Dynamic sizing (`balance × risk_pct ÷ stop_pips`) is the only ceiling the runner respects; OANDA's own margin/leverage checks reject over-sized orders. Field removed; legacy `risk_config` JSON blobs may still carry the key and the runner tolerates that.

<!-- Historical raw scope notes from 2026-07-08 / 2026-07-09 preserved for archaeology; the executable list above is what future readers act on.

### v0.6.1 — Known gaps surfaced during UAT smoke-test (2026-07-08)
- Validator write-back bug (approval queue permanently empty on real runs) → now US-027.
- Trades endpoint + trades table → part of US-025.
- Decisions endpoint + decision log stream → part of US-025.
- Equity endpoint (page-reload survival) → part of US-025 + US-026.
- Backtest OOS overlay data → US-026.
- LLM-suggested deployment name → deferred to v0.6.2.

### v0.6.1 — Critical gap (2026-07-09)
- Runner is a monitoring skeleton (grep confirmed no `generate_signals` / `place_market_order` / `StrategySpec`). Wilson's 24h UAT run halted with `stop_reason='USER_KILL_v0.6.0_gap'` — folded into US-024.
- Adjacent decisions (candle staleness, cadence architecture, streaming, order timing, position flip, SL/TP sourcing) locked in V0.6.1_IMPLEMENTATION_PLAN.md.
-->


---

## v0.7.0 — DSL / evolution guardrails (ABSORBED 2026-10-05 into v0.8.x PE workload)

**Status (2026-10-05):** v0.7.0 is no longer a standalone milestone. The four open items below become Platform Engineer Agent (v0.8.1) tasks picked up on-demand when the Quant Analyst identifies DSL-level failure modes in paper divergence reports. If PE opens PRs covering all four, this milestone retires inside the v0.8.x window; otherwise the remainder rolls into v1.0.0's hardening tail.

**Theme (historical):** Prevent silent runner-vs-backtest divergence from ever
happening again by tightening what the evolution/validator layer
allows into the paper-deployment queue.

### Wilson UAT 2026-07-28 — cumulative-indicator drift

The `genome_gen_evorun_20260712_30f6bc87_017_019` paper run
diverged from the backtest by 42% of signal bars because the DSL
used `obv < 0` (a cumulative-sum indicator compared to an absolute
constant). Live OBV cumulates from run start; backtest OBV
cumulates from fold start; the same bar has different OBV values
under each anchor, so the boolean flips on completely different
bars. Fix A (paper runner uses fold-length-anchored candle window,
matching validation-fold OBV cumulation) resolves this specific
run. This section covers guardrails for FUTURE genomes.

- [ ] `TODO` **DSL validator warning for start-point-sensitive
  indicators.** Static-analyse every genome's `dsl_json` at
  validator time. If any leaf indicator is state-dependent (`obv`,
  `ad_line`, or any indicator whose body contains `cumsum`) AND
  it's compared to an absolute constant (not to another indicator
  or moving average of itself), emit a soft warning attached to
  the genome row: "This indicator is start-point sensitive; its
  live value at bar T depends on where cumulation started. The
  paper runner anchors at the validation fold length to
  approximate training behaviour, but portable formulations
  (`obv < sma(obv, N)`, `obv_change`, etc.) generalise across
  time windows without this anchoring dependency."

- [ ] `TODO` **Grammar-level guidance on cumulative comparisons.**
  Update `core/dsl/grammar.py` doc-strings for OBV / AD_LINE /
  future cumulative indicators to explicitly document the
  anchor-sensitivity and recommend relative comparisons. Add a
  `docs/dsl-writing-strategies.md` best-practices note that the
  LLM discovery agent (`agents/discovery.py`) reads before
  proposing new genomes.

- [ ] `TODO` **Evolution/validator consistency audit.** During
  training, the walk-forward evaluator computes per-fold metrics
  with each fold's own OBV cumulation window (fold-anchored).
  The on-demand backtest endpoint
  (`/evolution-runs/{id}/genomes/{gid}/backtest?window=oos`)
  runs ONE continuous backtest across all OOS folds (window-
  anchored). Users comparing paper-vs-backtest see different
  numbers depending on which reference they pick. Refactor the
  backtest endpoint to walk fold-by-fold when `window=oos` so
  the reference matches training methodology.

- [ ] `TODO` **Compare-mode UI badge on genome detail.** When a
  genome has the warning above, surface it as an amber chip on
  the approval-queue row + the deploy modal, with a hover
  tooltip explaining that live-signal frequency may drift from
  backtest under different market regimes.

---

## v0.7.x — Cross-user genome sharing (import/export)

**Shipped:** commits `ce50e22` (backend + frontend + migration), `ab4a812` (warm-start isolation), `f35f8c5` (JS-compatible content hash + Toast wrap). Verified during 2026-08 UAT sessions.

**Theme:** Enable users to exchange validated strategies. Users A
and B are on separate OANDA accounts and independent evolution
tracks; today there is no way for A to hand B a promising genome
so B can evaluate it against their own conditions and, if they
approve, deploy it to their own paper account.

### Wilson request 2026-08-02

Full spec below. Every piece is deliberate — the approval queue,
deploy modal, paper runner, fold-anchored candle fetch, backtest
endpoint, and lineage view all read genome metadata in specific
shapes. A naive "just copy the dsl_json" import breaks the
approval queue (which filters via `evolution_run.created_by ==
user_id`), the fold-anchored fetch (which needs `validation_folds`
attached), and the compare-vs-backtest reference (which reads
`fitness` + fold metrics).

### Data to export

The exporter's genome + everything needed for the importer to
evaluate + deploy WITHOUT the exporter's evolution run rows:

- **Genome core**: `id`, `dsl_json`, `fitness`, `origin` (llm /
  mutation / crossover / seed), `llm_reasoning` (nullable — for
  attribution), `validator_passed`, `validation_failures`, DSL
  grammar `version`.
- **Validation folds** (all rows): `fold_index`, `fold_start`,
  `fold_end`, `is_oos`, `sharpe`, `max_drawdown`, `hit_rate`,
  `n_trades`, `cagr`, `passed_gate_b`, regime tags.
- **Provenance**: exporter's user email + display name (opt-in),
  original evolution_run `instrument` + `granularity` + `id`,
  export timestamp, exporter's app version.
- **Signature**: HMAC over the payload with a shared secret, or
  ECDSA if we ever want cross-instance verification. v1: SHA-256
  content hash for tamper detection (not authentication).

### File format

`{genome_id}.agenome.json` — plain JSON, schema-versioned:

    {
      "schema_version": 1,
      "exported_at": "...",
      "exported_by": {"email": "...", "display_name": "..."},
      "app_version": "0.7.x",
      "genome": { ...core fields... },
      "validation_folds": [ ... ],
      "provenance": {
        "source_evolution_run_id": "evorun_...",
        "source_instrument": "EUR_USD",
        "source_granularity": "M1",
      },
      "content_hash": "sha256:...",
    }

### Import flow

1. Frontend: **Import Genome** button on Approval Queue page →
   file picker → POST `/genomes/import` with the JSON payload.
2. Backend validates:
   - `schema_version` supported
   - `content_hash` matches recomputed hash
   - DSL grammar version compatible with current build
   - DSL passes static validation (`_check_indicator_call` etc.)
   - Instrument is one the importer has candles for (or offer to
     backfill)
3. Backend creates SHADOW rows scoped to the importer:
   - New `EvolutionRun` row with `created_by = importer.user_id`,
     `id = "imported_" + original_evolution_run_id`, tagged with
     `origin_kind: "import"` and `exported_by_email` on a new
     column so the UI can attribute it.
   - Corresponding `Generation` shadow row.
   - New `StrategyGenome` row with a NEW id (`imported_gen_...`
     prefix) so it can't collide with the exporter's if they're
     on the same DB.
   - `ValidationFold` rows duplicated with new ids and
     `genome_id` FK pointing at the shadow genome.
4. Importer's approval queue picks up the new genome (its shadow
   `EvolutionRun.created_by == user_id` gate).
5. Importer's deploy flow uses it exactly like a native genome —
   `approve_and_deploy` reads `genome.validation_folds` to compute
   `fold_length_days` for Fix A. Since we duplicated the folds
   verbatim, this Just Works.

### UI surfaces

- **Approval queue row**: imported genomes get an amber `IMPORTED`
  chip with hover tooltip "Shared by wilson@… on 2026-08-15."
- **Deploy modal**: same chip near the genome id. No behavioural
  change — importer still owns the paper run outright.
- **Evolution runs list**: shadow `EvolutionRun` rows show under a
  separate "Imported strategies" tab so they don't pollute the
  user's own evolution tracks.
- **Lineage tree**: shadow runs have no parent_ids (empty family
  tree). Rendered as a single node with the `IMPORTED` chip.

### Compatibility checkpoints (verify each)

- [x] Approval queue query (`list_approval_queue_for_user`) — the
  `evolution_run.created_by == user_id` filter already scopes to
  the importer; verified naturally by using shadow rows.
- [x] Deploy flow (`approve_and_deploy`) — reads
  `genome.validation_folds` for `fold_length_days`. Folds ARE
  duplicated on import; `api/services/paper.py` computes
  `fold_length_days` from imported folds.
- [x] Fold-anchored paper runner (Fix A) — reads
  `run.parameters['fold_length_days']` set at deploy time. Works.
- [x] Backtest re-run endpoint
  (`/evolution-runs/{id}/genomes/{gid}/backtest`) — shadow rows
  carry `data_range_start/end` verbatim from the source snapshot.
- [x] Lineage tree (`GenomeLineageTree.tsx`) — shadow genome has
  empty `parent_ids`; single-node render confirmed, no crash.
- [x] Notifications (v0.6.2 email) — same as native genomes; no
  change needed. (Line originally said "Slack" — Slack was
  replaced with email in 2026-09; behaviour is identical.)
- [ ] Validator warnings (v0.7.0 items above) — re-run on import
  so the importer sees the same amber chip if the DSL uses
  start-point-sensitive indicators. **Blocked on v0.7.0 not
  started** (the cumulative-indicator warning itself doesn't
  exist yet).

### Attribution + trust posture

v1: no signature verification, no allowlist. Anyone can send anyone
an `.agenome.json` file; importer trusts it because they got it
from a person they trust. Content hash detects file corruption /
tampering but doesn't authenticate the sender. Sufficient for the
"share with a friend" workflow.

v2 (if needed): sign exports with the exporter's platform account
key so `exported_by.email` is provable. Add a "Received from" ledger
so users can audit the provenance of every deployed strategy.

### Test scope

- Round-trip: export a genome → parse → re-import as a different
  user → verify approval queue picks it up → verify deploy path
  writes correct `fold_length_days`.
- Version-skew: export from schema_version=1, import into a build
  supporting v2 → schema upgrade path.
- Grammar-skew: export uses DSL feature the importing build
  doesn't support → clean rejection with a specific error, not a
  500.
- ID collision: export from a DB where genome_id happens to
  already exist as an import from another user → new id must not
  collide.

### Non-goals

- Cross-DB import (e.g. moving genomes between staging and prod).
  All imports are within one platform instance.
- Runtime dependency on the source platform being up. Import
  payload is self-contained.
- Editing the DSL after import. Importer can only approve/reject
  the genome as-is. Modifications require running their own
  evolution.

---

## v0.7.x — Mobile-first paper-run monitoring view — **DONE**

**Shipped:** 2026-08-03. Commits `9b909ff` (initial view + 4 sub-routes
+ router branch + collateral desktop fixes), `7844f94` (SL/TP genome
DSL fallback + trades card layout + open-first sort — Wilson UAT
phone-test corrections), `3100a71` (chart marker window fix). Verified
by Wilson on iPhone via staging. `MobilePaperRunDetail` + 4 sub-routes
at `/paper/:runId/{chart,trades,decisions,risk}`; `PaperRunDetail`
branches on `useIsMobile()` at <640px.

Follow-ups deferred:
- Playwright test at 375×667 viewport (unit tests shipped for the
  pure helpers only — `resolveSlTpPips`, `sortTradesOpenFirst`,
  `_computeScaleInner`, `buildActivity`).

Original theme kept below for context.

**Theme:** Portal is unusable on mobile. `/paper/:id` renders 8
tiles + full equity chart + full price-and-indicators chart +
trades table + decisions log; on a phone screen tiles wrap
awkwardly, charts don't downscale, tables scroll horizontally,
and the whole thing is too dense to actually monitor a running
strategy on the go.

### Wilson request 2026-08-02

"When I try to access my staging environment from mobile it is
quite unusable, as things are not rendering properly, we should
have a lighter set of information with the most meaningful set of
information a mobile user would need while monitoring a run."

### What a mobile user actually needs (in priority order)

1. **Status pill** — RUNNING / HALTED / ERROR / etc. with visual
   colour. First glance answer to "is it still on?"
2. **Live P&L headline** — `-$1,477 · -1.54%` and elapsed time.
3. **Open position card** — side / size / entry price / unrealised
   P&L / duration held. Empty state "no open position" when flat.
4. **Kill switch** — big red button, always accessible.
5. **Last 5 trades** — compact rows: `date · side · pnl · reason`.
6. **Last 3 decisions** — timestamped, one-line, colour-coded by
   severity (stale/skip = amber, error = red, order = green).
7. **Sparkline equity chart** — one-line height, 24h window,
   no axes, no tooltip. Just gives the shape of the day.

### What can be hidden / progressively disclosed

- Full equity chart with tiles, drawdown, overlay — collapsed
  behind "Show detail" toggle.
- Price + indicators chart — collapsed behind toggle.
- Full trades table — link to a `/paper/:id/trades` sub-route.
- Full decisions log — link to a `/paper/:id/decisions` sub-route.
- Backtest OOS overlay explanations — hidden by default (they're
  chatty).

### Design decisions to make

- **Route strategy**: same URL, media-query breakpoint at ~640px
  → different top-level layout component? OR separate
  `/paper/:id/mobile` sub-route? Suggest media-query approach so
  desktop→mobile via window resize doesn't require nav.
- **Chart library**: LightweightCharts (used for price chart)
  supports mobile touch out of the box but the paper chart uses
  raw SVG. Sparkline probably needs a simple SVG polyline — small
  enough to hand-roll, no new dep.
- **Kill switch layout**: sticky footer or sticky top? Sticky top
  keeps status + kill co-located. Suggest sticky top on mobile.
- **Auto-refresh cadence**: same 60s / 5s cadences as desktop.
  Mobile users on cellular shouldn't hammer the backend more than
  desktop does.
- **Landscape**: not optimised in v1; portrait only. Note in the
  layout copy.

### Implementation shape (frontend only, no backend change)

- New `MobilePaperRunDetail.tsx` component with the priority-list
  above. Chooses the layout based on `useMediaQuery('(max-width: 640px)')`.
- Sub-components extracted from the desktop version where
  practical: `KillSwitchButton`, `SummaryStripTile` (with a
  `variant="compact"` prop) — keep one source of truth for the
  data.
- `PaperRunDetail.tsx` becomes a router that picks Mobile vs
  Desktop based on the media query, so the URL and data-fetching
  effects don't change.

### Non-goals

- Redesigning the desktop view. Only the mobile breakpoint.
- Mobile app (native / PWA). Just a responsive web layout.
- Charts as complex as desktop (no drawdown shading, no OOS
  overlay, no financing markers). Sparkline only.
- Approval queue / evolution-run pages — separate scope, tackle
  after paper-run monitoring lands.

### Tests

- Playwright test at 375×667 viewport (iPhone SE) verifying:
  - Kill switch visible without horizontal scroll
  - Status pill + P&L visible in first viewport
  - Trades table doesn't overflow
- Existing desktop Playwright tests continue to pass (breakpoint
  gates on >640px).

---

## v0.8.0 — Agent Fleet: read-only pilot

**Theme:** Introduce the first two Claude agents (Portfolio Manager + Quant Analyst) in **read-only mode** — they observe platform state, produce morning briefs + divergence reports, but take no actions. Earns Wilson's trust in the agents' judgement before any authority is granted.

**Plan document:** `~/.claude/plans/i-think-we-need-functional-pony.md` — approved 2026-10-05.

**Branch strategy:** All work on `feat/agent-fleet`. Staging keeps deploying from `main`. Wilson gates the final `feat/agent-fleet` → `main` merge.

**Cost envelope:** $20-30/month total (LLM + Managed Agents compute).

### User stories

- [ ] `TODO` [US-029 — Agent orchestration layer (Claude Agent SDK + lifespan hook)](requirements/US-029-agent-orchestration-layer.md) — `services/agent_orchestrator.py`, FastAPI lifespan integration, per-agent budget enforcement, `AGENT_FLEET_ENABLED` feature flag.
- [ ] `TODO` [US-030 — Portfolio Manager Agent (read-only daily brief)](requirements/US-030-pm-agent-readonly.md) — Haiku 4.5, 09:00 UTC cron, reads 7-day platform state, emails Wilson a morning brief, writes to `agent_decisions` + `agent_memory/scoreboard.md`.
- [ ] `TODO` [US-031 — Quant Analyst Agent (event-triggered divergence reports)](requirements/US-031-qa-agent-divergence.md) — Sonnet 5, fires on paper-run day-close + stop_reason events. Classifies deltas (cost-model / execution / indicator / regime / validator-weakness / novel). First expected win: diagnoses current staging paper runs' cost-model gap unprompted.
- [ ] `TODO` [US-032 — Shared memory layer](requirements/US-032-agent-memory-layer.md) — `agent_decisions` DB table + migration, `agent_memory/` markdown directory structure, read/write helpers.
- [ ] `TODO` [US-033 — Agent email channel](requirements/US-033-agent-email-subject-prefix.md) — `[AgentFX AGENT <ROLE>]` subject prefix support in `notifications/email.py` so Wilson's inbox stays sortable.

### Phase A follow-ups

- [ ] `TODO` **Email formatting polish** (Wilson 2026-10-05, deferred). Current brief body is a plain-text template — "quite plain and unformatted" per Wilson. Rework once Phase B lands the Haiku-driven PM brief: HTML multipart body, scoreboard rendered as a table, highlighted call-outs for actionable recommendations, mobile-safe layout. Doing it before Phase B is throwaway work since the brief content shape itself changes.

### Exit criteria

- [ ] PM Agent sent ≥14 morning briefs with no manual intervention.
- [ ] QA Agent produced ≥5 divergence reports; ≥1 correctly characterised the current staging paper runs' cost-model gap unprompted.
- [ ] `agent_decisions` DB table has >100 rows; `agent_memory/` has ≥3 topic files.
- [ ] Total LLM spend < $15 (buffer below $30 cap).
- [ ] **Profitability checkpoint:** QA identified ≥3 concrete evolution-vs-paper divergences and quantified impact on expected post-cost sharpe.

---

## v0.8.1 — Agent Fleet: autonomous scheduling + code authorship

**Theme:** PM gets its first action tool (schedule evolution runs). Platform Engineer Agent lands in a Managed Agents workspace and ships its first autonomous PRs.

**Approval model** (per Wilson 2026-10-05 decision): `agent/*` sub-branches auto-merge to `feat/agent-fleet` on tests-pass + PE review-agent approval. PRs from `feat/agent-fleet` to `main` require Wilson's manual approval.

### User stories

- [ ] `TODO` [US-034 — PM scheduling tool](requirements/US-034-pm-scheduling-tool.md) — PM calls `/evolution-runs` POST with chosen `(instrument, gran, config)`. Rate-limited (max 2 runs/week initially). First autonomous use: schedules the H1 EUR_USD experiment per Agent 3 SOTA research + Plan agent recommendation.
- [~] `IN PROGRESS` [US-035 — Platform Engineer Agent in Managed Agents workspace](requirements/US-035-pe-agent-managed-agents.md) — C.2 Phase 2 scaffolding shipped (2026-10-15): `agent_workspace_runs` table + migration; `WorkspaceAdapter` protocol + `StubWorkspaceAdapter` (default) + `ManagedAgentsAdapter` (scaffold, SDK wire-up pending); `AGENT_PE_WORKSPACE_ENABLED` + daily caps + `GITHUB_TOKEN` settings; `scan_and_dispatch` routing HIGH-confidence proposals; orchestrator uses the dispatcher when the flag is on. **Phase 2b remaining:** real Anthropic Managed Agents SDK call in `ManagedAgentsAdapter._dispatch_managed`; GitHub PAT vault setup; polling worker to transition RUNNING → PR_OPEN/MERGED; `feat/agent-fleet` branch + CI gate.
- [ ] `TODO` [US-036 — First PE task: cost-model calibration from paper fills](requirements/US-036-pe-first-cost-model-task.md) — take QA's cost-model divergence report, update `instruments.typical_spread_pips` for EUR_USD M1 from realized paper fills, open PR with reproducing test.
- [x] `DONE` [US-037 — OANDA account pool](requirements/US-037-oanda-account-pool.md) — v0.8.1 C.3 shipped: `oanda_account_pool` table + Fernet-encrypted key column, ADMIN router at `/api/v1/oanda-pool/accounts` (POST/GET/PATCH/DELETE + /summary), `agents/fleet/account_pool.py` with `allocate_slot`/`release_slot`/`pool_summary` (cooling-down + lease-TTL sweeps + 10-concurrent soft cap). PM wire-up (v0.8.2) still outstanding.
- [ ] `TODO` [US-038 — CI for agent-fleet branches](requirements/US-038-agent-fleet-ci.md) — `.github/workflows/agent-fleet-ci.yml` running test-unit + lint on `agent/*` and `feat/agent-fleet` branches, with auto-merge logic for the three-tier gate.

### Exit criteria

- [ ] PM scheduled ≥2 evolution runs autonomously.
- [ ] PE merged ≥3 PRs to `feat/agent-fleet`, all with passing tests.
- [ ] Reproducing test for Run B cost-model issue exists in the codebase.
- [ ] Account pool has ≥3 accounts; PM allocated runs across ≥2.
- [ ] **Profitability checkpoint:** ≥1 PE PR directly moves honest post-cost expected sharpe higher.

---

## v0.8.2 — Agent Fleet: autonomous paper lifecycle

**Theme:** PM gets paper-approval authority (unbounded on paper per Wilson "paper is aggressive sandbox" directive). QA + PE close the loop autonomously. Platform becomes self-improving.

### User stories

- [ ] `TODO` [US-039 — PM paper-approval authority](requirements/US-039-pm-paper-approval.md) — PM reads approval queue, decides deployments, posts to `/paper-runs/approve`. No allocation caps on paper. Multiple parallel runs across pairs/timeframes.
- [ ] `TODO` [US-040 — Paper-deploy gate hardening (first QA→PE pipeline task)](requirements/US-040-paper-deploy-gate.md) — fix `paper.py:677` hole where `validator_passed=TRUE` was accepted even with non-empty `validation_failures` or zero `validation_folds`. Prerequisite for PM auto-approval.
- [ ] `TODO` [US-041 — Research Agent (weekly literature digest)](requirements/US-041-research-agent.md) — Sonnet 5 + WebSearch, weekly on Monday 03:00 UTC. Reads arXiv/SSRN quant-finance + LLM-trading literature, proposes 1-3 concrete additions to PM.
- [ ] `TODO` [US-042 — CUSUM live drift detection](requirements/US-042-cusum-drift.md) — PE workload gated by QA. One-sided CUSUM (k=0.5σ, h=4σ) on daily realised-vs-backtest P&L residuals, calibrated per-genome. Fires halt + email on breach.
- [ ] `TODO` [US-043 — Reflexion loop](requirements/US-043-reflexion-loop.md) — PM writes expected outcome at decision time; later re-reads vs actual; "lessons learned" entries flow into QA's prompt context.

### Exit criteria

- [ ] PM approved ≥3 paper deployments autonomously.
- [ ] QA caught + reported ≥1 PE-introduced regression (Reflexion loop demonstrated).
- [ ] Live-vs-backtest divergence on ≥1 paper run under 1σ for ≥7 continuous days.
- [ ] CUSUM shipped and firing on ≥1 paper run.
- [ ] Monthly cost within envelope.
- [ ] **Profitability checkpoint:** ≥1 strategy sustained positive paper P&L for 14 continuous days with divergence under 1σ.

---

## v0.8.3 — Agent Fleet: production merge

**Theme:** `feat/agent-fleet` → `main`.

### Exit criteria

- [ ] 1-2 weeks soak on UAT + dev with no Wilson intervention needed.
- [ ] Wilson approves the single `feat/agent-fleet` → `main` PR.
- [ ] 2 continuous weeks of fleet on `main` without Wilson intervention.
- [ ] Monthly cost at steady state within $20-30.
- [ ] ≥1 paper run that the fleet fully owned (designed → deployed → monitored → halted/kept).

---

## v0.9.0 — Live-nomination pipeline

**Theme:** Fleet produces first paper-strategy nomination with full evidence package. Wilson reviews. If approved, strategy queues for v1.0.0 live deployment. **This is where the fleet's experimentation output converges on the ultimate goal.**

### User stories

- [ ] `TODO` [US-044 — Strategy nomination schema + composer](requirements/US-044-strategy-nomination.md) — formal `strategy_nomination` record with 30-day paper P&L, divergence report, lineage, cost-model notes, Research Agent corroboration, PM reasoning, sanity-check output.
- [ ] `TODO` [US-045 — Wilson's review interface](requirements/US-045-nomination-review.md) — approval endpoint + email with full nomination package. Approve / reject-with-reasoning / request-more-paper-time. Rejection reasoning flows into agent memory.

### Exit criteria

- [ ] ≥1 strategy reached nominated state with full evidence package.
- [ ] Wilson reviewed ≥1 nomination end-to-end.
- [ ] Approval path to v1.0.0 live trading is clear (mechanism works regardless of first-nomination outcome).

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
