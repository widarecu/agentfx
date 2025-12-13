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

## v0.2.0 - Parameter Optimizer Agent

**Theme:** First autonomous capability - optimize strategy parameters

### P0 - Must Have
- [ ] `TODO` Real-time WebSocket streaming (deferred from v0.1.0)
  - Req: [US-013](requirements/US-013-websocket-streaming.md)
  - Files: `src/agentfx/api/routers/websocket.py`, `portal/web/src/services/marketDataWebSocket.ts`

- [ ] `TODO` Optimizer agent - Grid search over strategy params
  - Req: [US-001](requirements/US-001-optimization-runs.md)
  - Files: `src/agentfx/agents/optimizer.py`

- [ ] `TODO` Experiment persistence - Store runs with lineage
  - Req: [US-001](requirements/US-001-optimization-runs.md)
  - Files: `src/agentfx/api/models/experiment.py`

- [ ] `TODO` Optimization API endpoints
  - Req: [US-001](requirements/US-001-optimization-runs.md), [US-002](requirements/US-002-view-optimization.md)
  - Files: `src/agentfx/api/routers/optimize.py`

- [ ] `TODO` Optimization portal page - View results
  - Req: [US-002](requirements/US-002-view-optimization.md)
  - Files: `portal/web/src/pages/Optimization.tsx`

- [ ] `TODO` RunDetail page implementation
  - Files: `portal/web/src/pages/RunDetail.tsx`
  - Dependencies: `/api/pairs/`, `/api/runs/{id}/signals` endpoints

### P1 - Should Have
- [ ] `TODO` Bayesian optimization - Smarter param search
  - Req: [US-001](requirements/US-001-optimization-runs.md) (enhancement)
  - Files: `src/agentfx/agents/optimizer.py`

### P2 - Could Have
- [ ] `TODO` Optimization export - CSV/JSON download
  - Files: `src/agentfx/api/routers/optimize.py`

---

## v0.3.0 - Validation Agent

**Theme:** Automated strategy validation pipeline

### P0 - Must Have
- [ ] `TODO` Validation agent - Walk-forward with OOS holdout
  - Req: US-003 (to be created)
  - Files: `src/agentfx/agents/validator.py`

- [ ] `TODO` Gate B threshold enforcement
  - Req: US-004 (to be created)
  - Files: `src/agentfx/core/gates.py`

- [ ] `TODO` Validation API endpoints
  - Req: US-003, US-004
  - Files: `src/agentfx/api/routers/validate.py`

### P1 - Should Have
- [ ] `TODO` Validation history portal page
  - Files: `portal/web/src/pages/Validation.tsx`

---

## v0.4.0 - Discovery Agent

**Theme:** Autonomous strategy hypothesis generation

### P0 - Must Have
- [ ] `TODO` Discovery agent - Generate strategy variations
  - Req: US-005 (to be created)
  - Files: `src/agentfx/agents/discovery.py`

- [ ] `TODO` Strategy template combinations
  - Req: US-005
  - Files: `src/agentfx/core/strategy_templates.py`

- [ ] `TODO` Discovery API endpoints
  - Req: US-005, US-006
  - Files: `src/agentfx/api/routers/discover.py`

### P1 - Should Have
- [ ] `TODO` Hypothesis scoring - Pre-filter before backtest
  - Req: US-006 (to be created)
  - Files: `src/agentfx/agents/discovery.py`

---

## v0.5.0 - Orchestrator & Evolution

**Theme:** Full autonomous loop with human approval gate

### P0 - Must Have
- [ ] `TODO` Orchestrator agent - Coordinate pipeline
  - Req: US-007 (to be created)
  - Files: `src/agentfx/agents/orchestrator.py`

- [ ] `TODO` Evolution agent - Drift detection
  - Req: US-009 (to be created)
  - Files: `src/agentfx/agents/evolution.py`

- [ ] `TODO` Paper trading service
  - Req: US-007, US-008
  - Files: `src/agentfx/services/paper_runner.py`

- [ ] `TODO` Approval queue portal page
  - Req: US-008 (to be created)
  - Files: `portal/web/src/pages/Approvals.tsx`

### P1 - Should Have
- [ ] `TODO` Email/Slack notifications for approval requests
- [ ] `TODO` Scheduled discovery runs (cron)

---

## v1.0.0 - Production Autonomy

**Theme:** Self-sustaining strategy factory

### P0 - Must Have
- [ ] `TODO` Live trading integration (OANDA Live)
- [ ] `TODO` Multi-strategy portfolio management
- [ ] `TODO` Performance monitoring dashboard

### P1 - Should Have
- [ ] `TODO` Automated promotion: paper to live (with approval)
- [ ] `TODO` Strategy versioning and rollback

---

## Legacy Items (Archived)

The following items from the original backlog have been archived to `planning/archive/`:
- FEAT-0001 through FEAT-0006 (replaced by agentic milestones)
- ADR-0001, ADR-0002 (decisions preserved for reference)
- BUS-0001, SYS-0001, TECH-0001, TECH-0002, TEST-0001

These documents contain valuable context and are preserved for historical reference.
