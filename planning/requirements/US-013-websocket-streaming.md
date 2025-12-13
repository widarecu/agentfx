# US-013: Real-Time WebSocket Market Data Streaming

**Version:** v0.2.0 - Parameter Optimizer Agent
**Priority:** P0 - Must Have
**Status:** TODO

## Background

This requirement consolidates WebSocket functionality that was deferred from v0.1.0:
- From [US-010](US-010-dashboard-integration.md): Real-time chart updates
- From [US-011](US-011-market-data-error-handling.md): WebSocket connection with graceful fallback

---

## User Story

**As a** logged-in user viewing Market Data
**I want** to see real-time price updates via WebSocket
**So that** I can monitor live market movements without manual refresh

## Given-When-Then

### Scenario 1: WebSocket Connection
**GIVEN** the user is on the Market Data page
**WHEN** the page loads
**THEN** a WebSocket connection is established to receive real-time updates

### Scenario 2: Real-Time Candle Updates
**GIVEN** a WebSocket connection is active
**WHEN** new candle data arrives
**THEN** the chart/table updates automatically without user action

### Scenario 3: WebSocket Unavailable (Graceful Fallback)
**GIVEN** the WebSocket connection fails or is unavailable
**WHEN** the user views Market Data page
**THEN**:
1. A "Live updates unavailable" indicator is shown
2. REST API fallback continues to work
3. Manual refresh button remains functional

### Scenario 4: Reconnection
**GIVEN** a WebSocket connection was lost
**WHEN** the connection becomes available again
**THEN** the connection is automatically re-established

## Acceptance Criteria

### WebSocket Infrastructure
- [ ] WebSocket endpoint `/ws/market-data` implemented
- [ ] Connection authentication via JWT token
- [ ] Subscription mechanism for instruments/timeframes
- [ ] Heartbeat/ping-pong for connection health

### Frontend Integration
- [ ] WebSocket service connects on Market Data page mount
- [ ] Connection status indicator (Connected/Disconnected)
- [ ] Automatic reconnection with exponential backoff
- [ ] Clean disconnection on page unmount

### Real-Time Updates
- [ ] New candles append to chart/table automatically
- [ ] Current candle updates in place as price changes
- [ ] Subscription changes when instrument/timeframe changes

### Graceful Fallback
- [ ] REST API continues to work when WebSocket fails
- [ ] Clear indicator when live updates unavailable
- [ ] No blocking errors from WebSocket failures

## API Contract

### WebSocket Endpoint
```
WS /api/v1/ws/market-data
```

### Authentication
```json
// Initial message after connection
{"type": "auth", "token": "JWT_TOKEN"}
```

### Subscribe
```json
{"type": "subscribe", "instrument": "EUR_USD", "timeframe": "M1"}
```

### Unsubscribe
```json
{"type": "unsubscribe", "instrument": "EUR_USD", "timeframe": "M1"}
```

### Incoming Candle Update
```json
{
  "type": "candle",
  "instrument": "EUR_USD",
  "timeframe": "M1",
  "candle": {
    "time": "2025-01-15T10:01:00Z",
    "open": 1.0852,
    "high": 1.0854,
    "low": 1.0851,
    "close": 1.0853,
    "volume": 567,
    "complete": false
  }
}
```

## Files to Create/Modify

### Backend
- `src/agentfx/api/routers/websocket.py` - WebSocket router
- `src/agentfx/api/services/market_data_stream.py` - Streaming service
- `src/agentfx/api/core/websocket.py` - WebSocket manager

### Frontend
- `portal/web/src/services/marketDataWebSocket.ts` - WebSocket client
- `portal/web/src/hooks/useMarketDataStream.ts` - React hook for streaming
- `portal/web/src/pages/MarketData.tsx` - Integrate WebSocket

### Tests
- `tests/unit/market_data/test_websocket.py` - Unit tests
- `tests/integration/market_data/test_websocket_connection.py` - Integration tests
- `tests/e2e/market_data_streaming.spec.ts` - E2E tests

## Definition of Done

- [ ] WebSocket endpoint implemented and tested
- [ ] Frontend connects and receives real-time updates
- [ ] Connection status indicator visible
- [ ] Graceful fallback when WebSocket unavailable
- [ ] Automatic reconnection works
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Manual verification passes

## Dependencies

- v0.1.0 complete (REST API foundation)
- OANDA streaming API access (for real-time prices)
