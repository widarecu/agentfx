# US-027: Validator Write-Back Fix

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P0 — Must Have (approval queue is empty without this)
**Status:** TODO

## Genesis

Wilson's 17 UAT evolution runs on 2026-07-08 produced multiple genomes whose `metrics_json` clearly clears every Gate B threshold (Sharpe ≥ 2.6, MDD ≤ 6.7%, hit ≥ 50.4%, n_trades ≥ 600) — yet every row had `validator_passed = NULL`. Result: `/paper/approvals` was permanently empty. The v0.6.0 smoke-test only proceeded because Wilson ran a manual `UPDATE strategy_genomes SET validator_passed = true WHERE id = '…'` against the UAT database. The validator agent computes the verdict but the write-back path is broken somewhere between `agents.validator` and the persisted row.

This US is safe to ship independently of US-024 — it unblocks the approval queue immediately without waiting for the runner rewrite. Land as PR-A on Day 1.

## Given-When-Then

**GIVEN** an evolution run E has completed at least one generation
  **AND** at least one genome in E has `metrics_json` clearing every Gate B threshold (Sharpe ≥ 1.0, MDD ≤ 10%, hit rate ≥ 48%, ≥ 300 trades in OOS)
**WHEN** the validator agent evaluates that genome
**THEN** the persisted `strategy_genomes` row has `validator_passed = true`, `validator_verdict_at = <utc_now>`, and `validator_verdict_metadata` populated with the four gate-check outcomes (which gates passed, actual metric values).

**GIVEN** a genome whose `metrics_json` fails one or more Gate B thresholds (e.g. Sharpe 0.5)
**WHEN** the validator agent evaluates that genome
**THEN** the persisted row has `validator_passed = false`, `validator_verdict_at` populated, and `validator_verdict_metadata.failure_reason` naming the failing gate(s).

**GIVEN** the fix is deployed to UAT
**WHEN** Wilson kicks off a fresh evolution
**THEN** validated genomes appear in `/paper/approvals` with zero manual DB writes.

## Acceptance Criteria

### Investigation (part of the PR)

- [ ] Audit the code path from `agentfx.agents.validator` → `agentfx.api.workers.evolution_runner` → the `strategy_genomes` row commit. Identify the exact point where the verdict is computed but not written. Suspected root cause: the validator returns a verdict object but the evolution runner reads only `metrics_json` and never invokes a second commit for `validator_passed`. Confirm the actual cause before fixing.
- [ ] Document the finding in the PR description (short — 3-5 sentences on where the drop happened).

### Schema (if needed)

- [ ] `strategy_genomes.validator_verdict_at` TIMESTAMPTZ NULLABLE — new column if not present.
- [ ] `strategy_genomes.validator_verdict_metadata` JSONB NULLABLE — new column if not present. Structure: `{"gate_b_thresholds": {…}, "actual_metrics": {…}, "passed_gates": [...], "failing_gates": [...], "failure_reason": "..."}`.
- [ ] `validator_passed` column already exists (from v0.4.0); left unchanged.

### Fix

- [ ] The write-back path is the smallest change that makes both directions (pass and fail) persist. Prefer editing `evolution_runner.py` to consume the validator's returned verdict object over rewriting the validator's return type, unless the validator contract is obviously the wrong shape.
- [ ] Idempotency: re-running the validator on a genome that already has a verdict should overwrite with the new one (evolutions can be re-validated after Gate B threshold changes).

### Tests

- [ ] `tests/integration/test_validator_writeback.py` (new) — three scenarios:
  1. **Passing genome**: seed a genome with `metrics_json = {"sharpe": 2.6, "max_drawdown": 0.067, "hit_rate": 0.504, "n_trades": 600}`. Run the validator agent. Assert the persisted row has `validator_passed = true`, `validator_verdict_at` non-null, `validator_verdict_metadata.passed_gates` lists all four gates.
  2. **Failing genome**: seed `metrics_json = {"sharpe": 0.5, "max_drawdown": 0.15, "hit_rate": 0.42, "n_trades": 200}`. Assert `validator_passed = false`, `failure_reason` names the failing gates.
  3. **Re-validation**: run scenario 1, then re-run the validator. Assert `validator_verdict_at` is updated (idempotent overwrite).
- [ ] `tests/unit/agents/test_validator.py` (update) — if the validator contract changes, unit tests need to reflect the new return type.
- [ ] Regress the existing evolution-runner integration test — ensure nothing else broke.

### Backfill (one-time, not part of the PR)

- [ ] Include a runbook step in `planning/runbooks/v0.6.1-uat-verification.md` for backfilling `validator_passed` on Wilson's existing 17 UAT evolutions. Script: `docker compose --profile uat exec backend-uat python scripts/backfill_validator_verdicts.py`. The script iterates all genomes with `validator_passed IS NULL` and re-runs the validator (which now writes back). Estimated runtime for 17 evolutions × ~50 genomes each: ~5 minutes.
- [ ] `scripts/backfill_validator_verdicts.py` (new) — the runbook backfill script.

## Files Involved

- `src/agentfx/agents/validator.py` — likely returns/writes the verdict (confirm during audit)
- `src/agentfx/api/workers/evolution_runner.py` — likely misses the write-back commit (confirm during audit)
- `src/agentfx/api/models/strategy.py` — `StrategyGenome` model; possibly add `validator_verdict_at` / `validator_verdict_metadata`
- `migrations/versions/2026_07_XX_validator_verdict_metadata.py` (new, if columns needed)
- `scripts/backfill_validator_verdicts.py` (new — one-time backfill for UAT)
- `tests/integration/test_validator_writeback.py` (new)
- `tests/unit/agents/test_validator.py` (update)
- `planning/runbooks/v0.6.1-uat-verification.md` (new — includes backfill step)

## Verification

1. `make test-unit` — validator unit tests pass with any contract change.
2. `make test-integration` — write-back test passes for both directions and idempotent overwrite.
3. **UAT manual verification:**
   - Run `scripts/backfill_validator_verdicts.py` on UAT. Confirm `SELECT COUNT(*) FROM strategy_genomes WHERE validator_passed IS NOT NULL` matches total genome count.
   - `/paper/approvals` shows genomes previously invisible.
   - Kick off a fresh evolution on UAT. When it completes, at least one genome ends `validator_passed = true` (assuming the evolution generates any Gate-B-passing candidates — if not, that's a strategy-fitness problem, not a write-back problem).
