# AgentFX Roadmap

AgentFX is an **autonomous FX trading platform** that discovers, develops, tests, and evolves trading strategies without manual intervention.

## Agentic Architecture

```
Discovery Agent -> Optimizer Agent -> Validator Agent -> [Human Approval] -> Paper Trading -> Live
       ^                                                                              |
       +---------------------- Evolution Agent (feedback loop) <----------------------+
```

---

## v0.1.0 - Foundation (COMPLETE)

**Theme:** Observable Infrastructure
**Status:** COMPLETE
**Release Date:** 2025-12-13

### Delivered Capabilities
- User authentication with JWT and role-based access (VIEWER/TRADER/ADMIN)
- User registration with email validation
- Market Data REST API (candles, instruments, status, metrics)
- Dashboard with real user info and system status
- Market Data page with instruments dropdown and candle data table
- Candle ingestion worker (7-day backfill + continuous 60s polling)
- OANDA API integration for historical candle data
- Comprehensive test suite (unit, integration, E2E)

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

**Theme:** First autonomous capability
**Status:** All exit criteria met. 604 tests passed (unit + integration + E2E) on 2026-05-04. See BACKLOG.md for caveats (coverage gate, per-pair spread, GP runtime warnings) and pending P2 export.

**Goal:** System can autonomously optimize strategy parameters using Bayesian optimization and identify profitable configurations with interactive visualization.

**Key Capabilities:**
- Bayesian optimization over strategy parameter space (more efficient than grid search)
- Trial tracking with full lineage (params, metrics, signals)
- Async job processing (202 Accepted pattern)
- Portal to view optimization results with top 3 configurations
- Strategy visualization with candlestick charts, entry/exit markers, and technical indicators

**User Stories:** [US-001](requirements/US-001-optimization-runs.md), [US-002](requirements/US-002-view-optimization.md), [US-015](requirements/US-015-visualization.md)

**Exit Criteria:**
- [x] Backtest service reads from PostgreSQL candles table (`load_price_df_from_db()`)
- [x] Bayesian optimizer runs autonomously (`_estimate_iterations(dims) = 10 + 12*dims`)
- [x] Top 3 parameter sets identified and ranked by Sharpe (`GET /optimization-jobs/{id}/trials/top`)
- [x] Gate B thresholds displayed (informational, not enforced) — Optimization, OptimizationDetail, TrialDetail pages
- [x] Strategy visualization with candlestick + entry/exit markers + indicators (`StrategyChart.tsx`)
- [x] User can view results in portal (routes wired in `router.tsx`, behind `ProtectedRoute`)

---

## v0.3.0 - Autonomous Strategy Loop (FUNCTIONALLY COMPLETE)

**Theme:** First genuinely agentic increment.

**Goal:** A scheduled closed loop that discovers, evolves, and validates trading strategies without human intervention. Discovery and Validation are absorbed into this milestone — they no longer exist as separate versions.

**Reference:** Full plan at `~/.claude/plans/i-think-we-need-functional-pony.md`.

**Status:** 4 of 5 exit criteria met. The loop runs end-to-end nightly on Railway. Acceptance #3 is **open** — no genome has cleared Gate B because of regime fragility in the validator (single-OOS-fold walk-forward systematically inverts when the OOS slice is a different regime to the training folds). Mitigation belongs in v0.4.0 (rolling walk-forward + regime tagging).

**Key Capabilities (delivered):**
- Strategy DSL v1 (constrained JSON grammar over a 9-indicator catalogue) with a compiler that emits the existing `Strategy` interface
- Genetic algorithm core (`deap>=1.4` + custom Pydantic-aware mutation/crossover) — population of 30 genomes evolved with tournament-3 selection, 10% elitism, edit-distance diversity penalty
- LLM-driven hypothesis injection every N=3 generations via the `anthropic>=0.40` SDK with mandatory prompt caching, structured output via tool-use, hard per-run cost cap
- Walk-forward validator (K=4 folds, final fold withheld from GA) applying Gate B (Sharpe ≥ 1.0, MDD ≤ 10%, Hit Rate ≥ 48%, ≥ 300 trades) on held-out OOS
- Real Orchestrator that drives the loop autonomously and forks `multiprocessing.Process` per run with `resource.setrlimit()` caps for blast-radius isolation
- Decision log: every agent step persisted to Postgres with reasoning text, queryable from the portal
- Modular monolith on one Railway service with a pre-committed `backtest-worker` split point at >6h wall-clock or queue depth >200 sustained

**User Stories:** [US-003](requirements/US-003-evolution-run-lifecycle.md), [US-004](requirements/US-004-walk-forward-validation.md), [US-005](requirements/US-005-llm-hypothesis-injection.md), [US-006](requirements/US-006-strategy-dsl.md), [US-016](requirements/US-016-evolution-observability.md)

**Exit Criteria:**
- [x] Scheduled Railway cron triggers a fresh evolution run nightly
- [x] Run completes unattended, hits a stop condition, persists VALIDATED genomes for human review
- [ ] **OPEN** — At least one VALIDATED genome differs structurally from the gen-0 seeds (system actually discovered something). Carries into v0.4.0.
- [x] Decision log lets a reviewer reconstruct *why* each surviving genome was kept
- [x] LLM cost stays under per-run budget across 5 consecutive nightly runs

---

## v0.4.0 - Capability Expansion + Regime-Aware Validation (IN PROGRESS)

**Theme:** Two goals at once — (a) close v0.3.0 acceptance #3 by fixing the validator's regime fragility; (b) grow the discovery surface so genomes have more to express.

**Why both:** The 2026-06-07 analysis of 9 recent UAT M10 runs revealed that training-fold Sharpes of +1.5 to +3 routinely become OOS Sharpes of −0.4 to −1.8 on the same Apr-Jul 2024 OOS slice. Three independent runs with different RNG seeds all lost on the same OOS slice — a regime-shift signature, not random GA overfit. Expanding the DSL alone wouldn't close the gap; we need to change *how we validate* in parallel with *what we can express*.

**Already shipped (May-June 2026):**
- Per-pair configurable spread snapshot at run-start; `min_hold_bars` DSL field
- OOS-split tabs + per-trade cost columns + trade-ledger metrics
- Cost-model rewrite to OANDA AU 21:00 UTC schedule + slippage default 0.0 (PR #6)
- Delete invalid evolution runs from UI; scrollable form modal; CI E2E fixes (PRs #6/#7/#8)

**Planned Capabilities:**
- *Regime-aware validation* — rolling walk-forward instead of fixed-25%-tail OOS; per-fold regime tagging (vol quintile + trend strength); early-reject runs where train/OOS regimes disagree
- *Expanded DSL* — 5 new indicators (MACD, stochastic, ADX, OBV, ichimoku); time-of-day entry filter; multi-timeframe references
- *Multi-symbol portfolios* — within a single evolution run (design decision pending)
- *Regime tags as LLM prompt context* — surfaces train/OOS regime asymmetry to the proposer
- *Optional Bayesian late-stage refiner* — repurpose `BayesianOptimizerAgent` to fine-tune numeric thresholds inside winning genome shapes after GA selection

**Exit Criteria:**
- [ ] At least one VALIDATED genome clears Gate B on a real nightly run (closes v0.3.0 acceptance #3)
- [ ] DSL grammar covers ≥ 12 indicators and time-of-day filters
- [ ] Rolling walk-forward produces a stable median OOS Sharpe across ≥ 3 non-overlapping data slices
- [ ] Evolution runs can target a portfolio of 3+ pairs simultaneously
- [ ] Per-pair spread accuracy verified against historical OANDA spread data on EUR_USD + GBP_USD + USD_JPY

---

## v0.5.0 - Self-Improvement (IN PROGRESS)

**Theme:** The platform learns *across* runs, not just *within* runs.

**Goal:** Evolution becomes meta — the system distils what worked into priors and prompts that condition future runs.

**Already shipped (May-June 2026):**
- Randomised `rng_seed` per run (was hardcoded 42 — all runs were converging on bit-identical DSL)
- Fold-consistency fitness: `mean(per_fold_sharpe) − 0.5·std(per_fold_sharpe)` instead of mean alone
- Per-genome training-fold metrics + fitness-breakdown panel; LLM-injection cards with per-proposal DSL + reasoning; archetype badges

**Key Capabilities (planned):**
- Real Evolution agent (replacing the v0.3.0-era stub) that distils cross-run patterns into prompt updates and learned DSL priors
- Drift detection on live-running validated strategies; re-triggers evolution when behaviour drifts from validation distribution
- LLM prompts include patterns observed across the last K runs, not just the current one
- Prompt A/B testing infrastructure to measure prompt-quality improvements quantitatively

**Exit Criteria:**
- [ ] Cross-run hypothesis lineage demonstrably influences new genome proposals
- [ ] Drift detection successfully triggers re-optimization on a contrived regime shift
- [ ] Measurable improvement in mean OOS Sharpe across consecutive evolution runs

---

## v0.6.0 - Paper Trading

**Theme:** Promote VALIDATED genomes to OANDA Practice with human approval gate. (Was previously v0.5.0; pushed down a slot by the v0.3.0 reframe.)

**Goal:** Bring real-money-shaped feedback into the loop without risking real money.

**Key Capabilities:**
- Approval queue portal page — VALIDATED genomes awaiting human sign-off
- Paper trading service executing approved genomes on OANDA Practice
- Live performance comparison vs. validation metrics
- Email/Slack notifications for approval requests

**Exit Criteria:**
- [ ] Trader role can review a VALIDATED genome and promote it to paper
- [ ] Paper trading service executes orders on OANDA Practice
- [ ] Live vs. validation drift visible in portal

---

## v1.0.0 - Live Trading + Portfolio

**Theme:** Self-sustaining strategy factory in production.

**Goal:** Platform continuously discovers, evolves, validates, paper-trades, and (with human approval) live-trades profitable strategies with minimal human oversight.

**Key Capabilities:**
- Live trading integration (OANDA Live) with second human approval gate (paper → live)
- Multi-strategy portfolio management with capital allocation across approved genomes
- Performance monitoring dashboard (live P&L, drift alerts, regime context)
- Strategy versioning and rollback
- **Re-evaluate `Temporal` (or similar durable workflow engine)** at this scale per the plan's "Orchestration & scheduling" revisit triggers

**Exit Criteria:**
- [ ] At least 1 strategy successfully promoted to live trading
- [ ] Platform generates net positive returns over 3+ months
- [ ] Evolution agent successfully adapts to regime change

---

## Future Considerations (Post v1.0)

- Reinforcement-learning-based strategy discovery as an alternative engine alongside the LLM+GA loop
- Multi-asset support beyond FX (indices, commodities, crypto)
- Sentiment / news / orderbook data integrated as prompt context and DSL inputs
- Advanced portfolio optimization (Kelly, mean-variance) at the allocation layer
- Institutional-grade reporting and compliance

---

## References

- [BACKLOG.md](BACKLOG.md) - Detailed task breakdown
- [requirements/](requirements/) - Executable user stories
- [MVP_CONSTRAINTS.md](MVP_CONSTRAINTS.md) - Risk and operational limits
- [archive/](archive/) - Historical planning documents
