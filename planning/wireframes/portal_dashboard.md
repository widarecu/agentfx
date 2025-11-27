# Portal Wireframe — Dashboard (Runs List)

## Purpose
Read-only overview of strategy runs with key metrics and quick navigation to detail.

## Layout (ASCII)

[ Header: AgentFX Portal | env filter [dev|paper] (paper default) | search | last-updated 01:02:03Z ]

| Strategy (name)          | Pair   | Env   | Status   | PnL (Daily) | PnL (Cum) | Sharpe | MDD    | Hit% | Turnover | Updated             | Actions |
|--------------------------|--------|-------|----------|-------------|-----------|--------|--------|------|----------|---------------------|---------|
| MA Crossover (AUDUSD)    | AUDUSD | paper | enabled  |   -12.34    |  -532.10  | -0.42  | -0.31  | 0.47 |    320   | 2025-11-18T01:02:03Z | [View]  |
| RSI MeanRev (EURUSD)     | EURUSD | paper | enabled  |    +4.20    |  -120.50  | -0.15  | -0.28  | 0.49 |    280   | 2025-11-18T00:55:11Z | [View]  |

- Sort: by PnL (Cum) desc; toggle on column headers.
- Filters: env, status, pair; search by name/pair.
- Pagination: limit 50; next/prev.
- Auto-refresh: 60s; keep sort/filter; show spinner + subtle row shimmer.

## Data
- Source: `GET /api/runs?env=paper&pair=&strategy=&status=&limit=&cursor=`
- Columns map 1:1 to runs response. PnL currency shown per-row.

## Empty/Errors
- Empty: "No runs found — adjust filters or check environment."
- Error: inline toast; preserve prior results if available.

## Navigation
- [View] → `/runs/{run_id}` (Strategy Detail)
