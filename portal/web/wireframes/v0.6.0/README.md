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

## Answers received (Wilson · 2026-07-05)

Wilson answered all 8 open questions on the initial PR. Mockups + wireframes have been revised on the same branch (`docs/v0.6.0-ux-wireframes`). Summary of what changed and why below.

| # | Question | Answer | Change made |
|---|---|---|---|
| 1 | Trades table: add a signal reason column? | **Yes** | Added `Signal reason` column to `paper-run-detail.html` trades table + Flow 3 wireframe. Value = DSL entry rule + exit cause (TP / SL / opposite / kill). |
| 2 | LivePnLChart x-axis: relative-only or dual? | **Relative + actuals** | SVG chart now has two x-axes: bottom = relative hours since run start, top = actual UTC timestamp. |
| 3 | Approval queue empty state? | **Yes, meaningful** | New `paper-approvals-empty.html` mockup — positive "your queue is empty" + Gate B recap + recent-activity strip (last approve/reject events). |
| 4 | Deploy modal defaults? | **Rethink — introduce accounts** | Removed the "Initial balance (USD)" field entirely (that's set inside OANDA when you open the account). Added an OANDA sub-account picker (radio list, live balances). Added a read-only "Snapshot balance" preview showing what will be captured at run start. Same treatment applied to Settings — see "OANDA account model" below. |
| 5 | Reject reason required? | **Required** | `reject-modal.html` now requires ≥ 10 chars, adds quick-pick chips, char counter. Reject button disabled until valid. |
| 6 | Nav placement of "Paper"? | **Fine** | Unchanged. |
| 7 | Owner badge colour? | **Indigo** | Unchanged (mockups already indigo). |
| 8 | Concurrency indicator at 10+ paper runs? | **Yes** | Amber banner appears above the list on `paper-runs.html` and `admin-view.html` when running count ≥ 10. Explains broker quota headroom + dismissible for 24h. |

## OANDA account model (Wilson's answer #4, expanded)

Wilson pointed out — correctly — that an "initial balance" is not something AgentFX can set. Balances live in OANDA. A user with an OANDA login can have multiple sub-accounts under one API key, each with its own currency, balance, and risk isolation. The revised model:

- **Credentials store just the API key** (plus environment: `PRACTICE` for now, `LIVE` gated by v1.0.0).
- **On verify** AgentFX calls OANDA `/v3/accounts` and lists the sub-accounts the token can access. Each row: account ID, alias, currency, balance, margin available. This list is shown read-only in `/settings` and cached in the DB (ID + alias + currency; balance always queried live).
- **Deploy modal picks one sub-account** from that list. The account row is highlighted; other columns (max position, daily-loss %, kill-switch DD) are unchanged.
- **Snapshot balance** — at the moment the paper runner subprocess starts, it queries the selected account's current balance and stores it on the `runs` row as `snapshot_balance`. That's the denominator for all P&L % numbers on the run detail page. It replaces the manual "initial balance" input.
- **Run detail** shows both `snapshot_balance` and the **live** account balance from OANDA (polled every 5 s), so users can see both "how has this run performed" (delta from snapshot) and "what's actually in the account right now" (live).
- **Concurrency banner mentions per-API-key quotas** because multiple runs on one key share the OANDA v20 REST rate limit (120 req/s). The admin banner calls this out explicitly.

### Impact on US files (needs downstream update — flag in PR-1, act in PR-C)

The following US files were written before the accounts concept and need revising before Phase 4 begins. Recorded here so they aren't forgotten:

- **US-019** (`user_oanda_credentials`) — schema drops `oanda_account_id` from this table. Add new table `user_oanda_accounts` cached from `/v3/accounts` (columns: `id`, `user_credentials_id` FK, `oanda_account_id`, `alias`, `currency`, `last_seen_at`). Verify endpoint now returns the account list, not just a boolean.
- **US-020** (`OandaClient` order-execution) — add `list_accounts()` returning the account list; `place_market_order()` etc. gain an `account_id` positional arg. Client is scoped to one API key, addressed by account ID per call.
- **US-021** (paper runner) — `runs.oanda_account_id` column added (FK-less; validated against the cached table on start). `runs.snapshot_balance` column added. Loop snapshots balance on first tick before placing any order. `initial_balance` field on `runs` becomes non-nullable via backfill = `snapshot_balance` for consistency.
- **US-022** (portal) — `DeployPaperModal` gains an account list; `CredentialsForm` shrinks to API key + environment only; `PaperRunDetail` shows account context + live balance vs snapshot.
- **US-023** (risk controls + approval state machine) — daily loss cap is computed from `snapshot_balance` (not user input). Kill switch, DD threshold unchanged.

These edits will land as part of PR-C (credentials schema + migration) — noting them here so the Phase 4 branch starts from the right shape.

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

**Sequence**: land on empty `/paper` → prominent banner "Set OANDA credentials before deploying" → click Set-up-credentials → `/settings` OANDA tab → paste API key + pick PRACTICE → Save → Test connection → OANDA returns account list → detected-accounts table appears → verified badge with timestamp → return to `/paper` → empty state now says "You're set up. Go to the approval queue."

**Design decision**: paper-empty and approval-queue-empty states point at the *next action*, not at settings. Users shouldn't have to navigate manually.

**Mockup**: [`mockups/paper-empty.html`](./mockups/paper-empty.html), [`mockups/settings-credentials.html`](./mockups/settings-credentials.html)

### Flow 2 — Approval queue

**Goal**: review VALIDATED genomes that the user (or an agent, later) has sent to their queue, and explicitly approve each one they want to paper-trade.

**Sequence**: `/paper/approvals` → cards per pending genome with Gate-B-coloured metrics (green ▲ / rose ✗ / amber borderline) → Approve opens the Deploy Paper modal → pick the OANDA sub-account (live balances shown) → configure name / max position / thresholds → Confirm → redirect to `/paper/:new_id` where the paper runner has started. The runner snapshots the account balance on its first tick; that value is the P&L % denominator for the whole run.

**Empty state**: `paper-approvals-empty.html` — positive affirmation "your queue is empty", Gate B recap, recent-activity strip (last approve/reject events). Never a scolding tone.

**Design decision**: metrics are coloured by *individual* Gate-B threshold status, not by overall pass/fail. Users see immediately why a genome is borderline vs solid. Cards for genomes that fully clear Gate B get a subtle green border to distinguish them.

**Design decision**: Approve is the primary CTA (colored, prominent). Reject is a secondary text button — it's an intentional escape hatch, not a symmetric choice.

**Mockup**: [`mockups/paper-approvals.html`](./mockups/paper-approvals.html), [`mockups/deploy-modal.html`](./mockups/deploy-modal.html), [`mockups/reject-modal.html`](./mockups/reject-modal.html)

### Flow 3 — Live monitoring (the money view)

**Goal**: user knows at a glance whether a running paper deployment is behaving like the backtest. This is the page users will stare at.

**Layout** (top → bottom): sticky header with status pill + P&L + kill switch → 6-tile metrics grid → **the money chart** (Live equity vs backtest OOS overlay, same relative timestamp from run start) → three side-by-side cards (position, today's activity, risk config) → recent trades table with origin badges → candlestick chart with live+backtest markers overlaid → decision log stream.

**Design decision**: sticky header keeps status + P&L + kill switch always visible while scrolling. Users must never have to scroll up to find the kill switch in an emergency.

**Design decision**: LivePnLChart is dual-x-axis. Bottom = relative hours since run start (aligns live-hour-0 with backtest-hour-0 so divergence over same relative duration is visually obvious). Top = actual UTC timestamp (so users can correlate spikes with real-world events).

**Design decision**: Trades table has three semantic columns beyond price/pips — Origin (`live | backtest`), Direction (`LONG | SHORT`), and **Signal reason** (DSL entry rule that fired + exit cause: TP hit / SL hit / opposite signal / kill). Signal reason lets users answer "which rule paid, and why did the trade close" without opening the DSL editor.

**Design decision**: Account context is shown in the header (OANDA account ID + alias + currency) and the Account & Risk panel (snapshot balance + live balance). P&L % = (live balance − snapshot) / snapshot; balance is refetched from OANDA every 5s. Users never type an initial balance — it's read from the selected sub-account at run start.

**Design decision**: Everything on this page updates on 5-second polling — matches the existing portal convention from `EvolutionRunDetail`. WebSocket streaming is a v0.6.2+ optimization.

**Mockup**: [`mockups/paper-run-detail.html`](./mockups/paper-run-detail.html)

### Flow 4 — Kill switch

**Goal**: user halts a paper run in one click. Undo protects against accidents.

**Sequence**: click big red button → 3-second toast appears (countdown ticking, prominent Undo button, thin progress bar) → if user clicks Undo → toast dismisses, no side effect, no API call made → if 3 seconds elapse → toast morphs to "Halting…" spinner, `POST /paper-runs/:id/kill` fires → backend sets `kill_requested_at` → paper runner polls this on next loop tick (≤ granularity seconds), closes all OANDA positions, transitions to `HALTED_MANUAL` → toast dismisses when status transition arrives via polling.

**Design decision**: NO modal, NO type-name-to-confirm. Modal introduces friction that in a real emergency (a strategy losing money fast) hurts more than a rare accidental click. The 3-second undo is the "did I mean that?" affordance.

**Design decision**: while toast is countdown-active, the underlying page is dimmed and kill button is greyed. Prevents double-click / spam.

**Mockup**: [`mockups/kill-switch-countdown.html`](./mockups/kill-switch-countdown.html)

### Flow 5 — Settings / credentials

**Goal**: user pastes an OANDA API key, verifies it, and sees the list of sub-accounts it can access. Four states (empty, edit form, set-but-unverified, set-and-verified) plus a fifth "detected accounts" panel that renders under the verified state.

**Design decision**: form collects **only the API key** and environment. The account ID is not typed — it's discovered from OANDA on verify. This is the fix for Wilson's answer #4 — see the "OANDA account model" section above.

**Design decision**: detected-accounts table is read-only. Columns: account ID, alias, currency, live balance, live margin available, open runs count. Balance always fetched live; AgentFX only persists the ID + alias + currency.

**Design decision**: verified state gets a subtle emerald border to make "you're good to deploy" spatially unambiguous. The "Test connection" CTA disappears once verified — replaced by "Re-verify" as a manual refresh path.

**Design decision**: LIVE radio is present but disabled with tooltip "Live trading requires ADMIN approval and enables in v1.0.0." Users see the feature exists without accidentally selecting it.

**Design decision**: Delete gets a danger colour (red border, red text) so it visually stands apart from Edit/Re-verify. Delete triggers a follow-up confirm dialog if any paper runs are currently active under this credential.

**Design decision**: fine-print at the bottom explains "Fernet encrypted at rest, plaintext never in API response or log" — builds trust without being a wall of legalese.

**Mockup**: [`mockups/settings-credentials.html`](./mockups/settings-credentials.html)

### Flow 6 — Multi-user visibility (ADMIN)

**Goal**: ADMIN can inspect other users' paper runs for support / oversight; TRADER never sees others' data.

**Design decision**: for ADMIN, an "My runs / All runs" toggle appears in the header of `/paper`, `/paper/approvals`. **Default is "My runs"** — a fresh ADMIN session shouldn't accidentally expose them to the whole system's data.

**Design decision**: in "All runs" mode, each row shows a distinct indigo owner-email badge. ADMIN badge in the top-right nav also colored indigo for role clarity.

**Design decision**: rows also show an OANDA account badge (e.g. `acct …-001`) — support / oversight questions frequently involve "which account is this hitting". Same badge on the TRADER (`/paper`) list.

**Design decision**: TRADER users don't see the toggle. Backend enforces `scope=all` → 403 when role != ADMIN (per US-018).

**Concurrency banner**: when running-run count ≥ 10 (per-user for TRADER, fleet-wide for ADMIN), an amber banner appears above the list. Explains that OANDA v20 REST enforces 120 req/s per API key and calls out the user (in ADMIN scope) most at risk of throttling. Dismissible for 24h.

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
| `LivePnLChart.tsx` | Run detail page | Live equity curve overlaid on backtest OOS equity, dual-x-axis (relative hours + actual UTC) |
| `CredentialsForm.tsx` | Settings page | API-key + environment; test-connection triggers OANDA account discovery |
| `OandaAccountsTable.tsx` | Settings page | Read-only table of detected sub-accounts + live balances; polled every 15s while page open |
| `DeployPaperModal.tsx` | Approval queue | Modal from Approve action; account picker (radio list, live balances) + risk knobs; snapshot balance preview |
| `ApprovalQueueCard.tsx` | Approval queue | Compact genome summary + Gate-B-colored metrics + Approve/Reject CTAs |
| `RejectGenomeModal.tsx` | Approval queue | Required reason (≥ 10 chars) + quick-pick chip enum + counter |
| `ConcurrencyBanner.tsx` | Runs list, admin view | Amber banner shown when running-run count ≥ 10 |

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

## Follow-ups after sign-off

Small items outside the 8 answered questions that are worth calling out before Phase 3 begins:

- **Rejection reasons feed the Evolution agent.** The quick-pick chips in `reject-modal.html` are UI sugar — they resolve to structured `reject_reason_code` values (e.g. `TRADE_COUNT_LOW`, `SHARPE_BORDERLINE`, `OVERFIT_TO_REGIME`, `WAIT_FOR_REEVOLUTION`, `SIMILAR_TO_RUNNING`, `OTHER`) plus the free-text `reject_reason_text`. Evolution agent V1 in v0.7.0 will bias its variation generator away from patterns tagged with the top rejection codes. Noted so the DB migration in PR-F picks the enum up front rather than adding it later.
- **Snapshot balance is captured post-authorization but pre-first-order.** If OANDA is unreachable at that moment, the runner retries per its backoff policy; if it exhausts retries it transitions the run to `ERROR` with `stop_reason=BROKER_UNREACHABLE` and never places an order. No live orders exist without a snapshot.
- **Concurrency banner threshold is a constant for MVP.** Hard-coded at 10 — no user setting yet. If Wilson later wants it per-user (some users have higher OANDA rate limits under premium tiers), we add `settings.concurrency_warn_threshold` in v0.6.1.

## Verification checklist for this mockup PR

- [ ] Open `wireframes/index.html` — all 6 flows link out and back
- [ ] Open `mockups/index.html` — all 10 mockups link out and back (including the new empty-state variant)
- [ ] Kill switch mockup — countdown JS visibly ticks 2 → 1 → 0
- [ ] Deploy modal → account picker shows 3 accounts with live balances; Approve & deploy → `paper-run-detail.html`
- [ ] Approval queue → click Reject → `reject-modal.html` with required reason + quick-pick chips
- [ ] Settings shows API-key-only form + detected-accounts table
- [ ] Run detail sticky header shows OANDA account context; trades table has Signal reason column; chart has dual x-axis
- [ ] `paper-runs.html` and `admin-view.html` show the concurrency banner (running count ≥ 10)
- [ ] `paper-approvals-empty.html` reachable from `paper-approvals.html`'s footer link
- [ ] Every mockup is legible in browser dark mode (via `dark:` variants)

## After sign-off

Once Wilson approves on the PR:
1. Merge PR-1 (this UX package)
2. Phase 3 (RBAC refactor — PR-A / PR-B) opens
3. Backend and frontend implementation lift markup close-to-verbatim from these mockups
