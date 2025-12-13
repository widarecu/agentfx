# US-011: Market Data Page with REST API

**Version:** v0.1.0 - Foundation
**Priority:** P0 - Must Have
**Status:** DONE
**Completed:** 2025-12-13

## Scope Change Notes

The original requirement focused on graceful error handling when WebSocket fails. The scope was revised:

1. **WebSocket removed from v0.1.0** - No WebSocket means no WebSocket errors to handle
2. **REST-only architecture** - Market Data page uses REST APIs exclusively
3. **WebSocket streaming** deferred to v0.2.0 (see [US-013](US-013-websocket-streaming.md))

---

## User Story

**As a** logged-in user
**I want** to view available instruments and their candle data on the Market Data page
**So that** I can analyze historical price data

## Given-When-Then

### Scenario 1: Page Load
**GIVEN** a user is logged in
**WHEN** they navigate to the Market Data page
**THEN** the page displays:
1. Instruments dropdown populated from API
2. Timeframe selector
3. Refresh button to reload data

### Scenario 2: View Candle Data
**GIVEN** the Market Data page is loaded
**WHEN** the user selects an instrument and timeframe
**THEN** the candle data table displays historical OHLCV data

### Scenario 3: REST API Unavailable
**GIVEN** the backend REST API is unavailable
**WHEN** the user navigates to Market Data page
**THEN** the page shows a clear error message with retry option

## Acceptance Criteria

### Instruments Dropdown
- [x] Dropdown populated from `/market-data/instruments` endpoint
- [x] All 6 supported FX pairs displayed (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CHF, USD_CAD)
- [x] Loading state while fetching instruments
- [x] Error state if instrument fetch fails

### Timeframe Selector
- [x] Timeframe dropdown with options (M1, M5, M15, H1, D1)
- [x] Default timeframe selected

### Candle Data Table
- [x] Table displays candle data from `/market-data/candles` endpoint
- [x] Columns: Timestamp, Open, High, Low, Close, Volume
- [x] Empty state when no data available
- [x] Loading state while fetching candles
- [x] Error state if candle fetch fails

### Refresh Functionality
- [x] Refresh button to reload candle data
- [x] Data reloads on instrument change
- [x] Data reloads on timeframe change

## API Dependencies

### GET /market-data/instruments
Returns available instruments.
```json
[
  {"symbol": "EUR_USD", "name": "EUR/USD", "type": "forex"},
  {"symbol": "GBP_USD", "name": "GBP/USD", "type": "forex"}
]
```

### GET /market-data/candles
Returns historical candles.
```
GET /market-data/candles?instrument=EUR_USD&timeframe=M1&limit=100
```
```json
{
  "instrument": "EUR_USD",
  "timeframe": "M1",
  "candles": [
    {
      "time": "2025-01-15T10:00:00Z",
      "open": 1.0850,
      "high": 1.0855,
      "low": 1.0848,
      "close": 1.0852,
      "volume": 1234
    }
  ]
}
```

## Verification Commands

```bash
# Run E2E tests
./run_tests.sh e2e

# Manual verification
# 1. Login at http://localhost:5173/login
# 2. Navigate to Market Data page
# 3. Verify instruments dropdown is populated
# 4. Select instrument and timeframe
# 5. Verify candle data table displays
# 6. Click Refresh - verify data reloads
```

## Files Involved

### Created
- `portal/web/src/pages/MarketData.tsx` - Market Data page
- `portal/web/src/components/InstrumentSelector.tsx` - Instrument dropdown
- `portal/web/src/components/CandlestickChart.tsx` - Candle display
- `portal/web/src/services/marketData.ts` - REST API client

### E2E Tests
- `tests/e2e/market_data.spec.ts` - Market Data E2E tests

## Definition of Done

- [x] Instruments dropdown populated from API
- [x] All 6 FX pairs displayed in dropdown
- [x] Timeframe selector functional
- [x] Candle data table displays historical data
- [x] Loading states for all async operations
- [x] Error states for failed API calls
- [x] Refresh button functional
- [x] E2E tests pass

## Deferred to v0.2.0

The following items were moved to v0.2.0:
- WebSocket real-time streaming (see [US-013](US-013-websocket-streaming.md))
- Live price updates
- Connection status indicator
