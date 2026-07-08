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

## v0.5.0 - Self-Improvement (IN PROGRESS, runs in parallel with v0.6.0)

**Runs alongside v0.6.0.** The diagnostic agent doesn't block paper trading — it *augments* it. A genome only needs `validator_passed` to enter the v0.6.0 paper approval queue. Once the diagnostic agent lands, it becomes a second condition (diagnosis must be `ok` or `regime_mismatch → route to conditional-edge portfolio`). Paper trading ships first with `validator_passed` as the sole gate.

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

- [ ] `TODO` [US-017 — Paper Trading UX design lockdown](requirements/US-017-paper-trading-ux-design.md)
- [ ] `TODO` [US-018 — Per-user RBAC filter on runs, jobs, genomes](requirements/US-018-rbac-user-isolation.md) — **also closes an existing security bug** where any authenticated user sees all runs, not just their own
- [ ] `TODO` [US-019 — Per-user OANDA credentials, encrypted at rest](requirements/US-019-per-user-oanda-credentials.md)
- [ ] `TODO` [US-020 — OANDA order-execution adapter methods](requirements/US-020-oanda-order-execution.md)
- [ ] `TODO` [US-021 — Paper runner service + subprocess model](requirements/US-021-paper-runner-service.md)
- [ ] `TODO` [US-022 — Paper trading portal pages (`/settings`, `/paper`, `/paper/approvals`, `/paper/:id`)](requirements/US-022-paper-trading-portal.md)
- [ ] `TODO` [US-023 — Kill switch + risk controls + approval state machine](requirements/US-023-kill-switch-risk-controls.md)

### Deferred to v0.6.1

- [ ] `DEFERRED to v0.6.1` Email / Slack notifications for approval requests, kill events, risk-control triggers
- [ ] `DEFERRED to v0.6.1` Live-vs-validation drift alerts as a separate UI surface (basic overlay chart is in MVP)
- [ ] `DEFERRED to v0.6.1+` OANDA v20 pricing stream (WebSocket) — replaces 60s polling if latency becomes a blocker
- [ ] `DEFERRED to v0.7.0` Fernet key rotation → `key_version=2` on `user_oanda_credentials`
- [ ] `DEFERRED to v0.7.0` ADMIN visibility of other users' *masked* credential status (for support)

### v0.6.1 — Known gaps surfaced during UAT smoke-test (2026-07-08)

The v0.6.0 backend + portal shipped clean, but the smoke-test on Wilson's real UAT data flushed out four follow-ups that are already noted in the PR-J placeholders and one new one from the approval-queue verification:

- [ ] `NEEDS INVESTIGATION` **Validator never writes `validator_passed`** — Wilson's 17 UAT evolution runs have produced multiple genomes whose `metrics_json` clearly clears every Gate B threshold (Sharpe 2.6, MDD 6.7%, hit 50.4%, n_trades 600), yet every row has `validator_passed = NULL`. The evaluator agent isn't persisting the verdict. Impact: the approval queue is permanently empty for real users — the smoke-test only worked after a manual `UPDATE strategy_genomes SET validator_passed = true`. Fix path: audit `agentfx.agents.validator` + `evolution_runner` to find where the write-back is dropped, add an integration test that confirms passing metrics → `validator_passed = true` on the persisted row.
- [ ] `NEEDS BACKEND` `GET /paper-runs/{id}/trades` endpoint + trades table on the detail page (Origin + Signal reason columns per Wilson's UX answer #1)
- [ ] `NEEDS BACKEND` `GET /paper-runs/{id}/decisions` endpoint + decision log stream on the detail page — every gate refusal / order / kill is already persisted in `decision_logs`, just needs surfacing
- [ ] `NEEDS BACKEND` `GET /paper-runs/{id}/equity` endpoint — persisted historical equity snapshots so LivePnLChart survives page reloads (currently it accumulates only while the tab is open)
- [ ] `NEEDS BACKEND` Backtest OOS overlay data on `LivePnLChart` — right now the backtest curve is a placeholder; needs a source that returns the walk-forward OOS equity time series
- [ ] `UX POLISH` **LLM-suggested deployment name** on the DeployPaperModal — today the field pre-fills with `{instrument} {granularity} — {YYYY-MM-DD}` which is fine for one deployment but useless when the user has 10+ paper runs. Use the existing Anthropic integration (same one the evolution runner uses for genome hypotheses) to summarise the genome's DSL into a short, memorable identifier — e.g. `EUR_USD trend-MACD 20/50` or `USD_JPY RSI-oversold 14`. New backend endpoint `POST /paper-runs/generate-name` body `{strategy_genome_id}` → `{suggested_name}`. Frontend calls it when the deploy modal opens and shows the suggestion in the name field with an "AI" chip; user can still overwrite. Cheap LLM call — Haiku is fine given the DSL is small.

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
