# US-015: Strategy Visualization with Candlestick Chart

**Version:** v0.2.0 - Bayesian Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** a trial "trial_optjob_20250115_abc123_042" has trade signals stored
  **AND** the user is authenticated (VIEWER, TRADER, or ADMIN role)
**WHEN** the user clicks "View Chart" on a trial from the optimization detail page
**THEN** they see:
1. Candlestick chart with OHLC price data for the backtest period
2. Entry points marked as green triangles (long) / red triangles (short)
3. Exit points marked as circles (green for profit, red for loss)
4. Technical indicator overlays appropriate to the strategy:
   - MA strategy: fast MA line (blue) + slow MA line (orange)
   - RSI strategy: RSI panel below chart with 30/70 threshold lines
   - Donchian: upper/lower channel bands (shaded)
5. Interactive zoom/pan controls
6. Crosshair with price/time values on hover

## Acceptance Criteria

- [ ] Chart renders within 2 seconds for 5 years of daily/hourly data
- [ ] Candlestick OHLC display with proper coloring (green up, red down)
- [ ] Trade signals (entry/exit) align precisely with candle timestamps
- [ ] Indicators match the strategy parameters used in the trial
- [ ] Zoom/pan functionality works smoothly
- [ ] Crosshair shows exact price and timestamp on hover
- [ ] Responsive layout for different screen sizes
- [ ] Metrics summary panel shows trial performance alongside chart
- [ ] Parameter configuration displayed for reference
- [ ] Uses existing `/api/v1/market-data/candles` endpoint for price data

## API Contract

### Get Trial Signals (for chart markers)
```
GET /api/v1/optimization-jobs/{job_id}/trials/{trial_id}/signals
Authorization: Bearer <JWT_TOKEN>

Response:
{
  "trial_id": "trial_optjob_20250115_abc123_042",
  "strategy": "ma",
  "params": {"fast": 10, "slow": 50},
  "pair": "EUR_USD",
  "data_start": "2020-01-01T00:00:00Z",
  "data_end": "2025-01-01T00:00:00Z",
  "signals": [
    {
      "timestamp": "2020-01-15T10:00:00Z",
      "signal": 1,
      "signal_type": "entry_long",
      "price": 1.1234,
      "indicator_values": {
        "fast_ma": 1.1220,
        "slow_ma": 1.1200
      }
    },
    {
      "timestamp": "2020-01-20T14:30:00Z",
      "signal": 0,
      "signal_type": "exit_long",
      "price": 1.1345,
      "pnl": 0.0111,
      "indicator_values": {
        "fast_ma": 1.1330,
        "slow_ma": 1.1340
      }
    },
    {
      "timestamp": "2020-02-05T09:15:00Z",
      "signal": -1,
      "signal_type": "entry_short",
      "price": 1.1050,
      "indicator_values": {
        "fast_ma": 1.1070,
        "slow_ma": 1.1090
      }
    }
  ],
  "total_signals": 342
}
```

### Get Candle Data (REUSE EXISTING ENDPOINT)
```
GET /api/v1/market-data/candles?instrument=EUR_USD&timeframe=H1&from=2020-01-01T00:00:00Z&to=2025-01-01T00:00:00Z
Authorization: Bearer <JWT_TOKEN>

Response: (existing format from v0.1.0)
{
  "candles": [
    {
      "timestamp": "2020-01-01T00:00:00Z",
      "open": 1.1210,
      "high": 1.1235,
      "low": 1.1195,
      "close": 1.1220,
      "volume": 1500
    }
  ],
  "total": 43800
}
```

## Signal Types

| signal_type | Description | Marker |
|-------------|-------------|--------|
| `entry_long` | Open long position | Green triangle up |
| `exit_long` | Close long position | Green circle (profit) / Red circle (loss) |
| `entry_short` | Open short position | Red triangle down |
| `exit_short` | Close short position | Green circle (profit) / Red circle (loss) |

## Indicator Overlays by Strategy

### MA Crossover
- Fast MA line (configurable period from params.fast)
- Slow MA line (configurable period from params.slow)
- Crossover points highlighted

### RSI Mean Reversion
- RSI indicator in separate panel below price chart
- 30/70 threshold horizontal lines
- Oversold/overbought zones shaded

### Donchian Breakout
- Upper channel band (highest high over window)
- Lower channel band (lowest low over window)
- Channel area shaded with transparency

## Authorization Matrix

| Action | Minimum Role |
|--------|-------------|
| View trial chart | VIEWER |
| View trial signals | VIEWER |

## Verification Commands

```bash
# Frontend component tests
cd portal/web && npm run test -- --grep "StrategyChart"

# E2E test - visualization rendering
cd portal/web && npx playwright test tests/e2e/visualization.spec.ts
```

## UI Components

### Trial Visualization Page (`/optimization/:jobId/trials/:trialId`)
```
+------------------------------------------------------------------------+
| Trial #42 - MA Crossover (EUR_USD)                      [Back to Job]  |
| Parameters: fast=10, slow=50                                            |
+------------------------------------------------------------------------+

+----------------------------------+  +----------------------------------+
|        Performance Metrics       |  |     Parameter Configuration      |
|----------------------------------|  |----------------------------------|
| Sharpe Ratio:    1.85            |  | Strategy: MA Crossover           |
| Max Drawdown:    6.0%            |  | Fast Period: 10                  |
| Hit Rate:        54%             |  | Slow Period: 50                  |
| Trade Count:     342             |  | Data Range: 5 years              |
| Total Return:    45%             |  |                                  |
| Gate B:          PASS [✓]        |  |                                  |
+----------------------------------+  +----------------------------------+

+------------------------------------------------------------------------+
|                      Strategy Chart                     [Zoom] [Reset] |
+------------------------------------------------------------------------+
|                                                                        |
|  1.15 |                    /\                                          |
|       |                   /  \         ▲ Entry Long                    |
|  1.12 |      /\    ▲    /    \    ●   ○ Exit (profit)                  |
|       |     /  \  ●    /      \  /    ▼ Entry Short                    |
|  1.09 |    /    \/    /        \/     ● Exit (loss)                    |
|       |   /          /                                                 |
|  1.06 |  /   ▼      /                  === Fast MA (blue)              |
|       | /     \    /                   === Slow MA (orange)            |
|  1.03 +-------------------------------------------------> Time         |
|       Jan 2020    Jul 2020    Jan 2021    Jul 2021    Jan 2022        |
|                                                                        |
|  Crosshair: Jan 15, 2020 10:00 | Price: 1.1234 | Fast MA: 1.1220      |
+------------------------------------------------------------------------+
|  [◀ Prev Trade]                                        [Next Trade ▶]  |
+------------------------------------------------------------------------+
```

## Files Involved

### Create
- `portal/web/src/pages/TrialDetail.tsx` - Trial visualization page
- `portal/web/src/components/StrategyChart.tsx` - Candlestick chart with indicators
- `portal/web/src/components/TradeMarkers.tsx` - Entry/exit signal markers
- `portal/web/src/components/IndicatorOverlay.tsx` - Technical indicator overlays
- `portal/web/src/hooks/useChartData.ts` - Hook for fetching and combining chart data
- `tests/e2e/visualization.spec.ts` - E2E tests

### Modify
- `portal/web/src/router.tsx` - Add trial detail route
- `portal/web/src/services/optimizationApi.ts` - Add getTrialSignals method

## Dependencies

- US-001, US-002: Trial must exist with signals stored
- Existing `/api/v1/market-data/candles` endpoint (v0.1.0)
- Charting library (lightweight-charts or similar)

## Technical Notes

### Chart Library Selection
Recommend `lightweight-charts` by TradingView for:
- High performance with large datasets (5+ years of candles)
- Built-in candlestick support
- Easy marker/annotation API
- Small bundle size (~45KB gzipped)

### Data Aggregation for Performance
- For 5 years of M1 data (~2.6M candles), use server-side aggregation
- Default to H1 timeframe for chart display
- Allow timeframe switching (M1, M5, M15, H1, H4, D)

### Indicator Calculation
- Indicators can be calculated client-side from candle data
- Or pre-calculated server-side and included in signals response
- For MVP, calculate client-side using simple moving average formulas

### Memory Optimization
- Use virtualization for large datasets
- Lazy-load candles as user pans/zooms
- Cache candle data in React Query with long stale time

## Notes

- Chart should be the primary visual focus of the page
- Performance is critical - must render smoothly with 5 years of data
- Entry/exit markers must precisely align with candle timestamps
- Indicator lines should be toggleable (show/hide)
- Consider adding trade list panel showing all trades with jump-to functionality
- "Prev Trade" / "Next Trade" buttons to navigate between signals
