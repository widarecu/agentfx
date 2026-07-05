# US-022: Paper Trading Portal Pages

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (delivers the UX approved in US-017)
**Status:** TODO

## Given-When-Then

**GIVEN** the UX designed in US-017 has been approved and the backend from US-018 through US-021 is in place
**WHEN** the frontend implementation ships
**THEN** a TRADER user can navigate the four new routes (`/settings`, `/paper`, `/paper/approvals`, `/paper/:id`), send a validated genome to the approval queue, approve it, watch it run live, and kill-switch it — all without needing developer support

  **AND** the pages reuse existing components (`StrategyChart`, `TradesTable`, `MetricsCard`, `WalkForwardFoldTable`, `Skeleton`, `ProtectedRoute`) rather than reimplementing equivalents
  **AND** ADMIN users see a "My runs / All runs" scope toggle on `/paper` (US-018 backend behind it).

## Acceptance Criteria

### Routes (in `portal/web/src/router.tsx`)

- [ ] `/settings` → `Settings.tsx` — tabbed layout (Profile / OANDA Credentials / [placeholder for future tabs])
- [ ] `/paper` → `PaperRuns.tsx` — user's paper runs list, aggregate P&L header, per-run compact cards, 5s polling per portal convention, ADMIN scope toggle
- [ ] `/paper/approvals` → `ApprovalQueue.tsx` — VALIDATED genomes awaiting user approval
- [ ] `/paper/:id` → `PaperRunDetail.tsx` — live monitoring with kill switch

All routes wrapped in `ProtectedRoute` + `<RequireRole allowed={['TRADER','ADMIN']}>` (US-018).

### New pages

- [ ] `Settings.tsx` — tabbed layout using existing patterns from `EvolutionRuns.tsx`. CredentialsForm rendered under the OANDA Credentials tab.
- [ ] `ApprovalQueue.tsx` — cards per pending VALIDATED genome (`ApprovalQueueCard`), Approve opens `DeployPaperModal`, Reject opens a small reason-collection modal.
- [ ] `PaperRuns.tsx` — paginated list, aggregate P&L in the section header, per-run cards showing status pill / instrument / current P&L / one-tap access to `/paper/:id`. 5s polling refreshes running rows.
- [ ] `PaperRunDetail.tsx` — hero (status + kill switch + owner email in ADMIN view + P&L), sections: `MetricsCard` grid (live P&L, hit rate, drawdown), `LivePnLChart` (live vs backtest overlay by relative timestamp from run start), `StrategyChart` (backtest markers + live-trade markers overlaid), `TradesTable` (extended with `origin: live | backtest` badge), decision log stream. 5s polling.

### New components (`portal/web/src/components/`)

- [ ] `KillSwitchButton.tsx` — big red button top-right of `/paper/:id`. Single click starts a 3-second toast countdown with an Undo button. If undo is not clicked within 3s, calls `POST /api/v1/paper-runs/:id/kill`. Button becomes disabled once `kill_requested_at` is set on the row.
- [ ] `LivePnLChart.tsx` — SVG line chart (adapt patterns from `FitnessCurveChart.tsx`), live equity curve overlaid on backtest OOS equity by relative timestamp from run start. X-axis: relative hours since run start. Y-axis: normalised equity.
- [ ] `CredentialsForm.tsx` — 3 fields (account ID, API key masked, environment dropdown), "Test connection" button that calls `POST /api/v1/users/me/credentials/verify`, shows verified badge with last-verified timestamp on success. Masked-on-read (never renders plaintext key).
- [ ] `DeployPaperModal.tsx` — invoked from `ApprovalQueueCard` Approve action. Form fields: name, initial balance, max position size, daily loss threshold. Client-side validation; submit disabled until all fields valid. On submit calls `POST /api/v1/paper-runs/{run_id}/approve`.
- [ ] `ApprovalQueueCard.tsx` — compact genome summary (instrument, validation Sharpe, hit rate, trade count), "View genome details" link (opens `/evolution/:id/genomes/:gid` in new tab), Approve and Reject buttons.

### Existing files touched

- [ ] `portal/web/src/pages/GenomeDetail.tsx` — add "Send to approval queue" button next to existing "Backtest" button. Enabled only when `validator_passed=true`. Calls `POST /api/v1/paper-runs/enqueue`. Shows a success toast on 202.
- [ ] `portal/web/src/services/paperApi.ts` (new) — mirrors `evolutionApi.ts` shape: `listPaperRuns`, `getPaperRun`, `getApprovalQueue`, `enqueuePaperRun`, `approvePaperRun`, `rejectPaperRun`, `killPaperRun`, `deletePaperRun`
- [ ] `portal/web/src/services/credentialsApi.ts` (new) — `getCredentials`, `putCredentials`, `deleteCredentials`, `verifyCredentials`
- [ ] `portal/web/src/App.tsx` (or nav component) — add "Paper" and "Settings" nav entries, visible only for TRADER/ADMIN (checked via `useAuth()`)
- [ ] `portal/web/src/types/paper.ts` (new) — TypeScript types mirroring backend Pydantic schemas
- [ ] `portal/web/src/types/credentials.ts` (new) — same

### Tests

- [ ] Vitest for `KillSwitchButton.tsx` — click triggers countdown, undo cancels, timeout fires kill callback
- [ ] Vitest for `CredentialsForm.tsx` — form validation, API key never rendered plaintext, verify button state
- [ ] Vitest for `DeployPaperModal.tsx` — form validation, disabled state, submit calls approve endpoint
- [ ] Vitest for `ApprovalQueueCard.tsx` — Approve / Reject button wiring
- [ ] Playwright E2E in US-023 covers the full happy path

## Design references

- UX mockups in `portal/web/wireframes/v0.6.0/mockups/` — implementation should lift markup close to verbatim
- Existing page container pattern from `pages/EvolutionRuns.tsx` (page → header → error → loading → content → modal)
- Existing polling pattern: 5s `setInterval` in `useEffect`, cleanup on unmount, only poll when at least one row is in `RUNNING` state

## Files Involved

- `portal/web/src/router.tsx` (edit)
- `portal/web/src/pages/Settings.tsx` (new)
- `portal/web/src/pages/PaperRuns.tsx` (new)
- `portal/web/src/pages/ApprovalQueue.tsx` (new)
- `portal/web/src/pages/PaperRunDetail.tsx` (new)
- `portal/web/src/pages/GenomeDetail.tsx` (edit — add Send-to-Queue button)
- `portal/web/src/components/KillSwitchButton.tsx` (new)
- `portal/web/src/components/LivePnLChart.tsx` (new)
- `portal/web/src/components/CredentialsForm.tsx` (new)
- `portal/web/src/components/DeployPaperModal.tsx` (new)
- `portal/web/src/components/ApprovalQueueCard.tsx` (new)
- `portal/web/src/services/paperApi.ts` (new)
- `portal/web/src/services/credentialsApi.ts` (new)
- `portal/web/src/types/paper.ts` (new)
- `portal/web/src/types/credentials.ts` (new)
- `portal/web/tests/unit/*.test.tsx` (new for each new component)

## Verification

1. `make test-unit` — Vitest suites pass
2. Manual: log in as TRADER, navigate all four routes, exercise every button and empty / loading / error state
3. Cross-role manual: log in as VIEWER, confirm cannot access `/paper`, `/paper/approvals`, `/paper/:id`, `/settings` (redirected with toast)
4. Cross-user manual: log in as User B, confirm `/paper` shows no runs from User A; navigate directly to `/paper/{User-A-run-id}` → 403 toast
5. Playwright E2E in US-023 covers scripted flow
