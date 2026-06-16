# AgentFX Roadmap

AgentFX is an **autonomous FX trading platform** that discovers, develops, tests, and evolves trading strategies without manual intervention.

## North Star — The product we are building toward

The platform is finished when it is a **regime-aware portfolio of strategies** that delivers reliable monthly profitability without a human in the loop, supported by an intelligent diagnostic agent that closes the discover → evaluate → improve cycle automatically. The milestone breakdown below is the path to that end state; this section is the destination it converges on.

### Success criteria for the end-state platform

- **Consistent monthly profitability of 20–30% or higher** on the live portfolio, with no upper bound — *higher is better, but the floor is what matters*.
- **Reliability is the success metric, not peak performance.** A 40% month followed by a −15% month is a failure, even though the average looks fine. The platform's value is in *predictable* month-over-month positive returns, not lottery-ticket months with offsetting losses.
- **Drawdowns stay inside risk limits even during the best months** — the strategies clearing this bar are ones with genuine edge, not ones that got lucky.

### The portfolio shape we're building toward

This is not "find one strategy and run it." It's a **regime-aware collection** where multiple strategies are validated, queued, and activated dynamically based on the current market state. The portfolio knows:

- Which strategies historically work in **high-vol-trending-up** vs **low-vol-choppy-flat** vs every other regime tag the validator records.
- The current regime tag of each pair it trades, computed live in the same way the validator computes it on historical slices.
- The activation policy: which strategy is "on" for which pair, at what risk weight, given current regime.

A directionally-biased strategy that's brilliant in down-trending markets isn't promoted to "always on" — it's promoted to "on when the live regime tag for this pair is `low-vol-choppy-down`, off otherwise." The v0.4.0 directional regime tag (PR #9) is the foundation this rides on; conditional-edge classification (also PR #9) is the surfacing layer.

### The diagnostic evaluation agent (the missing piece)

Today's loop is **evolve → evaluate → mark as winner or loser → repeat**. That's optimization without learning. The missing piece is the agent that examines failures and underperformers and diagnoses *why*, then feeds that diagnosis back into the next iteration as guidance — not just noise. Its job, when shown an underperforming or rejected genome:

1. **Is the strategy fundamentally flawed?** Same-mechanical-event pattern? Degenerate signal that fires too rarely / too often? Missing a stop-loss safety net the live environment requires?
2. **Was it deployed in the wrong regime?** Per-fold regime tags show training and OOS clearly diverged in a way the strategy couldn't compensate for. The fix is to surface this strategy for conditional deployment, not to discard it.
3. **Are costs killing the margin?** Spread + slippage + swap eats more than the gross edge produces. The fix may be a higher-timeframe variant of the same strategy where fewer trades pay fewer costs.
4. **Is risk management the bottleneck?** Stop too tight (signal whipsawed out); target too tight (left profit on the table); position size too large (one outlier loss wipes the month).

The agent must convert each diagnosis into a *concrete next action* — a DSL change to propose to the LLM, a regime tag to require for this genome's activation, a cost-model assumption to revisit. **It is the bridge between "we found a problem" and "the next nightly run is smarter."**

### Guardrails — when does the agent stop?

The agent must have explicit convergence and stopping logic so it doesn't spin indefinitely. Specifically:

- **Per-cycle budget cap** — N generations or M wall-clock hours per nightly invocation, with the per-run LLM-budget cap (already shipped) as the cost ceiling.
- **Plateau detection** — N consecutive cycles with no improvement in best fitness AND no diagnostic-driven change to the search space triggers a "we've exhausted what this configuration can find" signal. The next move at that point is either (a) human review or (b) a different instrument / timeframe / DSL extension — not another cycle of the same.
- **Convergence to a portfolio slot** — once a strategy clears Gate B AND has a stable regime-conditional activation policy, it graduates out of the active evolution loop and into the portfolio. Further evolution focuses on filling gaps (regimes not yet covered) rather than re-validating proven slots.
- **Negative-progress detection** — if the diagnostic agent's proposed improvements consistently produce worse OOS results across N consecutive cycles, the diagnostic itself becomes the subject of review (its prompt, its assumptions, its training data). The platform must distrust its own feedback loop when evidence says it should.

### What this means for the milestone breakdown below

The milestones are sequenced to converge on this vision:

- **v0.3.0 (shipped)** built the evolution loop itself.
- **v0.4.0 (in progress)** is building the *honest* validation that makes diagnosis possible — rolling walk-forward, regime tagging, conditional-edge surfacing. Without these the diagnostic agent has nothing trustworthy to reason about.
- **v0.5.0 (planned)** is where the diagnostic agent itself lives. It absorbs the v0.4.0 signals into actionable feedback.
- **v0.6.0 + v1.0.0** deploy the portfolio in paper and live.

The 20-30% monthly target is not a v0.5.0 deliverable — it's the steady-state benchmark the full platform must hit before live capital scales. Each milestone is judged by whether it moves us mechanically closer.

---

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

## v0.5.0 - Self-Improvement + Diagnostic Evaluation Agent (IN PROGRESS)

**Theme:** The platform learns *across* runs and *diagnoses why* strategies fail, closing the feedback loop with intentional next-iteration guidance rather than blind re-evolution.

**Goal:** Evolution becomes meta. Two complementary layers:
1. **Cross-run pattern distillation** — the system tracks what worked / what failed across many nightly runs and feeds those observations back into the next run's LLM prompts and DSL priors.
2. **Per-failure diagnostic agent** — the missing piece called out in the North Star section. Examines underperforming genomes, decides whether they're fundamentally flawed / regime-misplaced / cost-bottlenecked / risk-bottlenecked, and produces a concrete next action (a DSL change to propose, a regime tag to require, a cost assumption to revisit). Without this agent the loop is optimization without learning.

**Already shipped (May-June 2026):**
- Randomised `rng_seed` per run (was hardcoded 42 — all runs were converging on bit-identical DSL)
- Fold-consistency fitness: `mean(per_fold_sharpe) − 0.5·std(per_fold_sharpe)` instead of mean alone
- Per-genome training-fold metrics + fitness-breakdown panel; LLM-injection cards with per-proposal DSL + reasoning; archetype badges
- Rolling walk-forward + directional regime tag + same-mechanical-event grammar constraint + conditional-edge UI classifier (PR #9 — the v0.4.0 outputs the diagnostic agent will consume)

**Key Capabilities (planned):**

*Diagnostic evaluation agent:*
- LLM-driven failure classifier: given a genome + its full per-fold metrics + regime tags, outputs a structured diagnosis (`flawed_dsl` | `regime_mismatch` | `cost_dominated` | `risk_bottlenecked` | `unknown`) with reasoning text
- Action proposer: each diagnosis maps to a concrete instruction for the next iteration. E.g. `regime_mismatch` → "this strategy belongs in the conditional-edge portfolio for regime X, not the active evolution loop"; `cost_dominated` → "propose a coarser-timeframe variant"; `flawed_dsl` → "the spec violates principle P, the next LLM proposal should avoid it"
- Feedback ingestion into the LLM-injection prompt — the model sees recent diagnoses as context, not just recent top genomes
- Per-genome diagnosis surfaced in the portal next to the fitness breakdown so a human can audit the agent's reasoning

*Convergence guardrails:*
- Plateau detection: N consecutive cycles with no improvement AND no diagnostic-driven change to the search space triggers a "we've exhausted this configuration" signal. The platform stops re-evolving from a dead end and asks the human (or, later, a higher-level meta-agent) to pivot
- Negative-progress detection: if diagnostic-proposed changes consistently produce worse OOS results across N cycles, the diagnostic agent's own prompt / assumptions / training data become the subject of review. The platform must distrust its own feedback loop when evidence demands it
- Graduation logic: a genome that clears Gate B AND has a stable regime-conditional activation policy moves out of the active evolution loop into the portfolio queue. Further evolution effort shifts to filling regime coverage gaps, not re-validating proven slots

*Cross-run distillation (was the original v0.5.0 scope, kept):*
- Real Evolution agent (replacing the v0.3.0-era stub) that distils cross-run patterns into prompt updates and learned DSL priors
- Drift detection on live-running validated strategies; re-triggers evolution when behaviour drifts from validation distribution
- LLM prompts include patterns observed across the last K runs, not just the current one
- Prompt A/B testing infrastructure to measure prompt-quality improvements quantitatively

**Exit Criteria:**
- [ ] Diagnostic agent classifies every failed/underperforming genome into one of the structured failure modes with audit-trail reasoning visible in the portal
- [ ] At least one cycle exists end-to-end where a diagnosis produced a concrete DSL/regime/cost/risk change that demonstrably improved the next cycle's OOS result (a measurable feedback loop closure, not anecdotal)
- [ ] Plateau detection successfully halts a stuck configuration without manual intervention
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

## v1.0.0 - Regime-Aware Portfolio + Live Trading

**Theme:** Self-sustaining strategy factory in production — the end-state described in the North Star section.

**Goal:** Platform continuously discovers, evolves, validates, paper-trades, and (with human approval) live-trades a *regime-aware portfolio* of profitable strategies, hitting the North Star floor of 20–30% reliable monthly returns with minimal human oversight.

**Key Capabilities:**
- *Live trading integration* — OANDA Live with the second human approval gate (paper → live)
- *Regime-aware portfolio activation engine* — multiple validated strategies on standby; each tagged with its preferred regime (from v0.4.0's conditional-edge classifier). At each tick, the engine computes the live regime tag for each pair, looks up which strategies should be active for that regime, and applies them at their assigned risk weight. A strategy with edge only in `low-vol-choppy-down` is in the portfolio but inactive unless that regime is live
- *Capital allocation across activated strategies* — risk parity / equal weight / fitness-weighted, configurable, with hard caps so a single losing strategy can't take down the portfolio
- *Performance monitoring dashboard* — live P&L per strategy, current regime per pair, which strategies are active vs dormant, drift alerts, regime context
- *Strategy versioning and rollback* — when the diagnostic agent (v0.5.0) re-evolves a strategy and ships v2, v1 stays in the version history with full audit trail; rollback is one-click
- **Re-evaluate `Temporal` (or similar durable workflow engine)** at this scale per the plan's "Orchestration & scheduling" revisit triggers

**Exit Criteria:**
- [ ] At least 1 strategy successfully promoted to live trading with regime-conditional activation
- [ ] Portfolio (multiple strategies, regime-gated) generates **consistent monthly returns at the North Star floor** (20–30%+ per month, no upper bound) over a contiguous 3+ month window
- [ ] Drawdowns stay inside configured risk limits even during the highest-return months
- [ ] Evolution agent + diagnostic agent (v0.5.0) successfully adapts portfolio composition to regime change without human intervention

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
