# US-026: Persisted Equity Snapshots + Backtest OOS Overlay

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P1 — Should Have (LivePnLChart survives reload; overlay curve is real data)
**Status:** TODO

## Genesis

Two smoke-test findings from 2026-07-08 collapse into a single US:

1. `LivePnLChart` accumulates equity samples only while the tab is open. Reload the page after 30 minutes of runtime and the chart starts fresh from the reload timestamp — the curve before that is gone. Users can't leave a run for an hour and come back to see how it went.
2. The backtest OOS overlay curve on `LivePnLChart` is a hard-coded placeholder from the PR-J ship. It doesn't reflect the actual walk-forward OOS equity of the deployed genome, so users can't visually compare live vs backtest — the whole point of the overlay.

Both need one new table (`paper_equity_snapshots`) and one new endpoint (`GET /paper-runs/{id}/backtest-overlay`).

## Given-When-Then — Persisted Equity

**GIVEN** US-024 is live (runner executes a signal tick every granularity boundary)
**WHEN** the runner completes a signal tick
**THEN** it inserts a `paper_equity_snapshots` row with the post-action balance and unrealized_pnl before advancing `next_signal_at`.

**GIVEN** a `paper_equity_snapshots` history exists for `run_id=X`
**WHEN** the browser loads `/paper/:id` for run X
**THEN** `LivePnLChart` fetches the full history via `GET /paper-runs/{id}/equity` (see US-025) and renders the curve from `run_started_at` through the latest snapshot, before the 5s polling loop begins accumulating new samples on top.

## Given-When-Then — Backtest OOS Overlay

**GIVEN** the deployed genome came from evolution run E, and E has walk-forward OOS equity data persisted (fold-level `oos_equity_series` in the evolution results)
**WHEN** the browser loads `/paper/:id` for a run deployed from that genome
**THEN** the backtest overlay curve on `LivePnLChart` is populated from `GET /paper-runs/{id}/backtest-overlay` — the walk-forward OOS equity time series normalized to seconds-since-run-start so it renders against the LivePnLChart's relative x-axis.

**GIVEN** the evolution run has no persisted OOS equity series (older evolutions from before v0.4.0 fold-consistency work)
**WHEN** the browser requests the backtest overlay
**THEN** the endpoint returns `{"items": [], "reason": "no_oos_equity_persisted"}` with 200. The frontend renders a small "backtest overlay unavailable — evolution predates OOS series persistence" note where the dashed curve would be.

## Acceptance Criteria

### Schema

- [ ] Alembic migration creates `paper_equity_snapshots`:
  - `id` UUID PK
  - `run_id` VARCHAR FK → `runs.id` NOT NULL
  - `timestamp` TIMESTAMPTZ NOT NULL
  - `balance` DECIMAL(18, 2) NOT NULL
  - `unrealized_pnl` DECIMAL(18, 2) NOT NULL DEFAULT 0
  - Index on `(run_id, timestamp)` — hot path for the equity list endpoint

- [ ] ORM model `PaperEquitySnapshot` in `src/agentfx/api/models/paper.py`, added to the models `__init__` exports.

### Runner persistence hook

- [ ] `paper_runner._signal_tick` (from US-024) inserts a `PaperEquitySnapshot` row after the order-action step, before advancing `next_signal_at`. Timestamp = signal-tick UTC now. Balance = post-tick `run.current_balance`. `unrealized_pnl` computed from `client.get_position(instrument).unrealized_pnl` (fall through to 0 if no position).

- [ ] The health tick does NOT snapshot equity — otherwise we'd write 12x more rows than needed. Only signal ticks persist.

### Endpoint — Backtest overlay

- [ ] `GET /api/v1/paper-runs/{run_id}/backtest-overlay` — RBAC-scoped. Response shape:

```json
{
  "items": [
    {"timestamp_relative_seconds": 0, "balance": 10000.00},
    {"timestamp_relative_seconds": 60, "balance": 10007.50},
    ...
  ],
  "source": "walk_forward_oos",
  "reason": null
}
```

For unavailable OOS series, `items: []`, `source: "unavailable"`, `reason: "no_oos_equity_persisted" | "genome_predates_v0_4_0" | "no_associated_evolution_run"`.

- [ ] Service resolution logic (`src/agentfx/api/services/paper.py::get_backtest_overlay`):
  1. Load `Run.strategy_genome_id`. If null → return `no_associated_evolution_run`.
  2. Walk `strategy_genome → generation → evolution_run`. If null at any step → return `no_associated_evolution_run`.
  3. Load the champion-by-OOS fold's persisted `oos_equity_series` (JSON array on the fold result row).
  4. Normalize: subtract the first timestamp from every timestamp so `t=0` at OOS-start; multiply by the granularity's seconds so the x-axis matches the LivePnLChart's `hours-since-run-start` axis.

### Frontend

- [ ] `portal/web/src/services/paperApi.ts` — new `getBacktestOverlay(runId): Promise<BacktestOverlayResponse>`.
- [ ] `portal/web/src/pages/PaperRunDetail.tsx` — on mount, `getBacktestOverlay(runId)` and pass the array as `backtestSeries` prop to `LivePnLChart`. When `items` is empty, render the "unavailable" note below the chart.
- [ ] `portal/web/src/components/LivePnLChart.tsx` — no behavioral change; already accepts `backtestSeries` prop from v0.6.0 (it just wasn't wired). Update the header/subtitle to reference "walk-forward OOS" instead of "placeholder".

### Tests

- [ ] `tests/unit/services/test_backtest_overlay_resolution.py` (new) — table-driven: (i) genome with OOS series → returns normalized array, (ii) genome without OOS series → `no_oos_equity_persisted`, (iii) genome with null `evolution_run_id` → `no_associated_evolution_run`.
- [ ] `tests/integration/test_paper_equity_snapshots.py` (new) — seed a running paper run, drive 5 signal ticks, assert 5 rows in `paper_equity_snapshots` (not 60 — signal-tick-only persistence).
- [ ] Vitest `portal/web/tests/components/LivePnLChart.test.tsx` — mounted with a real `backtestSeries` array, asserts both curves render; mounted with empty array, asserts the "unavailable" note.

## Files Involved

- `migrations/versions/2026_07_XX_paper_equity_snapshots.py` (new)
- `src/agentfx/api/models/paper.py` — add `PaperEquitySnapshot`
- `src/agentfx/api/models/__init__.py` — export the new model
- `src/agentfx/services/paper_runner.py` — persistence hook in `_signal_tick`
- `src/agentfx/api/routers/paper.py` — new `/backtest-overlay` endpoint
- `src/agentfx/api/services/paper.py` — `get_backtest_overlay` resolution logic
- `src/agentfx/api/schemas/paper.py` — `BacktestOverlayResponse` schema
- `portal/web/src/services/paperApi.ts` — `getBacktestOverlay`
- `portal/web/src/pages/PaperRunDetail.tsx` — wire overlay
- `portal/web/src/components/LivePnLChart.tsx` — subtitle update only
- `tests/unit/services/test_backtest_overlay_resolution.py` (new)
- `tests/integration/test_paper_equity_snapshots.py` (new)
- `portal/web/tests/components/LivePnLChart.test.tsx` (updated)

## Verification

1. `make test-unit` — backtest-overlay resolution + LivePnLChart Vitest pass.
2. `make test-integration` — signal ticks persist exactly one snapshot each; no snapshots from health ticks.
3. **UAT manual verification:**
   - Deploy a genome. Wait 10 minutes. Reload `/paper/:id`.
   - LivePnLChart shows the accumulated curve from run start, not from reload time.
   - Backtest OOS overlay is drawn behind the live curve as a dashed grey line, visually diverging from live as expected.
   - If the deployed genome came from a pre-v0.4.0 evolution: chart shows the "unavailable" note instead of an error.
