# US-017: Paper Trading UX Design Lockdown

**Version:** v0.6.0 — Paper Trading MVP
**Priority:** P0 — Must Have (hard gate for all subsequent v0.6.0 backend PRs)
**Status:** TODO

## Given-When-Then

**GIVEN** we are about to build a multi-tenant, kill-switch-equipped paper trading experience with per-user OANDA credentials
  **AND** every downstream engineering PR (RBAC, credentials, runner, portal) will be shaped by the UX
**WHEN** we begin the v0.6.0 milestone
**THEN** the `trading-ux-designer` agent produces a two-pass UX deliverable that Wilson signs off in writing BEFORE any backend code PR opens:

1. **Pass A — low-fidelity wireframes** (grayscale, annotated flows) as static HTML under `portal/web/wireframes/v0.6.0/wireframes/`.
2. **Pass B — high-fidelity clickable HTML mockups** using real Tailwind classes under `portal/web/wireframes/v0.6.0/mockups/`, so the eventual React implementation can lift markup close to verbatim.

Six flows must be designed end-to-end, aligned with the user-approved decisions locked before UX begins.

## User-approved decisions (locked)

- Concurrent paper runs per user: **unlimited (soft cap)** — list/detail UX must scale, aggregate P&L across runs shown per user
- Approval workflow: **separate approval queue** — dedicated `/paper/approvals` page; state machine `AWAITING_APPROVAL → APPROVED → RUNNING`
- Kill switch: **big red button + 3-second undo toast** — no modal, no type-name confirmation
- Notifications: **deferred to v0.6.1** — portal is the source of truth

## Six flows to design

1. **First-time TRADER onboarding.** Sign in → land on empty `/paper` → banner "Set OANDA credentials before deploying" → `/settings` → credentials form → "Test connection" → verified badge → back to `/paper` → CTA "Approve a validated genome."
2. **Approval queue.** `/paper/approvals` shows cards per pending VALIDATED genome (metrics summary, "View genome details" link, Approve + Reject buttons). Approve opens `DeployPaperModal` (name, initial balance, max position size, daily loss threshold) → confirm → status becomes APPROVED and paper runner picks it up.
3. **Live monitoring.** `/paper/:id` — header (status pill, big red kill switch top-right, live P&L, owner email in ADMIN view), sections: `MetricsCard` grid, `LivePnLChart` (live vs backtest OOS overlay by relative timestamp from run start), `StrategyChart` (backtest markers + live-trade markers overlaid), `TradesTable` with `origin: live | backtest` badge, decision-log stream.
4. **Kill switch.** Big red button → single click starts 3-second toast countdown with prominent Undo button → after 3s the halt fires (positions close, status `HALTED_MANUAL`, `stop_reason=USER_KILL`). Undo cancels within the window with no side effect.
5. **Settings / credentials.** `/settings` tabbed (Profile / OANDA Credentials / [future API tokens]) → credentials tab shows masked account ID (last 4), verified badge with last-verified timestamp, environment selector (PRACTICE / [LIVE disabled with tooltip]), edit / delete / verify buttons.
6. **Multi-user visibility.** TRADER sees only own runs on `/paper` and `/paper/approvals`. ADMIN gets a scope toggle "My runs / All runs" — list items show owner email badge in the "All" scope.

## Acceptance Criteria

- [ ] `portal/web/wireframes/v0.6.0/wireframes/` contains grayscale wireframes for all 6 flows with annotations
- [ ] `portal/web/wireframes/v0.6.0/mockups/` contains clickable high-fidelity HTML mockups for all 6 flows using real Tailwind classes
- [ ] Every empty / loading / error / success state per screen is drawn (not just the happy path)
- [ ] Kill switch mockup shows the 3-second countdown toast + Undo button explicitly
- [ ] Approval queue mockup shows both TRADER (own only) and ADMIN (scope toggle) views
- [ ] Credential form mockup shows PRACTICE selected, LIVE tooltip-disabled
- [ ] Wilson signs off in writing on the mockups PR before Phase 3 (RBAC refactor) begins — enforced in PR reviewer checklist
- [ ] Reusable components are called out by name in mockup annotations: `StrategyChart`, `TradesTable`, `MetricsCard`, `WalkForwardFoldTable`, `Skeleton`, `ProtectedRoute`

## Files Involved

- `portal/web/wireframes/v0.6.0/wireframes/` — Pass A output
- `portal/web/wireframes/v0.6.0/mockups/` — Pass B output
- `portal/web/wireframes/v0.6.0/README.md` — flow narratives + decision log summarising the six user decisions and their UX implications

## Verification

- Review meeting: walk through all 6 flows on the clickable mockups. Wilson gives explicit sign-off (comment on PR).
- After sign-off: Phase 3 (RBAC refactor) opens.
- If mockup review reveals a design flaw that would materially change an already-in-flight backend design decision, block the affected PR until the mockup is amended and re-approved.
