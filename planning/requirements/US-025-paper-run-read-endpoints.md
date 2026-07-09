# US-025: Paper Run Trades / Decisions / Equity Read Endpoints

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P0 — Must Have (surfaces what US-024 writes)
**Status:** TODO

## Genesis

Once US-024 lands, the runner writes Trade rows, DecisionLog rows, and PaperEquitySnapshot rows every signal tick. The `/paper/:id` page already has placeholders for the Trades table and Decision log stream from the PR-J UI work in v0.6.0. Three thin RBAC-scoped `GET` endpoints connect them.

## Given-When-Then

**GIVEN** a `runs` row with `environment=PAPER` and `id=<run_id>`
  **AND** an authenticated request from either the owning TRADER user OR any ADMIN
**WHEN** the client calls one of the three endpoints below
**THEN** the server returns the RBAC-scoped payload with 200; returns 403 when the caller is not the owner and not ADMIN; returns 404 when the run does not exist.

### Endpoint 1 — Trades

`GET /api/v1/paper-runs/{run_id}/trades?limit=100&offset=0`

**Response (200)**:
```json
{
  "items": [
    {
      "id": "trade_...",
      "entry_timestamp": "2026-07-10T14:23:00Z",
      "exit_timestamp": "2026-07-10T14:47:00Z",
      "side": "long",
      "quantity": 10000,
      "entry_price": 1.08234,
      "exit_price": 1.08312,
      "pnl": 7.80,
      "pnl_pct": 0.072,
      "origin": "live",
      "signal_reason": "SMA_20 > SMA_50"
    }
  ],
  "total": 42,
  "limit": 100,
  "offset": 0
}
```

Ordered newest-first by `COALESCE(exit_timestamp, entry_timestamp)`. Open trades (no `exit_timestamp`) sort ahead of closed ones. `origin` is always `live` in v0.6.1 — the field is reserved for the v0.7.0 backtest-vs-live overlay work.

### Endpoint 2 — Decisions

`GET /api/v1/paper-runs/{run_id}/decisions?limit=100&offset=0&since=<iso_timestamp>`

**Response (200)**:
```json
{
  "items": [
    {
      "id": "dl_...",
      "timestamp": "2026-07-10T14:23:00Z",
      "action_type": "USER_ORDER_PLACED",
      "actor_user_id": 42,
      "reason": "SMA_20 crossed above SMA_50",
      "metadata_json": {
        "instrument": "EUR_USD",
        "signed_units": 10000,
        "fill_price": 1.08234,
        "trade_id": "trade_..."
      }
    }
  ],
  "total": 128,
  "limit": 100,
  "offset": 0
}
```

Ordered newest-first. Filtered to the action types relevant to the paper flow: `USER_DEPLOY_PAPER`, `USER_APPROVE_GENOME`, `USER_REJECT_GENOME`, `USER_KILL_PAPER`, `USER_RESUME_PAPER`, `USER_SIGNAL_EVALUATED`, `USER_SIGNAL_SKIPPED_STALE`, `USER_ORDER_PLACED`, `USER_POSITION_CLOSED`, `USER_GATE_REFUSED`. `?since=` is optional and enables incremental polling from the frontend (return only entries newer than the provided timestamp).

### Endpoint 3 — Equity

`GET /api/v1/paper-runs/{run_id}/equity?from=<iso>&to=<iso>&limit=5000`

**Response (200)**:
```json
{
  "items": [
    {
      "timestamp": "2026-07-10T14:23:00Z",
      "balance": 10173.42,
      "unrealized_pnl": 12.30
    }
  ],
  "run_started_at": "2026-07-10T14:00:00Z",
  "snapshot_balance": 10000.00
}
```

Ordered oldest-first (chart-friendly). `run_started_at` + `snapshot_balance` are returned alongside so `LivePnLChart` can anchor the curve without a second request. Default window = all samples; `?from=` / `?to=` narrow to the LivePnLChart's visible range.

## Acceptance Criteria

### Router — `src/agentfx/api/routers/paper.py`

- [ ] `GET /api/v1/paper-runs/{run_id}/trades` — RBAC-scoped via `scope_query_to_user`, paginated, filters origin to the paper run's `run_id`.
- [ ] `GET /api/v1/paper-runs/{run_id}/decisions` — RBAC-scoped, `?since=` support, action-type filter.
- [ ] `GET /api/v1/paper-runs/{run_id}/equity` — RBAC-scoped, optional `?from` / `?to`.
- [ ] All three return `404` for missing runs, `403` for cross-user access (regress the existing RBAC isolation suite).

### Service layer — `src/agentfx/api/services/paper.py`

- [ ] `list_trades_for_run(db, run_id, limit, offset) -> tuple[list[Trade], int]` — queries `Trade` filtered by `run_id`, ordered by `COALESCE(exit_timestamp, entry_timestamp) DESC`.
- [ ] `list_decisions_for_run(db, run_id, limit, offset, since=None) -> tuple[list[DecisionLog], int]` — filters `action_type IN (…)` union of the ten types above.
- [ ] `list_equity_for_run(db, run_id, from_ts=None, to_ts=None, limit=5000) -> list[PaperEquitySnapshot]` — see US-026 for the table.

### Schemas — `src/agentfx/api/schemas/paper.py`

- [ ] `TradePublic`, `TradeListResponse` Pydantic models mirroring the payload shapes above.
- [ ] `DecisionLogPublic`, `DecisionLogListResponse`.
- [ ] `EquitySnapshotPublic`, `EquityListResponse`.

### Frontend wiring — `portal/web/src/services/paperApi.ts`

- [ ] `getTradesForRun(runId, limit, offset): Promise<TradeListResponse>` — new client method.
- [ ] `getDecisionsForRun(runId, since?): Promise<DecisionLogListResponse>` — supports incremental polling.
- [ ] `getEquityForRun(runId, fromTs?, toTs?): Promise<EquityListResponse>`.

### Frontend consumption — `portal/web/src/pages/PaperRunDetail.tsx`

- [ ] Trades placeholder replaced with real `<TradesTable>` populated from `getTradesForRun(runId, 100, 0)`; polls every 5s while `status === 'RUNNING'`.
- [ ] Decision log placeholder replaced with a scrolling list populated from `getDecisionsForRun(runId, lastSeenAt)`; polls every 5s while `status === 'RUNNING'`. Auto-scrolls to newest entry on arrival unless the user has scrolled up.
- [ ] `LivePnLChart` seeded on mount from `getEquityForRun(runId)` — replaces the "tab-open-time-only" accumulation with the full persisted history.

### Tests

- [ ] `tests/integration/test_paper_read_endpoints.py` (new) — user A cannot fetch user B's trades / decisions / equity (all three return 403). ADMIN can with `?scope=all` semantics (though these endpoints are already run-scoped so the check is just "not owner OR ADMIN").
- [ ] `tests/unit/api/services/test_paper_list_helpers.py` (new) — pagination, ordering, since-filter.
- [ ] Vitest `portal/web/tests/components/TradesTable.test.tsx` — updated to include the origin + signal_reason columns.
- [ ] Vitest `portal/web/tests/pages/PaperRunDetail.test.tsx` — mounts, mocks the three endpoints, asserts Trades / Decision log / LivePnLChart populate.
- [ ] E2E `portal/web/tests/e2e/paper-trading.spec.ts` — after 90 seconds of runtime on a fake-OANDA-backed stack, the Trades table has ≥ 1 row and the Decision log has ≥ 1 `USER_ORDER_PLACED` entry.

## Files Involved

- `src/agentfx/api/routers/paper.py` — three new GET endpoints
- `src/agentfx/api/services/paper.py` — three new list helpers
- `src/agentfx/api/schemas/paper.py` — three response schemas
- `portal/web/src/services/paperApi.ts` — three new client methods
- `portal/web/src/pages/PaperRunDetail.tsx` — wire Trades / Decision log / LivePnLChart seed
- `portal/web/src/components/TradesTable.tsx` — add origin + signal_reason columns
- `tests/integration/test_paper_read_endpoints.py` (new)
- `tests/unit/api/services/test_paper_list_helpers.py` (new)
- `portal/web/tests/components/TradesTable.test.tsx` (updated)
- `portal/web/tests/pages/PaperRunDetail.test.tsx` (updated)
- `portal/web/tests/e2e/paper-trading.spec.ts` (updated)

## Verification

1. `make test-unit` — new list-helper + Vitest tests pass.
2. `make test-integration` — read-endpoints RBAC suite passes; existing v0.6.0 RBAC isolation suite still passes (regress).
3. `make e2e` — paper-trading spec asserts Trades / Decision log populate.
4. **UAT manual verification:**
   - Open `/paper/:id` for a running paper deployment.
   - Confirm Trades table shows entries with signal reason ("SMA_20 crossed above SMA_50" etc.).
   - Confirm Decision log scrolls in real time with `USER_ORDER_PLACED`, `USER_SIGNAL_EVALUATED`, gate refusals.
   - Reload the page — LivePnLChart shows the full historical curve, not a fresh start.
