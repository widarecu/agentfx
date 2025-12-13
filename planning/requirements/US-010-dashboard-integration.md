# US-010: Dashboard with User and System Status

**Version:** v0.1.0 - Foundation
**Priority:** P0 - Must Have
**Status:** DONE
**Completed:** 2025-12-13

## Scope Change Notes

The original requirement included candlestick chart and WebSocket real-time updates on the Dashboard. This scope was revised:

1. **Dashboard** displays user info and system status (this requirement)
2. **Market Data page** displays instruments and candle data (separate page)
3. **WebSocket streaming** deferred to v0.2.0 (see [US-013](US-013-websocket-streaming.md))

---

## User Story

**As a** logged-in user
**I want** to see my user information and system status on the dashboard
**So that** I can verify my session and monitor system health

## Given-When-Then

### Scenario 1: Dashboard Load
**GIVEN** a user has successfully logged in
**WHEN** they navigate to the dashboard (/)
**THEN** the dashboard displays:
1. User's full name, email, and role
2. Account status (active/inactive)
3. System operational status (backend connected)
4. Market data service status (available instruments count)

### Scenario 2: Loading States
**GIVEN** the dashboard is loading data
**WHEN** API calls are in progress
**THEN** loading indicators are displayed

### Scenario 3: Error Handling
**GIVEN** an API call fails
**WHEN** the user views the dashboard
**THEN** appropriate error messages are displayed

## Acceptance Criteria

### User Info Card
- [x] Dashboard calls `/auth/me` endpoint on load
- [x] User information card shows: name, email, role
- [x] Account status (active/inactive) displayed
- [x] Loading state while fetching user data
- [x] Error state if user fetch fails

### System Status Card
- [x] Dashboard calls `/market-data/status` endpoint on load
- [x] System status card shows: API connection status
- [x] Instruments count displayed
- [x] Loading state while fetching status
- [x] Error state if status fetch fails

### No Placeholder Content
- [x] No "Coming soon" text remains
- [x] No hardcoded mock data
- [x] All data fetched from real API endpoints

## API Dependencies

### GET /auth/me
Returns current user information.
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "full_name": "John Doe",
  "is_active": true,
  "role": "trader"
}
```

### GET /market-data/status
Returns market data service status.
```json
[
  {
    "exchange": "oanda",
    "instrument": "EUR_USD",
    "timeframe": "M1",
    "status": "active",
    "last_updated": "2025-01-15T10:00:00Z"
  }
]
```

## Verification Commands

```bash
# Run E2E tests
./run_tests.sh e2e

# Manual verification
# 1. Login at http://localhost:5173/login
# 2. Verify dashboard shows user name, email, and role
# 3. Verify system status shows connection state
# 4. Verify no "Coming soon" text anywhere
```

## Files Involved

### Created
- `portal/web/src/components/UserInfoCard.tsx` - User info component
- `portal/web/src/components/SystemStatusCard.tsx` - System status component

### Modified
- `portal/web/src/pages/Dashboard.tsx` - Integrated new components

## Definition of Done

- [x] Dashboard fetches real user data on mount
- [x] Dashboard fetches real system status on mount
- [x] UserInfoCard displays name, email, role
- [x] SystemStatusCard displays API status and instrument count
- [x] Loading spinners shown during fetch
- [x] Error messages shown on failure
- [x] No "Coming soon" or placeholder text
- [x] E2E tests pass

## Deferred to v0.2.0

The following items from the original requirement were moved to v0.2.0:
- Candlestick chart on dashboard (chart is on MarketData page instead)
- WebSocket real-time updates (see [US-013](US-013-websocket-streaming.md))
- Instrument selector on dashboard (selector is on MarketData page)
