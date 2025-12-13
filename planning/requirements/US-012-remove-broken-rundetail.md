# US-012: Remove Broken RunDetail Navigation

**Version:** v0.1.0 - Foundation
**Priority:** P0 - Must Have
**Status:** DONE
**Completed:** 2025-12-13

---

## User Story

**As a** user navigating the portal
**I want** all navigation links to work correctly
**So that** I don't encounter broken pages or confusing errors

## Given-When-Then

**GIVEN** the RunDetail page relies on non-existent API endpoints
**WHEN** a user navigates to /runs/:runId
**THEN** they should see a 404 page (route removed from router)

## Problem Statement

The RunDetail page (`portal/web/src/pages/RunDetail.tsx`) called these endpoints:
- `/api/runs/{runId}` - EXISTS but returns minimal data
- `/api/pairs/{pair}/candles` - DOES NOT EXIST
- `/api/runs/{runId}/signals` - DOES NOT EXIST

This caused the page to show errors when accessed.

## Solution Implemented

**Option A: Remove Route** was implemented.

The RunDetail route was removed from the router. It will be re-added in v0.2.0 when the backend endpoints are implemented.

## Acceptance Criteria

- [x] RunDetail route removed from router.tsx
- [x] No navigation links point to /runs/:runId
- [x] Direct URL access to /runs/:runId shows 404
- [x] Dashboard does not link to individual runs
- [x] RunDetail.tsx component preserved for v0.2.0

## Verification Commands

```bash
# Start frontend
cd portal/web && npm run dev

# Manual verification:
# 1. Navigate to dashboard
# 2. Verify no links to /runs/xxx pages
# 3. Manually enter URL /runs/123
# 4. Verify 404 or redirect to dashboard
```

## Files Modified

- `portal/web/src/router.tsx` - Removed RunDetail route

## Definition of Done

- [x] /runs/:runId route removed from router
- [x] No navigation links to runs
- [x] No JavaScript errors when browsing portal
- [x] Clean user experience with no broken pages
- [x] RunDetail.tsx preserved for v0.2.0 implementation
