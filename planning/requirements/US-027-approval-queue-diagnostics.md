# US-027: Approval Queue Diagnostics + ADMIN Validator Override

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P1 — Should Have (turns "queue is empty" from mystery into diagnostic; legitimises the manual-SQL demo unblock)
**Status:** TODO

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

- [ ] `list_approval_queue_for_user` in `src/agentfx/api/services/paper.py` extended to also return meta counts. Returns an `ApprovalQueueResponse` shape with:
  - `items` — the existing deployable rows.
  - `meta` — `{evolution_run_count, total_genomes, validated_count, passed_count, touched_count, closest_to_passing}`.
  - `closest_to_passing` — `{genome_id, oos_sharpe, oos_max_drawdown, oos_n_trades, failure_summary}` or `None`.
- [ ] `closest_to_passing` picks the genome with the highest median-across-OOS-folds Sharpe among those that have `validator_passed = false` and belong to the user's evolution runs. Ties broken by fewest failing gates. `None` when 0 validated OR user has passing-untouched genomes.
- [ ] Endpoint `GET /api/v1/paper-runs/approval-queue` returns the new shape. Router unchanged apart from response model.

### Backend — ADMIN override

- [ ] New endpoint `POST /api/v1/strategy-genomes/{genome_id}/override-validator` in `src/agentfx/api/routers/evolution.py`.
- [ ] Request body: `{new_value: bool, reason: str}` (Pydantic model with `reason: str = Field(min_length=20, max_length=500)`).
- [ ] Authorization: `require_role(ADMIN)`. Non-ADMIN → 403.
- [ ] Service function `override_validator_verdict` in `src/agentfx/api/services/evolution.py`:
  - Loads genome; 404 if missing.
  - Reads current `validator_passed` as `previous_value`.
  - Updates `validator_passed = new_value`.
  - Writes DecisionLog row with the action/metadata specified above.
  - `db.commit()` inside a single transaction.
- [ ] Response: `GenomePublic` schema (existing) with the updated row.

### Frontend — Empty-queue banner

- [ ] `portal/web/src/services/paperApi.ts::getApprovalQueue` return type updated to `ApprovalQueueResponse`.
- [ ] `portal/web/src/pages/ApprovalQueue.tsx` — replace the generic empty state with a switch over the four scenarios above. Uses the meta shape to pick which banner to render.
- [ ] "Closest to passing" card links to `/genomes/:id` so the user can inspect the near-miss directly.
- [ ] Banner text is a strict function of the meta; no client-side heuristics.

### Frontend — ADMIN override button

- [ ] `portal/web/src/pages/GenomeDetail.tsx` — new "Admin actions" section (rendered only when `currentUser.role === 'ADMIN'`; reuse the `RequireRole` HOC pattern from v0.6.0).
- [ ] "Override validator verdict" button opens a modal (`portal/web/src/components/ValidatorOverrideModal.tsx`, new).
- [ ] Modal fields: new-value dropdown, reason textarea (min 20 chars, live counter). Submit disabled until reason valid.
- [ ] On success, invalidates the query cache for the genome + approval queue so the change reflects immediately.
- [ ] Uses the standard toast pattern for success ("Validator verdict updated") and failure ("Only ADMIN can override validator" — expected only if the frontend gate is bypassed).

### Tests

- [ ] `tests/unit/api/services/test_approval_queue_meta.py` (new) — table-driven scenarios: 0 evolutions, evolutions with 0 validated, validated-but-none-pass, some-pass-user-touched-all, some-pass-untouched-mix. Assert meta shape + `closest_to_passing` selection.
- [ ] `tests/integration/test_validator_override_endpoint.py` (new):
  - ADMIN successfully overrides `false → true`, DecisionLog row written with correct `previous_value`, `new_value`, `reason`.
  - TRADER attempting override → 403.
  - VIEWER attempting override → 403.
  - Reason < 20 chars → 422.
  - Genome not found → 404.
- [ ] `portal/web/tests/components/ValidatorOverrideModal.test.tsx` (new) — form validation, submit disabled state, success/failure toast rendering.
- [ ] `portal/web/tests/pages/ApprovalQueue.test.tsx` (updated) — mounts with each of the four meta scenarios, asserts correct banner rendered.

### Docs

- [ ] This file replaces the original `US-027-validator-writeback-fix.md`.
- [ ] Backfill script from the original US (`scripts/backfill_validator_verdicts.py`) is **not** written — nothing to backfill.
- [ ] UAT runbook step for smoke-test now reads "if the approval queue is empty and you need to unblock a walkthrough, use the ADMIN override button on the closest-to-passing genome" instead of "run raw SQL."

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
