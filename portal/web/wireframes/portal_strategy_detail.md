# Portal Wireframe — Strategy Detail (Run View)

## Purpose
Deep-dive into a specific run: candles + overlays, trades with tooltips (MAE/MFE), positions, and recent activity.

## Layout (ASCII)

[ Header: MA Crossover — AUDUSD — paper | Back | last-updated 01:02:03Z | Refresh 60s ]

[ Controls ]  TF: [ M1 | M5* | M15 | H1 ]   Range: [ 1d* | 7d | 30d ]   Overlays: [x] MA  [x] RSI  [ ] Donchian   [ ] Decisions

[ Main Chart: Candlesticks ]
- Price Y-axis (right). Crosshair + tooltip (OHLC).
- Position band shading: green (long), red (short).
- Entry markers: ▲ (green=long, red=short) at price on entry bar.
- Exit markers: ● (filled circle), line connecting entry→exit.
- Overlay lines: MA fast/slow; RSI subpanel below with 30/70 bands; Donchian bands if toggled.

[ Tooltip on exit marker ]
- Side, Size
- Entry ts/price, Exit ts/price
- Gross PnL, Net PnL, Return %
- Duration, MAE (pips, %, ATRx), MFE (pips, %, ATRx)
- Provenance/Trace ID

[ Trades Table ]  (sorted by exit_ts desc)
| Trade ID          | Side  | Qty | Entry ts          | Entry | Exit ts           | Exit  | Net PnL | Return % | Dur. | MAE pips | MFE pips |
|-------------------|-------|-----|-------------------|-------|-------------------|-------|---------|----------|------|----------|----------|
| t_20251117_001    | long  | 1.0 | 2025-11-17T02:10Z | 0.6540| 2025-11-17T03:05Z | 0.6544| 38.5    | 0.0612   | 55m  | -6.0     | 12.0     |

[ Recent Orders / Executions ]
| Time               | Side  | Qty | Price   | Status  | Latency | Provenance |
|--------------------|-------|-----|---------|---------|---------|------------|
| 2025-11-17T02:10Z  | long  | 1.0 | 0.65402 | filled  | 120 ms  | dec_abc123 |

## Interactions
- Toggle overlays; chart updates without reflow
- Clicking a trade row zooms chart to trade window
- Hover on markers shows tooltips; RSI subpanel crosshair syncs
- Auto-refresh updates data without breaking viewport

## Data
- Candles: `GET /api/pairs/{pair}/candles?tf&from&to&limit`
- Trades: `GET /api/runs/{run_id}/trades?from&to&limit&cursor`
- Orders: `GET /api/runs/{run_id}/orders?from&to&limit&cursor`
- Signals: `GET /api/runs/{run_id}/signals?from&to&limit&series`
- Position bands: `GET /api/runs/{run_id}/position_bands?from&to`

## Empty/Errors
- No data in range → non-blocking notice; keep controls active
- Errors: toast + retry; preserve last good render

## Performance
- Respect 10k downsampling cap on candles/signals
- Lazy-load trades/orders pages (limit=100) with cursor
