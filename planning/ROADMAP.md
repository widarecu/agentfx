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

## v0.2.0 - Parameter Optimizer Agent

**Theme:** First autonomous capability

**Goal:** System can autonomously optimize strategy parameters and identify profitable configurations.

**Key Capabilities:**
- Grid search optimization over strategy parameter space
- Experiment tracking with full lineage
- Async job processing
- Portal to view optimization results
- Real-time WebSocket streaming (deferred from v0.1.0)

**User Stories:** [US-001](requirements/US-001-optimization-runs.md), [US-002](requirements/US-002-view-optimization.md)

**Exit Criteria:**
- [ ] Optimizer agent runs 90+ backtests without human intervention
- [ ] Top 3 parameter sets identified and ranked by Sharpe
- [ ] User can view results in portal
- [ ] Real-time price streaming via WebSocket

---

## v0.3.0 - Validation Agent

**Theme:** Automated quality gates

**Goal:** System validates strategies against Gate B thresholds before promotion.

**Key Capabilities:**
- Walk-forward validation with out-of-sample holdout
- Gate B threshold enforcement (Sharpe >= 1.0, MDD <= 10%, Hit Rate >= 48%)
- Automated pass/fail determination
- Validation audit trail

**User Stories:** US-003, US-004 (to be defined)

**Exit Criteria:**
- [ ] Validator blocks promotion of failing strategies
- [ ] Validation results stored with metrics
- [ ] Clear failure reasons provided

---

## v0.4.0 - Discovery Agent

**Theme:** Autonomous hypothesis generation

**Goal:** System proposes new strategy configurations to test.

**Key Capabilities:**
- Strategy template variations (indicator combinations)
- Guided exploration based on past performance
- Hypothesis pre-scoring to prune unpromising paths

**User Stories:** US-005, US-006 (to be defined)

**Exit Criteria:**
- [ ] Discovery generates 10+ valid strategy configs
- [ ] Configs are queued for optimization pipeline
- [ ] Discovery reasoning is logged

---

## v0.5.0 - Orchestrator & Evolution

**Theme:** Full autonomous loop

**Goal:** Complete pipeline runs without human intervention. Strategies ready for live trading are surfaced for approval.

**Key Capabilities:**
- Pipeline coordination: Discover -> Optimize -> Validate
- Performance drift detection
- Human approval gate for paper trading promotion
- Paper trading execution on OANDA Practice

**User Stories:** US-007, US-008, US-009 (to be defined)

**Exit Criteria:**
- [ ] Full pipeline runs autonomously (end-to-end)
- [ ] Strategies awaiting approval shown in portal
- [ ] Drift detection triggers re-optimization
- [ ] Paper trades execute on OANDA Practice

---

## v1.0.0 - Production Autonomy

**Theme:** Self-sustaining strategy factory

**Goal:** Platform continuously discovers and evolves profitable strategies with minimal human oversight.

**Key Capabilities:**
- Scheduled discovery runs (daily/weekly)
- Multi-strategy portfolio management
- Live trading integration (OANDA Live) with human approval
- Continuous evolution and adaptation

**Exit Criteria:**
- [ ] At least 1 strategy successfully promoted to live trading
- [ ] Platform generates net positive returns over 3+ months
- [ ] Evolution agent successfully adapts to regime change

---

## Future Considerations (Post v1.0)

- ML-based strategy discovery (reinforcement learning)
- Multi-asset support (indices, commodities)
- Sentiment integration (news, social)
- Advanced portfolio optimization (Kelly, mean-variance)
- Institutional-grade reporting and compliance

---

## References

- [BACKLOG.md](BACKLOG.md) - Detailed task breakdown
- [requirements/](requirements/) - Executable user stories
- [MVP_CONSTRAINTS.md](MVP_CONSTRAINTS.md) - Risk and operational limits
- [archive/](archive/) - Historical planning documents
