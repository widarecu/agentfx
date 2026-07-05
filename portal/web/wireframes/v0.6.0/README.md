# v0.6.0 · Paper Trading — UX Package

This directory contains the two-pass UX deliverable for the v0.6.0 Paper Trading MVP.

- **Wireframes** (`wireframes/`): grayscale, annotated low-fidelity screens showing information hierarchy, page sequence, and interaction states. Open [`wireframes/index.html`](./wireframes/index.html) as the entry point.
- **Mockups** (`mockups/`): high-fidelity clickable HTML using real Tailwind classes. Numbers, labels, and layouts match what will ship in the React implementation. Open [`mockups/index.html`](./mockups/index.html) as the entry point.

Both directories are self-contained: each HTML file loads Tailwind via CDN, no build step, opens in any browser.

## Purpose

Phase 2 of the v0.6.0 plan (see `~/.claude/plans/i-think-we-need-functional-pony.md`) is a **hard gate**: no backend PR (RBAC refactor, credential store, paper runner, portal pages) opens until Wilson signs off on these mockups. The value is:

1. Everyone agrees on **what the experience is** before code shapes any decision.
2. Downstream React implementation is a mechanical port of the mockup markup, not a redesign in flight.
3. Wilson catches ambiguity and disagreement while it is still cheap.

Sign-off means: comment on the PR narrating any changes wanted, then explicit approval.

## Wilson-approved decisions (locked before this UX pass began)

| Decision | Choice | Rationale |
|---|---|---|
| Concurrent paper runs per user | Unlimited (soft cap) | Users may want to A/B multiple genomes at once. UI must scale — list uses compact rows with aggregate P&L header. |
| Approval workflow | Separate approval queue with explicit Approve/Reject | Human sign-off = human clicks Approve. Prevents accidental deploys; audit trail is unambiguous. |
| Kill switch | Big red button + 3-second undo toast | Fast-path emergency stop with a short "did I mean that?" window. No modal typing friction; no accidental double-click risk. |
| Notifications | Deferred to v0.6.1 | Portal is source of truth for MVP. Adds no engineering scope now. |

## Per-flow narrative

### Flow 1 — First-time TRADER onboarding

**Goal**: get a new user from "just signed in" to "OANDA verified, ready to approve a genome" in under a minute.

**Sequence**: land on empty `/paper` → prominent banner "Set OANDA credentials before deploying" → click Set-up-credentials → `/settings` OANDA tab → fill form (account ID + API key + PRACTICE) → Save → Test connection → verified badge appears with timestamp → return to `/paper` → empty state now says "You're set up. Go to the approval queue."

**Design decision**: paper-empty and approval-queue-empty states point at the *next action*, not at settings. Users shouldn't have to navigate manually.

**Mockup**: [`mockups/paper-empty.html`](./mockups/paper-empty.html), [`mockups/settings-credentials.html`](./mockups/settings-credentials.html)

### Flow 2 — Approval queue

**Goal**: review VALIDATED genomes that the user (or an agent, later) has sent to their queue, and explicitly approve each one they want to paper-trade.

**Sequence**: `/paper/approvals` → cards per pending genome with Gate-B-coloured metrics (green ▲ / rose ✗ / amber borderline) → Approve opens the Deploy Paper modal → configure name / initial balance / max position / thresholds → Confirm → redirect to `/paper/:new_id` where the paper runner has started.

**Design decision**: metrics are coloured by *individual* Gate-B threshold status, not by overall pass/fail. Users see immediately why a genome is borderline vs solid. Cards for genomes that fully clear Gate B get a subtle green border to distinguish them.

**Design decision**: Approve is the primary CTA (colored, prominent). Reject is a secondary text button — it's an intentional escape hatch, not a symmetric choice.

**Mockup**: [`mockups/paper-approvals.html`](./mockups/paper-approvals.html), [`mockups/deploy-modal.html`](./mockups/deploy-modal.html), [`mockups/reject-modal.html`](./mockups/reject-modal.html)

### Flow 3 — Live monitoring (the money view)

**Goal**: user knows at a glance whether a running paper deployment is behaving like the backtest. This is the page users will stare at.

**Layout** (top → bottom): sticky header with status pill + P&L + kill switch → 6-tile metrics grid → **the money chart** (Live equity vs backtest OOS overlay, same relative timestamp from run start) → three side-by-side cards (position, today's activity, risk config) → recent trades table with origin badges → candlestick chart with live+backtest markers overlaid → decision log stream.

**Design decision**: sticky header keeps status + P&L + kill switch always visible while scrolling. Users must never have to scroll up to find the kill switch in an emergency.

**Design decision**: LivePnLChart uses "relative timestamp from run start" for the x-axis, not calendar time. Aligns live-run-hour-0 with backtest-hour-0 so divergence over the same relative duration is visually obvious.

**Design decision**: Trades table's `origin: live | backtest` badge column lets users answer "did the strategy actually trade what the backtest said it would?" in a quick scan.

**Design decision**: Everything on this page updates on 5-second polling — matches the existing portal convention from `EvolutionRunDetail`. WebSocket streaming is a v0.6.2+ optimization.

**Mockup**: [`mockups/paper-run-detail.html`](./mockups/paper-run-detail.html)

### Flow 4 — Kill switch

**Goal**: user halts a paper run in one click. Undo protects against accidents.

**Sequence**: click big red button → 3-second toast appears (countdown ticking, prominent Undo button, thin progress bar) → if user clicks Undo → toast dismisses, no side effect, no API call made → if 3 seconds elapse → toast morphs to "Halting…" spinner, `POST /paper-runs/:id/kill` fires → backend sets `kill_requested_at` → paper runner polls this on next loop tick (≤ granularity seconds), closes all OANDA positions, transitions to `HALTED_MANUAL` → toast dismisses when status transition arrives via polling.

**Design decision**: NO modal, NO type-name-to-confirm. Modal introduces friction that in a real emergency (a strategy losing money fast) hurts more than a rare accidental click. The 3-second undo is the "did I mean that?" affordance.

**Design decision**: while toast is countdown-active, the underlying page is dimmed and kill button is greyed. Prevents double-click / spam.

**Mockup**: [`mockups/kill-switch-countdown.html`](./mockups/kill-switch-countdown.html)

### Flow 5 — Settings / credentials

**Goal**: user manages their OANDA credentials — four states (empty, edit form, set-but-unverified, set-and-verified).

**Design decision**: verified state gets a subtle emerald border to make "you're good to deploy" spatially unambiguous. The "Test connection" CTA disappears once verified — replaced by "Re-verify" as a manual refresh path.

**Design decision**: LIVE radio is present but disabled with tooltip "Live trading requires ADMIN approval and enables in v1.0.0." Users see the feature exists without accidentally selecting it.

**Design decision**: Delete gets a danger colour (red border, red text) so it visually stands apart from Edit/Re-verify. Delete triggers a follow-up confirm dialog if any paper runs are currently active under this credential.

**Design decision**: fine-print at the bottom explains "Fernet encrypted at rest, plaintext never in API response or log" — builds trust without being a wall of legalese.

**Mockup**: [`mockups/settings-credentials.html`](./mockups/settings-credentials.html)

### Flow 6 — Multi-user visibility (ADMIN)

**Goal**: ADMIN can inspect other users' paper runs for support / oversight; TRADER never sees others' data.

**Design decision**: for ADMIN, an "My runs / All runs" toggle appears in the header of `/paper`, `/paper/approvals`. **Default is "My runs"** — a fresh ADMIN session shouldn't accidentally expose them to the whole system's data.

**Design decision**: in "All runs" mode, each row shows a distinct indigo owner-email badge. ADMIN badge in the top-right nav also colored indigo for role clarity.

**Design decision**: TRADER users don't see the toggle. Backend enforces `scope=all` → 403 when role != ADMIN (per US-018).

**Mockup**: [`mockups/admin-view.html`](./mockups/admin-view.html)

## Component inventory

Components labeled with their exact filename in `portal/web/src/components/` (matching US-022):

### Reused (from existing codebase — no reimplementation)

| Component | Used in | Extension notes |
|---|---|---|
| `StrategyChart` | Run detail page | Extend to overlay live-trade markers on top of the existing backtest-marker layer |
| `TradesTable` | Run detail page | Add `origin: live \| backtest` badge column |
| `MetricsCard` | Run detail page | Direct reuse, no changes |
| `WalkForwardFoldTable` | Approval queue (referenced but not rendered in this MVP mockup) | Direct reuse in `GenomeDetail` |
| `Skeleton` | All list / detail pages | Direct reuse for loading states |
| `ProtectedRoute` | Router | Composes with new `RequireRole` |
| `FitnessCurveChart` | Approval queue (referenced) | Pattern basis for `LivePnLChart` |

### New components (proposed for US-022)

| Component | Used in | Purpose |
|---|---|---|
| `RequireRole.tsx` | Router | Role-gated route wrapper; composes with `ProtectedRoute` |
| `KillSwitchButton.tsx` | Run detail page | Big red button + 3-second undo toast; owns the `kill_requested_at` API call |
| `LivePnLChart.tsx` | Run detail page | Live equity curve overlaid on backtest OOS equity, relative-timestamp x-axis |
| `CredentialsForm.tsx` | Settings page | 3 fields + test-connection button; password-style masked API key with visibility toggle |
| `DeployPaperModal.tsx` | Approval queue | Modal collected from Approve action; validates client-side |
| `ApprovalQueueCard.tsx` | Approval queue | Compact genome summary + Gate-B-colored metrics + Approve/Reject CTAs |

### New pages (proposed for US-022)

| Page | Route | Uses |
|---|---|---|
| `Settings.tsx` | `/settings` | Tabbed layout; `CredentialsForm` first-class |
| `PaperRuns.tsx` | `/paper` | List of user's runs + ADMIN scope toggle; 5s polling |
| `ApprovalQueue.tsx` | `/paper/approvals` | Cards for pending genomes; opens `DeployPaperModal` / reject modal |
| `PaperRunDetail.tsx` | `/paper/:id` | Sticky header with kill switch; the "money view" |

### Existing files touched

- `portal/web/src/pages/GenomeDetail.tsx` — add "Send to approval queue" button (only enabled when `validator_passed=true`)
- `portal/web/src/router.tsx` — 4 new routes, all wrapped in `RequireRole allowed={['TRADER','ADMIN']}`
- Nav (in `App.tsx` or nav component) — add "Paper" and "Settings" entries

## Open questions for Wilson

1. **Trades table columns.** The current mockup shows Trade ID / Origin / Direction / Entry-Exit / Pips / P&L $ / Closed. Do you want a "signal reason" column? A stop-hit vs target-hit vs opposite-signal indicator? Trades table currently reuses `TradesTable`; we can extend the column set here if you want.

2. **LivePnLChart x-axis: relative vs calendar.** Mockup uses "hours since run start" so live and backtest curves align by duration. Alternative: dual-x — bottom axis relative hours, top axis actual timestamp. Adds complexity but more informative.

3. **Approval queue when empty.** Mockup shows a populated queue. What about the empty state? A "Head to Evolution to find a validated genome" nudge, or a passive "no pending approvals" message? Recommend the nudge, but calling out.

4. **Deploy modal defaults.** Currently: 10,000 initial balance, 10,000 max position (1 lot), 2% daily loss, 5% kill DD. Are these acceptable safe defaults for paper, or too aggressive / too conservative?

5. **Reject-with-reason: required or optional?** Mockup shows optional. If required, it slows down the reject flow but produces cleaner audit history. My preference: optional (mockup), with a placeholder that nudges toward filling it in.

6. **Nav placement of "Paper".** Currently between Evolution and Settings. Could also be at the top after Dashboard. Preference?

7. **Owner badge colour in ADMIN view.** Currently indigo (matches ADMIN badge). Alternative: neutral slate to reduce visual weight. Which feels right?

8. **Concurrency indicator.** Since unlimited concurrent runs, do we need a warning at some threshold (e.g., "you have 10+ paper runs; broker connection quotas apply")? Not in current mockup — flag if you'd want it.

## Verification checklist for this mockup PR

- [ ] Open `wireframes/index.html` — all 6 flows link out and back
- [ ] Open `mockups/index.html` — all 9 mockups link out and back
- [ ] Kill switch mockup — countdown JS visibly ticks 2 → 1 → 0
- [ ] Deploy modal → click "Approve & deploy" → lands on `paper-run-detail.html`
- [ ] Approval queue → click Reject → lands on `reject-modal.html`
- [ ] Settings verified state has visible emerald border to distinguish from unverified
- [ ] ADMIN view shows scope toggle, owner email badges on rows
- [ ] Every mockup is legible in browser dark mode (via `dark:` variants)

## After sign-off

Once Wilson approves on the PR:
1. Merge PR-1 (this UX package)
2. Phase 3 (RBAC refactor — PR-A / PR-B) opens
3. Backend and frontend implementation lift markup close-to-verbatim from these mockups
