# US-023: Kill Switch + Risk Controls + Approval State Machine

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (safety-critical, ships with US-021 backend)
**Status:** TODO

## Given-When-Then

### Kill switch

**GIVEN** a paper run is in `approval_status=RUNNING`
  **AND** the TRADER who owns it is viewing `/paper/:id`
**WHEN** they click the big red `KillSwitchButton` in the header
**THEN** a toast appears at the top of the viewport containing:
  1. The message "Kill in 3… 2… 1…" counting down
  2. An "Undo" button
  3. A progress bar

  **AND IF** the user clicks Undo within the 3-second window
**THEN** the toast dismisses, no kill signal is sent, the button re-enables, no side effect

  **AND IF** the 3-second window elapses
**THEN** the frontend calls `POST /api/v1/paper-runs/:id/kill`
  **AND** the backend sets `runs.kill_requested_at = now()`
  **AND** the paper runner (in a subprocess) polls this field within one loop cycle (at most granularity-seconds later)
  **AND** the runner: (i) attempts `client.close_all_positions(instrument)`, (ii) writes final `orders` / `trades` rows, (iii) transitions to `approval_status=HALTED_MANUAL`, `stop_reason=USER_KILL`
  **AND** the portal detail page (5s polling) reflects the halted status and closed positions

### Risk controls

**GIVEN** a paper run has `risk_config = {max_position_size, daily_loss_threshold, initial_balance}`
**WHEN** the paper runner is about to place an order OR is polling P&L
**THEN**:
  - If cumulative daily loss (running total of `trades.pnl` since 00:00 UTC today) < `-daily_loss_threshold`: halt run with `stop_reason=DAILY_LOSS_LIMIT`, close all positions
  - If projected order units > `max_position_size`: reject the order, log a `RISK_REJECT` decision, do not halt run (single-order guard, not fatal)
  - If OANDA circuit breaker raises `OandaCircuitOpen`: halt run with `stop_reason=BROKER_CIRCUIT`, attempt best-effort position close

### Approval state machine

**GIVEN** a genome has `validator_passed=true`
  **AND** the owning user clicks "Send to approval queue" from `GenomeDetail`
**WHEN** the API creates the `runs` row
**THEN** the row's `approval_status` follows this state machine:

```
[click Send to Queue]        → AWAITING_APPROVAL
[user clicks Approve]        → APPROVED (paper runner enqueued)
[runner starts subprocess]   → RUNNING
[user clicks Reject]         → REJECTED (from AWAITING_APPROVAL only)
[runner completes normally]  → COMPLETED
[user clicks Kill Switch]    → HALTED_MANUAL (from RUNNING only)
[risk control triggers]      → HALTED_MANUAL (from RUNNING; stop_reason distinguishes)
[subprocess crashes]         → ERROR
```

Illegal transitions (e.g. RUNNING → APPROVED) return `HTTP 409 Conflict`.

## Acceptance Criteria

- [ ] `KillSwitchButton.tsx` shows 3-second countdown toast on click; Undo cancels; timeout fires `POST /kill` (US-022)
- [ ] `runs.kill_requested_at` column populated on kill; paper runner polls this and honours it within one loop cycle
- [ ] `runs.approval_status` transitions validated at API layer via a state-machine helper; illegal transitions return 409
- [ ] Daily loss auto-halt: paper runner tracks cumulative daily P&L and halts on threshold breach
- [ ] Max position size guard: paper runner rejects orders exceeding threshold without halting the run
- [ ] Circuit-breaker halt: catches `OandaCircuitOpen` from US-020 adapter and halts with correct stop_reason
- [ ] All halts attempt best-effort position close via `client.close_all_positions(instrument)`; log outcome to `decision_logs`

## Tests

- [ ] `tests/integration/test_kill_switch.py` — using respx fake OANDA server:
  - Approve a genome → RUNNING
  - Set `kill_requested_at` via API → runner closes positions within one loop cycle
  - Assert final status `HALTED_MANUAL`, `stop_reason=USER_KILL`
  - Assert OANDA-side positions closed (via fake server assertion)
- [ ] `tests/integration/test_daily_loss_halt.py` — simulate consecutive losing trades summing below threshold; assert halt
- [ ] `tests/integration/test_circuit_breaker_halt.py` — respx returns 5 consecutive 503s; assert runner catches `OandaCircuitOpen` and halts
- [ ] `tests/integration/test_approval_state_machine.py` — parameterised over all valid + invalid transitions
- [ ] Playwright E2E `portal/web/tests/e2e/paper-trading.spec.ts` — sign in, verify credentials, send to queue, approve, RUNNING, click Kill, 3s elapses (do NOT click Undo), status → HALTED_MANUAL
- [ ] Playwright E2E — same but click Undo mid-countdown; assert no kill signal sent, run stays RUNNING

## API Contract

### Kill switch

```
POST /api/v1/paper-runs/{run_id}/kill
Authorization: Bearer <JWT>
```

Response 202 Accepted:
```json
{
  "run_id": "run_...",
  "kill_requested_at": "2026-07-05T14:22:00Z",
  "estimated_halt_by": "2026-07-05T14:22:01Z"
}
```

Response 409 Conflict (if run not RUNNING):
```json
{
  "detail": "Cannot kill run in state COMPLETED"
}
```

### State machine

Illegal transitions surface as 409 with detail message describing the attempted → target states and the legal alternative.

## Files Involved

- Backend:
  - `src/agentfx/services/paper_runner.py` (edit — kill polling, risk checks)
  - `src/agentfx/api/services/paper.py` (edit — state-machine helper)
  - `src/agentfx/api/routers/paper.py` (edit — kill endpoint, state-check on approve/reject)
- Frontend (implemented in US-022):
  - `portal/web/src/components/KillSwitchButton.tsx`
- Tests:
  - `tests/integration/test_kill_switch.py` (new)
  - `tests/integration/test_daily_loss_halt.py` (new)
  - `tests/integration/test_circuit_breaker_halt.py` (new)
  - `tests/integration/test_approval_state_machine.py` (new)
  - `portal/web/tests/e2e/paper-trading.spec.ts` (new)

## Verification

1. `make test-integration` — all four new integration suites pass
2. `make e2e` — Playwright suite passes both kill-happy-path and kill-undo scenarios
3. Manual UAT: deploy a genome, wait for a couple of orders to fill on OANDA Practice, click Kill Switch, do NOT click Undo, watch positions close on OANDA dashboard within 5 seconds, confirm portal shows `HALTED_MANUAL`
4. Manual UAT: same but click Undo mid-countdown, confirm run keeps running, no positions closed
5. Manual UAT (risk control): edit a running run's `risk_config.daily_loss_threshold` in DB to a very tight value; wait for one losing trade; confirm auto-halt fires
