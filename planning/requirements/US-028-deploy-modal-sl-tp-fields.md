# US-028: DeployPaperModal SL/TP Fields

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P2 — Nice to Have (US-024 works without it via signal-reversal exits, but broker-side SL/TP is safer for overnight runs)
**Status:** TODO

## Genesis

US-024's runner passes `stop_loss_pips` / `take_profit_pips` to `client.place_market_order` when set on `run.risk_config` — otherwise no broker-side SL/TP is registered and the strategy exits only on signal reversal. That's the safe default for validating parity with backtest, but for a run left unattended overnight the daily-loss halt is the only safety net, and it only fires at the 2% mark. Adding user-tunable SL/TP at deploy time gives Wilson a tighter per-trade risk gate before he lets a paper run breathe overnight.

The fields belong on the DeployPaperModal because that's the last chance the user sees the run config before the runner takes over. Making them optional and hidden behind an "Advanced risk controls" collapsible keeps the modal uncluttered for the common case.

## Given-When-Then

**GIVEN** the user is on `/paper/approvals` and clicks "Approve" on a validated genome
**WHEN** the `DeployPaperModal` opens
**THEN** the form shows the existing fields (name, instrument, granularity, initial_balance, max_position_size, loss_cap_pct, kill_switch_dd_pct) plus a collapsed "Advanced risk controls" section containing two new optional NumericFields:
- **Stop loss (pips)** — decimal, min 1, max 500, no default (empty).
- **Take profit (pips)** — decimal, min 1, max 1000, no default (empty).

**GIVEN** the user enters values for one or both fields and submits
**WHEN** the modal calls `POST /api/v1/paper-runs/{run_id}/approve` with the deploy config
**THEN** the request body contains `risk_config.stop_loss_pips` / `risk_config.take_profit_pips` for whichever fields were filled; omitted fields are not sent (server-side treats `None` = no broker-side SL/TP).

**GIVEN** the user leaves both fields empty and submits
**WHEN** the runner places its first order
**THEN** `client.place_market_order(instrument, signed_units, stop_loss_pips=None, take_profit_pips=None)` — no broker-side SL/TP; the strategy exits on signal reversal.

**GIVEN** the user entered SL=20, TP=40
**WHEN** the runner places its first order
**THEN** the OANDA order includes SL 20 pips below entry (for long) or 20 pips above entry (for short); TP 40 pips beyond entry in the opposite direction.

## Acceptance Criteria

### Frontend

- [ ] `portal/web/src/components/DeployPaperModal.tsx` — new collapsible "Advanced risk controls" section (default collapsed). Uses the existing `NumericField` component pattern for consistency (`portal/web/src/components/NumericField.tsx`).
- [ ] Fields validate client-side: SL 1-500, TP 1-1000. Invalid entries show the standard NumericField error state.
- [ ] Both fields are optional; leaving them blank is not a validation error.
- [ ] On submit, `risk_config` in the request body only includes keys the user actually filled (no `undefined` / `null` values).
- [ ] Vitest for `DeployPaperModal` submission with (i) both fields empty, (ii) SL only, (iii) SL + TP; asserts the request body shape in each case.

### Backend

- [ ] `POST /api/v1/paper-runs/{run_id}/approve` schema — `risk_config` accepts optional `stop_loss_pips: PositiveFloat` and `take_profit_pips: PositiveFloat`.
- [ ] Values pass through unchanged into the `runs.risk_config` JSON column.
- [ ] No new endpoint; extends the existing approve payload.

### Runner (already covered by US-024, listed here for cross-reference)

- [ ] `_signal_tick` reads `run.risk_config.get('stop_loss_pips')` / `.get('take_profit_pips')` and passes to `client.place_market_order`. Absent = `None`. Already in the US-024 acceptance criteria.

### Tests

- [ ] `portal/web/tests/components/DeployPaperModal.test.tsx` (update) — the three submission scenarios above.
- [ ] `tests/integration/test_paper_approve_with_sl_tp.py` (new) — POST to `/approve` with SL/TP set; assert `runs.risk_config['stop_loss_pips']` = the submitted value.
- [ ] Runner integration test (`test_paper_runner_full_cycle` from US-024) is updated to run once with SL/TP set on the run row and assert `place_market_order` receives those values in one of the parametrized cases.

## Files Involved

- `portal/web/src/components/DeployPaperModal.tsx` — Advanced risk controls collapsible + two fields
- `portal/web/src/services/paperApi.ts` — `approvePaperRun` request body type extended
- `src/agentfx/api/schemas/paper.py` — `PaperApproveRequest.risk_config` extended (already free-form JSON; explicit typing preferred)
- `portal/web/tests/components/DeployPaperModal.test.tsx` (update)
- `tests/integration/test_paper_approve_with_sl_tp.py` (new)

## Verification

1. `make test-unit` — Vitest submission scenarios pass.
2. `make test-integration` — approve endpoint accepts SL/TP and persists them into `risk_config`.
3. **UAT manual verification:**
   - Approve a genome with SL=20, TP=40.
   - When the first order fires, check the OANDA Practice dashboard: the trade shows an SL 20 pips below entry (long) or above (short), and a TP 40 pips beyond entry.
   - Approve a second genome with fields empty. Confirm the OANDA order has no SL/TP registered.
