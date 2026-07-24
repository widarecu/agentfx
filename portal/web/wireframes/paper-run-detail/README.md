# Live vs Backtest Equity — redesign brief

Replaces the current `portal/web/src/components/LivePnLChart.tsx` on `/paper/:id`. Motivated by Wilson's feedback that today's chart is not usable — no tooltips, opaque `h0.00 → h167.00` axis, absent OOS overlay, no summary of "how is the strategy actually doing."

- **Low-fi wireframe:** [`lfi-live-pnl-chart.html`](./lfi-live-pnl-chart.html)
- **High-fi mockup (interactive):** [`hi-fi-live-pnl-chart.html`](./hi-fi-live-pnl-chart.html) — hover the chart, hover the ▲ / ▼ / ● markers, toggle the OOS overlay preview.

## North Star

Wilson opens the page at 6am on a Sunday, has been watching his EUR_USD paper run for a week, and wants to know in under 5 seconds:

1. Is the strategy up or down, and by how much?
2. Is it tracking the backtest, or has it diverged?
3. Which trades drove the shape of the curve?
4. Anything to worry about right now (drawdown / open unrealised)?

Everything below is scored against those four questions.

## Design decisions & rationale

| # | Decision | Why |
|---|---|---|
| 1 | **Calendar-day x-axis** (`Mon 13 / Tue 14 / Wed 15…`) replaces `h0 → h167` | "167 hours" is meaningless for a long-running paper — Wilson can't map it to real events. Weekday + day-of-month lets him correlate a spike with "the ECB print on Thursday". Elapsed time (`6d 23h`) moves to the header sub-line where it still answers "how long has this been running." |
| 2 | **Explicit weekend gap band** (Fri 22:00 UTC → Sun 22:00 UTC, shaded, labelled `MARKET CLOSED`) | A flat curve during the weekend currently reads as "strategy did nothing." Shading it removes the ambiguity so the eye skips over the gap instead of interpreting it. Falls out of the same `_detectGaps` helper we can use for ingestion outages. |
| 3 | **8-tile summary strip above the chart** — live balance, realised, unrealised, max DD, trade count, win rate, best, worst | This is the "actual meaningful information" Wilson asked for. Mirrors the top-of-the-day summary a trader would want, sits directly above the shape it's summarising. |
| 4 | **Hover crosshair + rich tooltip** | The #1 complaint. Tooltip shows: timestamp (weekday + UTC HH:MM), elapsed since start, live equity, backtest OOS at same x (when overlay present), drift vs backtest, total P&L, realised, unrealised, trades-to-date (W / L). Answers every question Wilson listed for "point at any spot on the curve." |
| 5 | **Trade markers on a timeline strip below the plot** (▲ long / ▼ short at entry; coloured circle on the curve at exit) | Keeps the equity curve legible (markers don't fight the line) while still overlaying trades. Exit circle is coloured by realised P&L sign — green wins, red losses. Hover swaps the tooltip content for trade detail (side, entry / exit ts + price, pips, net P&L, exit reason, signal reason). Click scrolls to the row in the existing trades table. |
| 6 | **Backtest OOS overlay when present, persistent chip when absent** | Doesn't hide the whole chart when the overlay is missing (that's what today's design does implicitly by drawing nothing). Chip explains the specific schema gap in human terms and points at the tracking note. When the overlay lands, the chip auto-hides. |
| 7 | **Drawdown shaded region** (soft rose between running peak and current) | Visualises DD as *shape*, not just a number. Wilson's stated drawdown limit (2% daily / 10% max) becomes visually obvious rather than a stat he has to compute mentally. |
| 8 | **Range selector (24h / 7d / All)** with `1 / 2 / A` keyboard shortcuts | Long runs need to zoom into "what happened yesterday." Client-side filter over the equity[] array — no new endpoint needed. Wilson is a keyboard user; the shortcut hint sits under the chart. |
| 9 | **Snapshot reference line + label** at the y-value of `run.snapshot_balance` | The h0 anchor value now has a spatial referent. Anything above the dashed line = profit, below = loss. Existing chart draws this line but doesn't label it, so it reads as a random guide. |
| 10 | **Dark by default, matches existing `paper-run-detail.html`** — slate-900 canvas, slate-800 tiles, emerald / rose semantic colours, tabular-nums | Consistent with `PaperRunTradesTable`, `MetricsCard`, `paper-run-detail.html`. Zero new palette. |

## Data fields consumed

| Field | Endpoint | Used for |
|---|---|---|
| `snapshot_balance`, `approved_at`, `current_balance`, `status` | `GET /paper-runs/:id` | Snapshot line, header, run-start anchor, pulsing live-point |
| `[{timestamp, balance, unrealized_pnl}]` | `GET /paper-runs/:id/equity` | Live curve, hover tooltip, gap detection, drawdown series |
| `[{entry_timestamp, exit_timestamp, side, entry_price, exit_price, net_pnl, pnl_pct, exit_reason, signal_reason, origin}]` | `GET /paper-runs/:id/trades` | ▲ / ▼ / ● markers, trade tooltip, win-rate / best / worst tiles |
| `[{timestamp_relative_seconds, balance}]` | `GET /paper-runs/:id/backtest-overlay` | Dashed OOS line, drift-vs-BT row in tooltip |
| `stop_loss_pips`, `take_profit_pips` (present but not surfaced in the chart itself) | `GET /paper-runs/:id/risk_config` | (Left to the sibling risk panel.) |

The decision log (`/decisions`) is not consumed by this component — annotating `USER_SIGNAL_SKIPPED_STALE` bands on the plot is deferred to v0.6.2 as a nice-to-have.

## Pure helpers to extract (Vitest without jsdom)

All logic that can be pushed out of the React render function should be, so it locks under `portal/web/tests/unit/livePnlChart*.test.ts` following the `_scaleX` / `_buildRiskConfig` pattern:

| Helper | Signature | What it locks |
|---|---|---|
| `_scaleX`, `_scaleY` | *(unchanged)* | Axis maths |
| `_pickAxisTicks(startMs, endMs, rangeKey)` | → `{x, label, sublabel?, minor?}[]` | Range-adaptive tick spacing (24h = hourly, 7d = daily, All = daily+week markers) |
| `_detectGaps(equity, minGapMs = 6h)` | → `{startMs, endMs, isWeekend}[]` | Weekend + ingestion-outage bands |
| `_computeSummaryStrip(equity, trades, snapshotBalance)` | → `{realised, unrealised, maxDrawdown, peakEquity, tradeCount, openCount, winRate, bestTrade, worstTrade}` | All eight tiles — one memoised call |
| `_computeDrawdownSeries(equity)` | → `{timestampMs, drawdownPct}[]` | Peak-to-trough shading polygon |
| `_nearestSample(equity, targetMs)` | → `EquityPoint` | Hover crosshair snap (binary search — series is already timestamp-sorted) |
| `_formatElapsed(fromMs, toMs)` | → `"6d 23h 00m"` | Header sub-line + tooltip elapsed row |
| `_projectTrade(trade, scaleX, scaleY, equityAt)` | → `{entry: {x,y}, exit: {x,y}, sideColor, resultColor}` | Marker placement — separates geometry from JSX |
| `_interpolateOverlay(overlay, targetMs)` | → `number \| null` | "Backtest OOS at cursor" — linear interp between adjacent points |
| `_filterByRange(equity, trades, rangeKey, nowMs)` | → `{equity, trades, startMs, endMs}` | Range pill = pure filter, no new fetch |

Each helper takes primitive inputs (numbers, plain arrays) so the tests match the `_buildRiskConfig` shape from `deployPaperModalRiskConfig.test.ts` — no DOM, no React, no clock mocking beyond `Date.now()`.

## Implementation order

Ship in vertical slices so Wilson gets value at each merge:

1. **PR 1 · Axis + summary strip (P0).** Swap the x-axis for calendar days, add the 8-tile summary strip, add the snapshot-line label, add the weekend gap band. No hover yet. Delivers 3 of 5 of Wilson's complaints — the chart becomes readable.
2. **PR 2 · Hover crosshair + rich tooltip (P0).** Extract `_nearestSample` + `_formatElapsed`; wire pointer events on a single hit-rect covering the plot. This is complaint #1 fixed.
3. **PR 3 · Trade markers + trade tooltip (P1).** Consume `/paper-runs/:id/trades` (already fetched by the parent page — pass it down as a prop). Add ▲ / ▼ / ● markers with hover swap. Click handler scrolls to the trade row via a `data-trade-id` hook already on `PaperRunTradesTable` rows.
4. **PR 4 · Range selector + keyboard shortcuts (P1).** `_filterByRange` + three pills + `1/2/A` global handler while the chart is in the viewport.
5. **PR 5 · Drawdown shaded region (P2).** `_computeDrawdownSeries` + a second SVG path with rose fill and 0.10 opacity.
6. **PR 6 · Overlay wiring (P3, blocked on backend).** Consume the overlay array; toggle the "unavailable" chip based on `backtestOverlay.source`. Trivial once the backend gap is closed.

Steps 1–3 are the minimum viable redesign; if scope compresses, ship those and defer the rest behind Wilson's next round of feedback.

## Backend gaps this design surfaces

1. **`ValidationFold.equity_series` is unpopulated (v0.4.0 schema gap).** The overlay endpoint returns `source: "unavailable"` for virtually every genome. Design accommodates the absence gracefully, but the overlay isn't *actually useful* until the evolution runner starts writing per-fold OOS equity. Tracked alongside `project_swap_spread_todo.md`; suggested addition to that follow-up: wire the write path in the WF validator and backfill on-demand for recently promoted genomes.
2. **Running peak / drawdown is recomputed client-side on every render.** Fine at 6–8k samples (a week of M1), painful at a year. Nice-to-have: extend `/equity` to include a rolling `peak_balance` column so `_computeDrawdownSeries` becomes a simple map.
3. **Ingestion outages are inferred from gaps in `equity[]`.** Works but conflates "runner was down" with "signal-tick loop was late." A structured `equity_gap` reason (from the decision log) would let the shaded band be labelled `RUNNER RESTARTED` vs `MARKET CLOSED` vs `INGESTION LAG`.
4. **`trades[].origin` is `"live" | "backtest"` today.** Backtest-origin rows are only emitted when the runner ingests the source OOS trades — same gap as #1. Design assumes both are present; when only live is present the ▲ / ▼ markers still render, we just don't overlay backtest-side markers.

None of the four are blocking for PRs 1–5. #1 is the blocker for PR 6.

## Files that will change in the React port

- `portal/web/src/components/LivePnLChart.tsx` — rewritten. Consumes `trades` and `backtestOverlay` in addition to `liveSeries`.
- `portal/web/src/pages/PaperRunDetail.tsx` — passes `trades` down to `<LivePnLChart>` (already fetched for the table below), drops the "Live vs backtest equity" duplicate legend / status caption (they move inside the component).
- `portal/web/src/utils/time.ts` — add `formatElapsed` (currently only `formatUTC` lives there).
- `portal/web/tests/unit/` — new files: `livePnlChartHelpers.test.ts`, `livePnlChartSummary.test.ts`, `livePnlChartGaps.test.ts`. No new dev dependencies (Vitest + node, no jsdom).

No third-party chart library required. `d3-scale` is *not* needed — the maths in the helpers above is either linear or already covered by the existing `_scaleX` / `_scaleY`. If a future iteration wants log-scale y-axis or auto tick generation, revisit then.
