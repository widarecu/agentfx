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

## v0.4.0 - Regime-Aware Validation + Core Anti-Overfitting (FUNCTIONALLY COMPLETE)

**Theme:** Close v0.3.0 acceptance #3 by fixing the validator's regime fragility and the fitness function's Sharpe-only optimisation bias. DSL / TOD / multi-symbol expansion has been re-scoped to v0.7.0 — the regime + fitness work turned out to be the binding constraint, not DSL surface.

**Why the re-scope:** Empirically the GA didn't need more indicators — with the OLD regime-blind fitness function it produced high-training-Sharpe genomes that catastrophically failed OOS regardless of DSL depth. Fixing the validation and fitness pipeline unlocked more value than expanding the search space. Grammar expansion returns in v0.7.0 once we have paper-trading data on the current DSL.

**Delivered (PRs #10 – #19, June 2026):**
- Rolling walk-forward validator: 3 training + 5 OOS folds (was fixed-25%-tail single fold)
- Per-fold regime tagging: volatility × trend × direction, correctly working on M1 (bug fix PR #12 — the classifier previously returned "choppy" for every M1 window)
- Warm-start cascade fix trilogy: canonical-spec exclusion (PR #13) + deferred injection at gen 10 (PR #16) + champion-by-OOS end-of-run pick (PR #17). Closes the multi-run laundering loop empirically observed on UAT 2026-06-25 through 2026-06-28.
- Gate-B-proximity fitness function (PR #18): raw fitness = weighted sum of Gate B distance terms (CAGR 0.35, MDD 0.25, Sharpe 0.20, Hit 0.10, Trades 0.10) − 0.5·std(fold Sharpe). Backend + LLM prompt + UI updated in one PR; 966 backend + 111 frontend tests pass.
- Fold-consistency penalty row surfaced in `FitnessBreakdown` panel (PR #19).
- Earlier v0.4.0 work: per-pair spread snapshot at run-start; `min_hold_bars` DSL field; OOS-split tabs; per-trade cost columns; trade-ledger metrics; cost-model rewrite to OANDA AU 21:00 UTC schedule + slippage default 0.0 (PR #6); scrollable form modal; CI E2E fixes (PRs #6–#8).

**Open regime work carrying into v0.6.0 / v0.7.0:**
- Bootstrap-consistency penalty on training folds (~1 day, deferred pending paper-trading feedback)
- Combinatorial Purged Cross-Validation (López de Prado ch. 7 / ch. 12) — reserved for v0.7.0 if paper trading confirms overfitting still dominates after Gate-B-proximity
- Regime-conditioned strategies (specialist per regime + runtime switcher) — reserved for v1.0.0

**Deferred to v0.7.0 (Capability Expansion):**
- Expanded DSL — 5+ new indicators, time-of-day entry filter, multi-timeframe references
- Multi-symbol portfolios within a single evolution run
- Optional Bayesian late-stage refiner (`BayesianOptimizerAgent` repurposed as post-GA fine-tuner)

**Exit Criteria:**
- [x] Rolling walk-forward produces a stable median OOS Sharpe across 5 non-overlapping folds
- [x] Regime classifier correctly labels sub-daily granularities (per PR #12 — verified against known-directional windows including Q2 2025 EUR/USD rally)
- [x] Gate-B-proximity fitness rewards balanced strategies over Sharpe-max ones (verified: balanced A `0.6 Sharpe / 18% CAGR / 8% MDD / 50% hit` scores +0.113 vs Sharpe-max B `1.5 Sharpe / 5% CAGR / 15% MDD / 30% hit` at −0.493)
- [x] Warm-start no longer cascades exhausted specs across runs (per PRs #13/#16/#17)
- [ ] At least one VALIDATED genome clears Gate B on OOS — **carries into v0.6.0 paper-trading feedback**. As of the June-28 → July-4 batch, best OOS Sharpe is +0.84 (H1 50f984c8), still below the 1.0 threshold. Paper trading is expected to inform whether the remaining gap is a fitness-function issue or a calibration issue.

---

## v0.5.0 - Self-Improvement + Diagnostic Evaluation Agent (CANCELLED — subsumed by v0.8.x Agent Fleet)

**Status update (2026-10-05):** The diagnostic agent concept is CANCELLED as a standalone milestone and **replaced by the v0.8.x Agent Fleet**. The original v0.5.0 scope (failure classifier, plateau detection, cross-run distillation, drift detection) is covered by the Agent Fleet's Quant Analyst + Research agents, implemented as a true multi-agent system (Claude Agent SDK + Managed Agents workspaces) rather than a single in-process module. See `v0.8.0 - Agent Fleet: read-only pilot` below.

The exit criteria originally listed under v0.5.0 are reframed in v0.8.x terms:
- Diagnostic failure classification → Quant Analyst Agent (v0.8.0 Phase B).
- Plateau detection / stuck-run halting → Portfolio Manager Agent with kill clocks (v0.8.1).
- Cross-run hypothesis distillation → shared `agent_memory/` + Reflexion loop (v0.8.2 Phase C).
- Drift detection → CUSUM, ships as PE Agent workload under v0.8.2 Phase B.
- Measurable OOS improvement across evolution runs → the v0.9.0 live-nomination milestone IS this measurement.

**Runs alongside v0.6.0** (original intent, no longer applies): the diagnostic agent doesn't block paper trading — it *feeds* it. A genome only becomes paper-eligible when both `validator_passed` (v0.4.0) and a diagnostic pass (v0.5.0) accept it, but the paper-trading pipeline can ship first with `validator_passed` as the sole gate, then have the diagnostic pass added as a second condition when v0.5.0 lands.

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

## v0.6.0 - Paper Trading MVP — UX-first, multi-tenant, per-user credentials (FUNCTIONALLY COMPLETE)

**Status:** All 7 user stories and 5 of 6 exit criteria met. Exit criterion #6 (30-day continuous deployment) partially observed — long-running UAT paper runs exposed the reliability gaps that became v0.6.3. Backlog entries for US-017 through US-023 flipped `[x]` on 2026-09-30 during the plan-vs-code audit; the code shipped over v0.6.0/v0.6.1 (2026-07 → 2026-08).

**Theme:** Any TRADER user can securely deploy any of their VALIDATED genomes to their own OANDA Practice account, monitor live P&L vs backtest, and kill-switch the run — with zero cross-user data leakage.

**Why this shape.** Six weeks of fitness function work (v0.4.0) reduced but did not eliminate the training-OOS gap. Every further backtest sophistication is a *guess* without live feedback. Paper trading is the only signal that tells us whether our cost model, execution timing, and metric definitions correlate with what really happens in a real broker account. Everything after paper (bootstrap consistency, CPCV, regime conditioning) is better informed by paper feedback than by another round of backtest experiments.

**Why multi-tenant / RBAC from day one.** The codebase currently has a live security bug — `list_runs()` and `list_jobs()` do not filter by `created_by`, so any authenticated user sees all other users' data. Introducing paper trading (with a real broker account) without fixing that leak amplifies it to real capital exposure. RBAC + per-user isolation is therefore inside v0.6.0, not deferred.

**User Stories:** [US-017](requirements/US-017-paper-trading-ux-design.md), [US-018](requirements/US-018-rbac-user-isolation.md), [US-019](requirements/US-019-per-user-oanda-credentials.md), [US-020](requirements/US-020-oanda-order-execution.md), [US-021](requirements/US-021-paper-runner-service.md), [US-022](requirements/US-022-paper-trading-portal.md), [US-023](requirements/US-023-kill-switch-risk-controls.md)

**Key Capabilities:**
- **UX-first delivery** — `trading-ux-designer` produces low-fidelity wireframes then clickable high-fidelity mockups for six flows (onboarding, approval queue, live monitoring, kill switch, credentials, multi-user visibility). Wilson signs off before any backend PR opens.
- **RBAC + per-user isolation** — retrofit `list_runs`/`list_jobs`/read endpoints to filter by `created_by = current_user.id` for non-ADMIN; ADMIN gets `?scope=all`. Ship `<RequireRole>` frontend HOC.
- **Per-user OANDA credentials** — new `user_oanda_credentials` table; Fernet encryption at rest via `cryptography` (already installed, unused today); 4 API endpoints; `CredentialsForm` in `/settings`. LIVE toggle disabled behind an ADMIN + v1.0.0 feature flag.
- **OANDA order execution** — extend `OandaClient` (which today only reads candles + financing + spread) with `place_market_order`, `close_trade`, `close_all_positions`, `get_position`, `get_account_balance`, `get_open_trades`, `get_pricing`. Exponential backoff; circuit breaker on 5 consecutive failures.
- **Approval queue** — VALIDATED genomes land in the owning user's `/paper/approvals`; the user explicitly approves each one (approval = human sign-off = deploy). No auto-deploy.
- **Paper runner service** — mirrors `evolution_runner.py`'s subprocess + watchdog pattern. Loads user credentials via `secrets.decrypt_secret`, builds a scoped `OandaClient`, drives the signal→order loop at genome-granularity cadence, respects kill switch + risk controls. Decision loop p95 <1s.
- **Kill switch** — big red button on `/paper/:id` with a 3-second undo toast. After the undo window closes the run halts (positions closed, `stop_reason=USER_KILL`).
- **Live vs backtest overlay** — `LivePnLChart` on `/paper/:id` compares live equity vs the genome's original backtest OOS equity by relative timestamp from run start.

**Schema decision — REUSE existing `runs/orders/trades` tables** (already in `src/agentfx/api/models/run.py` from v0.1, with `RunEnvironment{DEV, PAPER, LIVE}` already defined). A migration adds `strategy_genome_id` FK, `oanda_environment`, `user_id`, `approval_status`, `kill_requested_at`, `risk_config`. Alternative — parallel `paper_run/paper_trade` tables — rejected because it duplicates schema and blocks v1.0.0 live-trading reuse.

**Deferred to v0.6.1+ (NOT in v0.6.0 MVP):**
- Email / Slack notifications
- OANDA v20 pricing stream (polling every 60s is sufficient for M1+ at MVP)
- Fernet key rotation
- ADMIN visibility of other users' masked credential status
- Live trading toggle in credentials

**Exit Criteria:**
- [x] UX mockups signed off by Wilson before any backend PR opens (hard gate)
- [x] `list_runs`/`list_jobs` filter by `created_by` for non-ADMIN — integration tests confirm user A cannot see user B's runs, jobs, genomes, credentials, or paper runs at any endpoint
- [x] Per-user OANDA credentials encrypted at rest with Fernet; test-connection endpoint verifies against OANDA before setting `verified_at`
- [x] Approval queue delivers approved genomes to the paper runner; run row shows correct `approval_status` state transitions
- [x] Kill switch halts a running paper run in <5s (positions closed on OANDA, status `HALTED_MANUAL` in portal)
- [ ] 30-day continuous paper deployment of at least one genome on UAT completes without unexplained crashes; daily drift between live P&L and backtest OOS P&L is bounded within ±30% — **partially met.** UAT paper runs did reach multi-week wall-clock but hit reliability failures (phantom-flat DB state, zombie subprocess, stranded positions) that surfaced the v0.6.3 work. Rerun the 30-day observation post-v0.6.3 to close this cleanly.

---

## v0.6.1 - Paper Trading Live Execution & Observability (FUNCTIONALLY COMPLETE)

**Status:** All 5 user stories and 6 of 7 exit criteria met (2026-07-28 UAT verification). US-024 (live DSL execution), US-025 (read endpoints), US-026 (persisted equity + backtest overlay), US-027 (approval-queue diagnostics + ADMIN override), US-028 (SL/TP fields) all shipped. Backlog entries flipped on 2026-09-30 during the plan-vs-code audit. 48-hour UAT paper deployment completed per `runbooks/v0.6.1-uat-verification.md`.

**Theme:** Turn the v0.6.0 paper platform from a UX skeleton into a working strategy evaluator — the runner actually trades, and the run-detail page surfaces what it did.

**Why this shape.** The 24-hour UAT smoke-test on 2026-07-08 flushed out a critical gap: `paper_runner._execute_loop` shipped as a monitoring skeleton — it polls balance + risk gates but never fetches candles, compiles the DSL, or places orders. Wilson's real paper run sat RUNNING for 24 hours with zero trades before being halted with `stop_reason='USER_KILL_v0.6.0_gap'`. Every other observability gap surfaced by the smoke-test (empty Trades table, missing decision log, curve resets on reload, placeholder backtest overlay) presupposes the runner is executing. v0.6.1 is therefore not "polish + one bug fix" but a coherent live-execution sprint that unblocks everything downstream. Also picks up an approval-queue diagnostics story: the 2026-07-09 audit found the validator write-back is working (255 correct `false` verdicts on Wilson's UAT DB), the queue was empty because 0/2,040 OOS folds ever cleared Gate B — surfacing that as a real diagnostic + adding an ADMIN override button retires the raw-SQL demo-unblock workaround.

**User Stories:** [US-024](requirements/US-024-paper-runner-live-execution.md), [US-025](requirements/US-025-paper-run-read-endpoints.md), [US-026](requirements/US-026-persisted-equity-and-backtest-overlay.md), [US-027](requirements/US-027-approval-queue-diagnostics.md), [US-028](requirements/US-028-deploy-modal-sl-tp-fields.md)

**Key Capabilities:**
- **Live DSL execution in the paper runner** — every granularity tick the runner fetches candles from the shared `candles` table, calls `compile_dsl_spec(genome.dsl_json)`, evaluates `strategy.generate_signals(df)`, diffs against the current OANDA position via `decide_action`, and places / closes orders. Emits `USER_SIGNAL_EVALUATED`, `USER_ORDER_PLACED`, `USER_POSITION_CLOSED` decision-log entries. Two internal timers: 5s health poll (kill flag, balance, risk gates) and granularity-aligned signal poll (M1 = every 60s aligned to `:00`, H1 = every `HH:00`).
- **Trades / decisions / equity read endpoints** — three RBAC-scoped `GET /paper-runs/{id}/*` endpoints so the run-detail page can show what the runner is doing. `origin` field on trades reserved for v0.7.0 backtest-vs-live overlay.
- **Persisted equity snapshots + backtest OOS overlay** — new `paper_equity_snapshots` table populated on every signal tick so `LivePnLChart` survives page reloads with the full history, and `GET /paper-runs/{id}/backtest-overlay` returns the walk-forward OOS equity time series aligned to the paper run's clock so the overlay curve is real data, not the current placeholder.
- **Approval queue diagnostics + ADMIN validator override** — `/paper/approvals` empty state replaced with a real diagnostic banner: evolution-runs count · genomes validated · genomes passed · closest-to-passing summary with failing gate margins. New ADMIN-only "Override validator verdict" button on `/genomes/:id` writes `USER_VALIDATOR_OVERRIDE` DecisionLog rows with previous / new value + reason, so demo unblocks stop needing raw SQL. Validator write-back path itself is confirmed working — nothing to fix there.
- **DeployPaperModal SL/TP fields** — two optional numeric fields (`stop_loss_pips`, `take_profit_pips`) under an "Advanced risk controls" collapsible; wired into `run.risk_config` and passed to `client.place_market_order` when set. Absent = strategy exits on signal reversal only (same as backtest).

**User-approved decisions** (locked before Phase 1):
- Candle staleness policy: **skip signal evaluation + log warning**, keep polling. Kill switch + daily-loss halt remain the hard stops.
- Loop cadence: **one subprocess, two internal timers.** Not two subprocesses.
- OANDA v20 pricing stream: **defer to v0.6.2.** 60s polling is fine for MVP.
- Order timing: **candle close only.** No intra-bar entries.
- Position flip: **two-step (close then place).** Explicit in the decision log.
- SL/TP sourcing: **`run.risk_config`, not the DSL.** DSL extension deferred.

**Deferred to v0.6.2+ (NOT in v0.6.1):**
- LLM-suggested deployment name on DeployPaperModal
- Email / Slack notifications
- OANDA v20 streaming pricing (replaces polling)
- Live-vs-validation drift dashboard as a separate UI surface

**Exit Criteria:**
- [x] `/paper/approvals` empty state renders the diagnostic banner with real counts + closest-to-passing card; ADMIN override endpoint writes `USER_VALIDATOR_OVERRIDE` DecisionLog rows (US-027 closed).
- [x] `paper_runner._execute_loop` fetches candles → compiles DSL → generates signals → places orders → persists trades on real OANDA Practice, verified by a fake-OANDA integration test.
- [x] `/paper/:id` Trades table populates from `GET /paper-runs/{id}/trades` with entry / exit timestamps, side, quantity, pnl, origin, signal reason.
- [x] `/paper/:id` Decision log streams from `GET /paper-runs/{id}/decisions` with 5s polling — every gate refusal / order / kill visible.
- [x] LivePnLChart survives a browser reload; equity samples persisted to `paper_equity_snapshots`.
- [x] LivePnLChart's backtest overlay curve is real walk-forward OOS equity fetched from `GET /paper-runs/{id}/backtest-overlay`.
- [x] 48-hour UAT paper deployment before tag: 5+ trades executed, no unexplained halts, live-vs-backtest drift within ±30% of backtest expected pnl. Step-by-step verification checklist: [v0.6.1-uat-verification.md](runbooks/v0.6.1-uat-verification.md).

---

## v0.6.2 - Paper Notifications + Deploy UX polish (COMPLETE)

**Status:** Shipped 2026-08 → 2026-09. Original scope was Slack notifications + LLM-suggested deployment name; notifications channel migrated from Slack to email in 2026-09 after mobile-push reliability issues on Wilson's device.

**Key Capabilities:**
- **Terminal + crash-event notifications.** First shipped as Slack incoming-webhook (`notifications/slack.py`); replaced with SendGrid SMTP email (`notifications/email.py`) in commits `352b1e4`, `19dc281`, `92f1dc6`. Subject line tagged with environment (`[AgentFX UAT|STAGING|PROD]`) so a mobile push preview instantly distinguishes envs. `SMTP_HOST/PORT/USER/PASSWORD/EMAIL_FROM/EMAIL_TO` env vars; idempotent per `(run_id, reason)` via `run.parameters['notified']`.
- **LLM-suggested deployment name on DeployPaperModal** (`c2db0a5`) — `POST /paper-runs/suggest-name` calls Claude Haiku to summarise the DSL into a memorable identifier; user can accept, edit, or override.
- **Deploy-flow polish** — removed the misleading `max_position_size_units` field (Wilson UAT feedback 2026-07-19); dynamic sizing (`balance × risk_pct ÷ stop_pips`) is the only ceiling the runner respects.

**Deferred:** OANDA v20 streaming pricing, live-vs-validation drift dashboard (both still open, roll into a later milestone).

**Exit criteria met:** halt/kill/crash events land in `wreyes06@outlook.com` within seconds of the event; suggested-name endpoint returns a validated string on every DeployPaperModal open.

---

## v0.6.3 - Paper Runner Reliability (COMPLETE)

**Status:** Shipped 2026-08 → 2026-09-29. Six commits closed the loop after multi-week UAT paper runs surfaced phantom-flat DB state + silent subprocess death + non-firing mismatch reconciler.

**Theme:** Turn the paper runner from "works on the happy path" into "survives real UAT wall-clock" — subprocess crashes recover automatically, OANDA API weirdness doesn't strand positions, and the DB never disagrees with the broker without an operator seeing an alert.

**Key Capabilities:**
- **Auto-recovery supervisor** (`3bb47bf`) — `scan_and_restart_transient_errors` + `scan_and_restart_zombie_running` scheduled from `api/main.py`; restart decisions logged to `DecisionLog` (`AUTO_RESTART`).
- **Heartbeat + PID tracking** (`da63de7`) — `run.parameters['last_heartbeat_at']` every ~5 min; `run.process_pid` written at spawn; zombie = `status=RUNNING AND (process_pid IS NULL OR heartbeat > 15 min stale)`.
- **Position-mismatch reconciler + stranded-position auto-adopt** (`44368c9`) — every ~60s the reconciler compares OANDA `get_position` (long + short) against the DB's open-trade signed units; on divergence, creates a fresh Trade row (or reopens a phantom-closed one) and alerts.
- **Verify close actually closed before marking DB flat** (`9bdaba6`) — `_flatten_and_halt` cross-checks `close_all_positions` return dict (`long_units_closed + short_units_closed == 0`) and re-fetches `get_position`; writes `USER_POSITION_STRANDED` and preserves `stranded_positions` in `run.parameters` if OANDA still holds units. Fixes the root cause of the phantom-flat state (stale `get_position` during OANDA maintenance returning 0/0).
- **DSL `eval_mode`** (`fc87230`) — `"close"` (legacy) vs `"wick"` (touch-based) SL/TP semantics; closes the runner-vs-backtest divergence from UAT 2026-07-28.
- **OANDA account exclusivity, broker fail-safe SL, walk-driven signal tick** (`248da49`, `b84644c`, `398a6c0`) — a coherent cluster of paper-runner correctness fixes discovered under UAT.

**Exit criteria:**
- [x] A killed subprocess is auto-restarted by the supervisor without operator intervention (verified in staging).
- [x] Phantom-flat DB state can no longer occur — every `close_all_positions` is cross-checked against a subsequent `get_position`.
- [x] Stranded positions are auto-adopted into a new Trade row and the operator is emailed.
- [x] Backtest-vs-runner divergence resolved via `eval_mode` (per-genome opt-in).

**Open:** post-mortem in `runbooks/`, and a stranded-position-specific alert (currently rolls into the generic email).

---

## v0.7.x - Cross-user genome sharing (COMPLETE)

**Status:** Shipped 2026-08 (commits `ce50e22`, `ab4a812`, `f35f8c5`). Full spec in [BACKLOG.md § v0.7.x](BACKLOG.md); the compatibility-checkpoint list has been reconciled to `[x]` for every non-blocked item.

**Theme:** Users on separate OANDA accounts + independent evolution tracks can hand each other validated genomes for evaluation and deployment, without leaking each other's evolution histories.

**Key Capabilities:**
- **Export endpoint** — `GET /evolution-runs/{run_id}/genomes/{genome_id}/export` returns a schema-versioned `.agenome.json` payload with genome core, validation folds, provenance, and a SHA-256 content hash.
- **Import service** — `import_genome_payload` (in `api/services/genome_share.py`) creates SHADOW `EvolutionRun` + `Generation` + `StrategyGenome` + `ValidationFold` rows scoped to the importer (`imported_gen_…` prefix so IDs can't collide with the exporter's on the same DB); `evolution_run.created_by == importer.user_id` makes the shadow genome appear in the importer's approval queue naturally.
- **UI attribution** — amber `IMPORTED` chip on the approval-queue row + deploy modal; imported strategies grouped under a separate tab on the Evolution Runs page; lineage tree renders shadow genomes as single-node trees (no parent_ids).
- **Deploy fidelity** — imported genome's deploy flow computes `fold_length_days` from the duplicated folds so fold-anchored paper runs work identically to native genomes.

**Deferred:** DSL grammar version negotiation (currently rejects on mismatch), signed exports (v1 uses SHA-256 for tamper detection, not authentication).

---

## v0.7.x - Mobile paper-run view (COMPLETE)

Shipped 2026-08-03. Phone-adaptive `/paper/:id` view; low-signal columns collapsed, kill button always visible, decision log virtualised. Existing entry retained below in future considerations if any post-launch iteration is needed.

---

## v0.7.0 - DSL / evolution guardrails (ABSORBED into v0.8.x PE workload)

**Status update (2026-10-05):** v0.7.0 is no longer a standalone milestone. The four items (`_check_indicator_call` extension, grammar docstring guidance, per-fold `window=oos` backtest, compare-mode amber chip) become work items the **Platform Engineer Agent (v0.8.1)** picks up on-task from the Quant Analyst when it identifies DSL-level failure modes in paper divergence reports. If PE opens PRs covering all four, this milestone retires inside the v0.8.x window; otherwise the remaining items roll into v1.0.0's "final hardening" tail.

---

## v0.8.0 - Agent Fleet: read-only pilot

**Theme:** Introduce the first two Claude agents (Portfolio Manager + Quant Analyst) in **read-only mode** — they observe platform state, produce morning briefs + divergence reports, but take no actions. Earns Wilson's trust in the agents' judgement before any authority is granted.

**Why this shape.** Wilson's platform is fully manual: he starts every evolution, approves every paper deployment, kills every losing run. The vision is a fleet of specialist agents doing that work autonomously — but handing them authority before validating their judgement would amplify platform mistakes. v0.8.0 ships the observe-and-report shell so Wilson can calibrate the fleet before progressively expanding its mandate.

**Branch strategy:** All v0.8.x work lives on `feat/agent-fleet` (cut from `main` at `1c42fc6`). Staging continues to deploy from `main`. Wilson gates the final `feat/agent-fleet` → `main` merge.

**Cost envelope:** $20-30/month total (LLM + Managed Agents compute). Enforced per-agent daily token budgets.

**Key Capabilities (v0.8.0):**
- **Portfolio Manager Agent** (Haiku 4.5, daily cron) — reads last 7 days of evolution + paper P&L, writes a morning brief to Wilson's inbox + agent memory. Read-only.
- **Quant Analyst Agent** (Sonnet 5, event-triggered) — fires on paper-run day-close or stop_reason events. Produces divergence reports: *"is the live behaviour an exact reflection of the backtest? If not, why?"* Classifies deltas (cost-model, execution-timing, indicator handling, regime shift, validator weakness).
- **Shared memory layer** — `agent_decisions` DB table + `agent_memory/` markdown directory. Reflexion dataset.
- **Orchestration via Claude Agent SDK** — single `services/agent_orchestrator.py` wired into FastAPI lifespan behind `AGENT_FLEET_ENABLED` flag.
- **Profitability scoreboard** — `agent_memory/scoreboard.md` updated by PM morning brief; Wilson's at-a-glance view of advancement along the profit chain.

**Exit Criteria:**
- [ ] PM Agent has sent ≥14 morning briefs with no manual intervention.
- [ ] QA Agent has produced ≥5 divergence reports; at least one correctly characterises the current staging paper runs' cost-model gap without hard-coded hints.
- [ ] `agent_decisions` DB table has >100 rows; `agent_memory/` has ≥3 topic files.
- [ ] Total LLM spend < $15 (buffer below $30 cap).
- [ ] **Profitability checkpoint:** QA has identified ≥3 concrete evolution-vs-paper divergences and quantified their impact on expected post-cost sharpe.

---

## v0.8.1 - Agent Fleet: autonomous scheduling + code authorship

**Theme:** PM gets its first action tool (schedule evolution runs). The Platform Engineer Agent lands in a Managed Agents workspace and ships its first autonomous PRs.

**Branch strategy:** PE Agent operates on `agent/<task-id>` sub-branches off `feat/agent-fleet`. Three-tier CI gate: `agent/*` auto-merges to `feat/agent-fleet` on tests-pass + PE review; `feat/agent-fleet` → `main` requires Wilson's manual approval.

**Key Capabilities (v0.8.1):**
- **PM scheduling tool** — PM can call `/evolution-runs` POST with its chosen (instrument, gran, config). Rate-limited to 2 evolution runs/week initially. First autonomous use: schedules the H1 EUR_USD experiment (lower noise than M1 — SOTA literature + Phase 1 Plan agent recommendation).
- **Platform Engineer Agent** (Opus 4.7, Managed Agents workspace, on-task from QA/PM) — reads task spec, forms hypothesis, writes reproducing test, writes fix, runs `make test-unit`, opens PR to `agent/*` sub-branch. Auto-merges to `feat/agent-fleet` on tests-pass + review agent approval.
- **First PE workload:** take QA's cost-model divergence report, update `instruments.typical_spread_pips` for EUR_USD M1 from realized paper fills, open PR with reproducing test.
- **OANDA account pool** — new `oanda_account_pool` table + ADMIN endpoint for Wilson to add practice accounts. PM allocates slots across the pool for parallel experiments. Initial seed ~5 accounts, expandable.

**Exit Criteria:**
- [ ] PM has scheduled ≥2 evolution runs autonomously.
- [ ] PE has merged ≥3 PRs to `feat/agent-fleet`, all with passing tests.
- [ ] Reproducing test for Run B cost-model issue exists in the codebase.
- [ ] Account pool has ≥3 accounts registered; PM has allocated runs across ≥2 of them.
- [ ] **Profitability checkpoint:** at least one PE PR directly moves the honest post-cost expected sharpe higher.

---

## v0.8.2 - Agent Fleet: autonomous paper lifecycle

**Theme:** PM gets paper-approval authority. QA + PE close the loop on live-vs-backtest divergence autonomously. The platform becomes self-improving.

**Key Capabilities (v0.8.2):**
- **PM paper-approval authority (unbounded on paper)** — reflecting the "paper is the aggressive sandbox" directive. No allocation caps; multiple parallel runs across pairs/timeframes/parameterisations. PM reads approval queue, decides what to deploy, posts to `/paper-runs/approve`.
- **Paper-deploy gate hardening** (PE's first "QA → PE pipeline" task) — fix the `paper.py:677` hole where `validator_passed=TRUE` was accepted even with non-empty `validation_failures` or zero `validation_folds`.
- **QA → PE loop closure** — QA identifies defect → PE opens PR → tests pass → merges to `feat/agent-fleet` → PM reviews impact → cycle continues.
- **CUSUM live drift detection** (Agent 3 R4) — ships as PE workload when QA identifies its need. One-sided CUSUM on daily realised-vs-backtest P&L residuals.
- **Reflexion loop** — PM writes expected outcome at decision time; later re-reads and compares to actual. Memory layer gets "lessons learned" entries. QA uses these as prompt context.

**Exit Criteria:**
- [ ] PM has approved ≥3 paper deployments autonomously.
- [ ] QA has caught + reported ≥1 regression PE introduced (demonstrates Reflexion loop).
- [ ] Live-vs-backtest divergence on at least one paper run under 1σ for ≥7 continuous days.
- [ ] CUSUM shipped and firing correctly on at least one paper run.
- [ ] Monthly cost still within envelope.
- [ ] **Profitability checkpoint (Week 10):** ≥1 strategy sustains positive paper P&L for 14 continuous days with divergence under 1σ.

---

## v0.8.3 - Agent Fleet: production merge

**Theme:** Merge `feat/agent-fleet` → `main`. Staging + Railway run the full fleet.

**Exit Criteria:**
- [ ] 1-2 weeks soak on `feat/agent-fleet` running on UAT + dev with no Wilson intervention required.
- [ ] Wilson approves the single PR from `feat/agent-fleet` → `main`.
- [ ] 2 continuous weeks of agent-fleet on `main` without Wilson intervention.
- [ ] Monthly agent cost at steady state within $20-30 envelope.
- [ ] At least one paper run that the fleet designed, deployed, monitored, and halted/kept — Wilson only in the loop for merge-to-main.

---

## v0.9.0 - Live-nomination pipeline

**Theme:** The fleet produces its first paper-strategy nomination with the full evidence package. Wilson reviews. If approved, the strategy queues for v1.0.0 live deployment. This is where the agent fleet's experimentation output converges on the ultimate goal — profitable, live-ready strategies.

**Scope:**
- **Nomination schema** — a formal `strategy_nomination` record with: 30-day paper P&L, divergence report, lineage, cost-model notes, Research Agent corroboration against SOTA literature, PM reasoning, sanity-check output ("would we still deploy if first 7 days had been losses?").
- **PM composes nominations** — once a paper run clears the 30-day-profitable-with-divergence-under-threshold bar.
- **Wilson's review interface** — simple approval endpoint + email with the full nomination package. Wilson approves, rejects (with reasoning that goes into agent memory), or requests more paper time.

**Exit Criteria:**
- [ ] At least 1 strategy has reached the nominated state with the full evidence package.
- [ ] Wilson has reviewed ≥1 nomination end-to-end.
- [ ] Approval path to v1.0.0 live-trading is clear (even if first nomination is rejected — the mechanism works).

---

## v1.0.0 - Regime-Aware Portfolio + Live Trading

**Theme:** Self-sustaining strategy factory in production — the end-state described in the North Star section.

**Goal:** Platform continuously discovers, evolves, validates, paper-trades, and (with human approval) live-trades a *regime-aware portfolio* of profitable strategies, hitting the North Star floor of 20–30% reliable monthly returns with minimal human oversight.

**Dependency on v0.6.0:** Live trading reuses the per-user credential vault built in v0.6.0. `oanda_environment=LIVE` is a database column on `user_oanda_credentials` gated behind `settings.LIVE_TRADING_ENABLED` (feature flag, default false in v0.6.0) AND an ADMIN role AND a second approval flow. Fernet-encrypted at rest; same access pattern as PRACTICE credentials — the only real difference is the OANDA API host and the approval gates.

**Key Capabilities:**
- *Live trading integration* — OANDA Live with the second human approval gate (paper → live), building on the v0.6.0 credential vault
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
