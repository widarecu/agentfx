# US-027: Approval Queue Diagnostics + ADMIN Validator Override

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P1 — Should Have (turns "queue is empty" from mystery into diagnostic; legitimises the manual-SQL demo unblock)
**Status:** DONE — code shipped end-to-end; doc reconciled 2026-07-19

## Implementation notes (2026-07-19)

Two intentional drifts from the original spec that survived the audit
and should not be treated as gaps:

1. **`closest_to_passing` (singular) → `top_close_to_passing` (list of up
   to 5).** Wilson 2026-07-13: the heuristic single-pick was too noisy
   when several near-miss genomes clustered within a fraction of a
   Sharpe point. The banner now shows a ranked table (
   `CloseToPassingTable` in `ApprovalQueue.tsx:304`) so the user can
   compare margins across candidates. Schema field renamed to
   `top_close_to_passing`; type is `list[ClosestGenome]` in
   `src/agentfx/api/schemas/paper.py:217`.
2. **Frontend component tests are node-env pure-helper tests, not
   jsdom render tests.** The project's Vitest config runs in the
   `node` environment with no `@testing-library/react` (see
   `portal/web/vitest.config.ts`). The `_pickEmptyBannerScenario`
   classifier is fully covered by `tests/unit/approvalQueueBanner.test.ts`
   and the `ValidatorOverrideModal` submit-gate + payload-builder are
   covered by `tests/unit/validatorOverrideModal.test.ts` (added
   2026-07-19). Full render coverage stays out of scope until jsdom
   is added to devDependencies as a separate infrastructure change.

## Genesis

Wilson's 2026-07-08 UAT smoke-test hit an empty `/paper/approvals` and diagnosed it as a validator write-back bug. Follow-up audit of the UAT DB on 2026-07-09 corrected the picture:

| finding | count |
|---|---|
| Evolution runs completed | 17 |
| Genomes across those runs | 8,730 |
| Genomes the validator ran on (top-K per run) | 255 |
| Validator wrote `false` for (correctly, with folds + failure reasons) | 255 |
| Validator wrote `true` for | **0** |
| Total OOS validation folds that ever cleared Gate B | **0** / 2,040 |

The validator write-back path IS working — the `false` side is exercised on every real evolution. The `true` side has literally never been exercised on Wilson's data because no evolved genome has passed OOS Gate B in 17 runs. Best-training strategy (`019_028`, training Sharpe 2.61, 600 trades) collapsed to OOS Sharpe 0.34, 292 trades, failing three of four Gate B thresholds.

The `validator_passed = true` currently on `019_028` in the UAT DB was Claude's manual `UPDATE strategy_genomes SET validator_passed = true` during the smoke-test — signature confirmed by `validation_folds` count = 0 and `validation_failures = []` on that row (impossible from a real `save_validation()` call, which inserts folds and failures atomically with the flag).

So the platform's actual gap isn't a broken write-back. It's two things:

1. **The empty queue explains nothing.** The user sees "Approval queue empty" and cannot tell whether (a) no evolutions have run, (b) evolutions ran but nothing passed OOS, or (c) they already deployed everything that passed. Three very different problems, indistinguishable.
2. **Manual SQL is the only escape hatch for demos.** When Wilson needs to unblock a smoke-test walkthrough (or a first-time UAT visit), the only path is SSH → docker exec → psql → raw SQL. That's a fine emergency lever but it bypasses the audit log entirely.

US-027 addresses both without pretending to fix a bug that doesn't exist. The training/OOS gap itself (Sharpe 2.61 → 0.34) is v0.5.0's diagnostic-agent territory, not v0.6.1.

## Given-When-Then — Empty-queue diagnostic

**GIVEN** the user opens `/paper/approvals`
  **AND** the query returns zero deployable genomes for that user
**WHEN** the page renders the empty state
**THEN** the banner shows the appropriate diagnostic instead of a generic "queue empty" message:

| Situation | Banner content |
|---|---|
| Zero evolution runs owned by user | "No evolution runs yet. [Start one]" |
| Evolutions ran but 0 genomes validated | "17 evolution runs · 8,730 genomes generated · 0 validated. Validator hasn't run — check evolution run status." (rare — indicates a run crashed mid-loop) |
| Evolutions ran, genomes validated, 0 passed | "17 evolution runs · 255 genomes validated on OOS folds · 0 passed Gate B. Closest to passing: [genome_id] (OOS Sharpe 0.82, needs 1.0). [View evolution runs]" |
| Genomes passed but user already deployed them all | "3 validated genomes have been deployed. [View paper runs]" |

The "closest to passing" summary shows the highest-OOS-Sharpe non-passing genome + its failing gate margins so the user has one concrete signal about whether the evolution was close or nowhere near.

## Given-When-Then — ADMIN validator override

**GIVEN** the user has `role=ADMIN`
  **AND** they navigate to a specific genome's detail page (`/genomes/:id`)
**WHEN** they click the "Override validator verdict" button (only visible to ADMIN)
**THEN** a confirmation modal opens asking for:
- New value: `true` or `false` (dropdown; NULL is not an option — we don't offer un-validating)
- Reason: free-text, required, min 20 chars
  ("used for demo walkthrough", "diagnostic-agent identified false negative", etc.)

**WHEN** the user confirms
**THEN** the server:
1. Sets `strategy_genomes.validator_passed = new_value`.
2. Writes a `DecisionLog` row with:
   - `action = 'USER_VALIDATOR_OVERRIDE'`
   - `agent_type = 'user'`
   - `actor_user_id = current_user.id`
   - `outputs_json = {previous_value, new_value, reason, genome_id}`
   - `reasoning_text = f"ADMIN {email} overrode validator verdict for {genome_id}: {previous_value} → {new_value}. Reason: {reason}"`
3. Returns the updated genome.
4. The refreshed `/paper/approvals` reflects the change (the genome now appears if new_value=true and not already deployed, or disappears if new_value=false).

**GIVEN** the user is TRADER or VIEWER (not ADMIN)
**WHEN** they attempt the same endpoint call directly
**THEN** the server returns `403 Forbidden`. The button is not rendered in the UI for their role.

## Acceptance Criteria

### Backend — Empty-queue diagnostic

- [x] `list_approval_queue_for_user` in `src/agentfx/api/services/paper.py:59` extended to also return meta counts. Returns an `ApprovalQueueResponse` shape with:
  - `items` — the existing deployable rows.
  - `meta` — `{evolution_run_count, total_genomes, validated_count, passed_count, touched_count, top_close_to_passing}` (see implementation note re: singular→list drift).
  - `top_close_to_passing` — `list[ClosestGenome]` with `{genome_id, oos_sharpe, oos_max_drawdown, oos_n_trades, failure_summary, distance_score}`.
- [x] `top_close_to_passing` picks the top-N genomes ranked by `distance_score` among those with `validator_passed = false` in the user's evolution runs. `_top_close_to_passing` helper at `paper.py:165`. Empty list when 0 validated OR user has passing-untouched genomes.
- [x] Endpoint `GET /api/v1/paper-runs/approval-queue` returns the new shape.

### Backend — ADMIN override

- [x] Endpoint `POST /api/v1/strategy-genomes/{genome_id}/override-validator` at `src/agentfx/api/routers/evolution.py:559`.
- [x] Request body: `ValidatorOverrideRequest` at `src/agentfx/api/schemas/evolution.py:371` — `{new_value: bool, reason: str}` with `reason: str = Field(min_length=20, max_length=500)`.
- [x] Authorization: `_ensure_admin` at `evolution.py:559`. Non-ADMIN → 403.
- [x] Service function `override_validator_verdict` at `src/agentfx/api/services/evolution.py:811`:
  - Loads genome; 404 if missing.
  - Reads current `validator_passed` as `previous_value`.
  - Updates `validator_passed = new_value`.
  - Writes DecisionLog row with `USER_VALIDATOR_OVERRIDE` + previous/new/reason/actor metadata.
  - `db.commit()` inside a single transaction.
- [x] Response: `GenomePublic` schema.

### Frontend — Empty-queue banner

- [x] `portal/web/src/services/paperApi.ts::getApprovalQueue` return type updated to `ApprovalQueueResponse`.
- [x] `portal/web/src/pages/ApprovalQueue.tsx` replaces the generic empty state with `_pickEmptyBannerScenario` at line 168 — switches over `no_evolutions | none_validated | none_passed | all_deployed`.
- [x] `CloseToPassingTable` at `ApprovalQueue.tsx:304` renders per-gate metrics with threshold coloring; each row links to `/genomes/:id`.
- [x] Banner text is a strict function of the meta payload; no client-side heuristics.

### Frontend — ADMIN override button

- [x] `portal/web/src/pages/GenomeDetail.tsx:307-317` renders the "Override validator verdict" button, gated on `currentUser?.role === 'admin'`.
- [x] Modal at `portal/web/src/components/ValidatorOverrideModal.tsx`.
- [x] Modal fields: new-value dropdown, reason textarea with live counter; submit disabled until `_canSubmit(reason, submitting)` returns true (min 20 chars, trimmed).
- [x] On success, `onOverridden` invalidates the caller-side cache so the change reflects immediately.
- [x] Success/failure surfaced via `onOverridden` / `onError` callbacks (parent controls toast rendering).

### Tests

- [x] `tests/unit/services/test_approval_queue_meta.py` (backend meta calculation, table-driven).
- [x] `tests/integration/test_validator_override_endpoint.py` — ADMIN override, TRADER/VIEWER 403, reason < 20 chars 422, genome not found 404.
- [x] `portal/web/tests/unit/approvalQueueBanner.test.ts` — pure classifier locked (existing).
- [x] `portal/web/tests/unit/validatorOverrideModal.test.ts` (added 2026-07-19) — pure `_canSubmit` gate + `_buildOverridePayload` request-body shape locked. See "Implementation notes" above re: node-env constraint on full render tests.

### Docs

- [x] This file replaces the original `US-027-validator-writeback-fix.md`.
- [x] Backfill script from the original US (`scripts/backfill_validator_verdicts.py`) intentionally not written — nothing to backfill.
- [x] UAT runbook step reads "if the approval queue is empty and you need to unblock a walkthrough, use the ADMIN override button on the closest-to-passing genome" instead of "run raw SQL."

## Files Involved

Backend:
- `src/agentfx/api/services/paper.py` — extend `list_approval_queue_for_user` with meta
- `src/agentfx/api/services/evolution.py` — new `override_validator_verdict` service function
- `src/agentfx/api/routers/paper.py` — response model update on approval-queue endpoint
- `src/agentfx/api/routers/evolution.py` — new override endpoint
- `src/agentfx/api/schemas/paper.py` — `ApprovalQueueResponse`, `ApprovalQueueMeta`, `ClosestGenome`
- `src/agentfx/api/schemas/evolution.py` — `ValidatorOverrideRequest`

Frontend:
- `portal/web/src/services/paperApi.ts` — updated return type
- `portal/web/src/pages/ApprovalQueue.tsx` — empty-state switch
- `portal/web/src/pages/GenomeDetail.tsx` — ADMIN actions section
- `portal/web/src/components/ValidatorOverrideModal.tsx` (new)

Tests:
- `tests/unit/api/services/test_approval_queue_meta.py` (new)
- `tests/integration/test_validator_override_endpoint.py` (new)
- `portal/web/tests/components/ValidatorOverrideModal.test.tsx` (new)
- `portal/web/tests/pages/ApprovalQueue.test.tsx` (updated)

## Verification

1. `make test-unit` — meta calculation + Vitest for banner + modal passes.
2. `make test-integration` — override endpoint RBAC + DecisionLog audit trail passes.
3. **UAT manual verification (Wilson):**
   - Open `/paper/approvals` on UAT. See banner explaining "255 validated, 0 passed" + closest-to-passing summary card.
   - Navigate to the closest genome's detail page. See "Override validator verdict" in the Admin actions section.
   - Click, set `new_value = true`, reason = "unblock demo of paper trading UI", submit.
   - Approval queue now shows the overridden genome. Deploy it as normal.
   - Query `decision_logs`: a `USER_VALIDATOR_OVERRIDE` row exists with the previous/new value, reason, and Wilson's user_id.

## Not in scope

- Backfilling `validator_passed` on the 8,474 unvalidated genomes — that would require running the validator on every non-top-K genome, which defeats the design (top-K is a cost control). Deferred.
- Fixing the training-vs-OOS gap. That's v0.5.0's diagnostic-agent work.
- LLM-generated "why did this fail" explanation for the closest-to-passing card. Nice-to-have for v0.6.2.
