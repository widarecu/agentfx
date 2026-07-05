# US-018: Per-User RBAC Filter on Runs, Jobs, Genomes

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (fixes an existing security bug; must ship before per-user paper trading)
**Status:** TODO

## Given-When-Then

**GIVEN** two authenticated users (User A, User B) both with role TRADER
  **AND** User A creates an evolution run, an optimization job, and a genome
**WHEN** User B calls `GET /api/v1/evolution-runs`, `GET /api/v1/evolution-runs/{User-A-run-id}`, `GET /api/v1/optimization-jobs`, or any per-user resource endpoint
**THEN** User B receives 403 Forbidden on the direct-ID lookup and an empty list on the collection endpoint
  **AND** an ADMIN user calling the same endpoints WITH `?scope=all` sees BOTH users' rows
  **AND** an ADMIN user calling the same endpoints WITHOUT `?scope=all` sees only their own rows (default behaviour matches TRADER)

## Why this must ship as part of v0.6.0

Audit conducted 2026-07-05 found that `evolution_service.list_runs()` and `optimization_service.get_jobs()` do NOT filter by `created_by`. The `created_by` FK exists on both tables but is not consulted at the query layer. Any authenticated user currently sees all other users' evolution runs, genomes (DSL specs), optimization jobs, and trials. Introducing paper trading (with real broker accounts) without fixing this amplifies the leak to real capital exposure. Fix must land before US-021 (paper runner) merges.

## Acceptance Criteria

### Backend

- [ ] New `src/agentfx/api/core/rbac.py` — single-source-of-truth helper `scope_query_to_user(query, model, current_user) -> query`. ADMIN passes through unchanged; other roles filter to `created_by = current_user.id`.
- [ ] `src/agentfx/api/routers/evolution.py` — every list / detail / delete endpoint invokes `scope_query_to_user()`. Detail endpoints do a router-level ownership check: load row, if `row.created_by != current_user.id AND current_user.role != ADMIN` → `HTTPException(403)`.
- [ ] `src/agentfx/api/routers/optimization.py` — same treatment as evolution.
- [ ] `src/agentfx/api/routers/runs.py`, `strategies.py` (v0.1 legacy routers) — same treatment where they expose user-owned data.
- [ ] Alembic migration: add `user_id` (nullable FK → `users.id`) on `decision_logs`. New action types: `USER_CREATE_RUN`, `USER_DELETE_RUN`, `USER_DEPLOY_PAPER`, `USER_KILL_PAPER`, `USER_UPDATE_CREDENTIALS`, `USER_APPROVE_GENOME`, `USER_REJECT_GENOME`. Every user-initiated action logs a row with `user_id`.
- [ ] Router endpoints support `?scope=all` query parameter, honoured only when `current_user.role == ADMIN`. Non-ADMIN passing `?scope=all` receives 403 (do not silently strip).

### Frontend

- [ ] New `portal/web/src/components/RequireRole.tsx` — wraps route element, reads `useAuth().user.role`, redirects to `/` with a toast if role not in `allowed`. Composes with existing `ProtectedRoute`.
- [ ] `portal/web/src/router.tsx` — wrap deploy / paper / settings / approval routes with `<RequireRole allowed={['TRADER','ADMIN']}>`.
- [ ] `EvolutionRuns.tsx`, `Optimization.tsx`, `PaperRuns.tsx` — ADMIN-only "My runs / All runs" scope toggle that passes `?scope=all`.

### Tests

- [ ] `tests/integration/test_rbac_isolation.py` — parameterised suite that seeds users A, B, and ADMIN, creates rows across all resource types (evolution runs, optimization jobs, genomes, DSL specs, decision logs, credentials, paper runs), and asserts:
  - User A can see own rows on list + detail
  - User B cannot see User A's rows (403 on detail, empty list)
  - User B without `?scope=all` cannot access even with knowledge of the run_id
  - ADMIN without `?scope=all` sees only own rows
  - ADMIN with `?scope=all` sees all rows
  - Non-ADMIN with `?scope=all` receives 403
- [ ] Vitest for `RequireRole` — renders children when role in `allowed`, redirects to `/` with toast when not.

## API Contract

### List with default scope (own resources only)

```
GET /api/v1/evolution-runs
Authorization: Bearer <JWT>
```

Response includes only rows where `created_by = current_user.id`, regardless of role.

### List with ADMIN scope=all

```
GET /api/v1/evolution-runs?scope=all
Authorization: Bearer <ADMIN-JWT>
```

Response includes all rows across users.

### Detail with cross-user access denied

```
GET /api/v1/evolution-runs/{other-users-run-id}
Authorization: Bearer <TRADER-JWT>
```

Response: `HTTP 403 Forbidden` with `{ "detail": "You do not have access to this resource" }`.

### Non-ADMIN scope=all rejected

```
GET /api/v1/evolution-runs?scope=all
Authorization: Bearer <TRADER-JWT>
```

Response: `HTTP 403 Forbidden`.

## Files Involved

- `src/agentfx/api/core/rbac.py` (new)
- `src/agentfx/api/routers/evolution.py` (edit)
- `src/agentfx/api/routers/optimization.py` (edit)
- `src/agentfx/api/routers/runs.py` (edit — legacy, but user-owned)
- `src/agentfx/api/routers/strategies.py` (edit — legacy, but user-owned)
- `src/agentfx/api/models/evolution.py` (edit — add `user_id` FK to `DecisionLog`)
- `migrations/versions/2026_07_XX_decision_logs_user_id.py` (new)
- `portal/web/src/components/RequireRole.tsx` (new)
- `portal/web/src/router.tsx` (edit)
- `portal/web/src/pages/EvolutionRuns.tsx`, `Optimization.tsx`, `PaperRuns.tsx` (edit — ADMIN scope toggle)
- `tests/integration/test_rbac_isolation.py` (new)
- `portal/web/tests/unit/RequireRole.test.tsx` (new — Vitest)

## Verification

1. `make test-integration` — new isolation suite passes; existing suites unchanged.
2. `make test-unit` — Vitest `RequireRole` suite passes.
3. Manual UAT: create a second TRADER user via `scripts/seed_test_data.py`. Log in as User A, create runs. Log in as User B, confirm empty list. Log in as ADMIN, toggle scope, confirm both users' runs visible.
4. Audit trail: verify `decision_logs` rows for user-initiated actions include correct `user_id`.
