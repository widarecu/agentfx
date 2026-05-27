# US-004: Walk-Forward Validation with Held-Out OOS Fold

**Version:** v0.3.0 - Autonomous Strategy Loop
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** an evolution run has completed all generations and the GA has selected the top-K genomes by in-sample fitness
  **AND** the run was configured with K=4 walk-forward folds and a held-out final fold the GA never saw
**WHEN** the orchestrator invokes the Validator agent on each top-K genome
**THEN** the validator:
1. Splits the loaded historical window into K=4 contiguous time-ordered folds (no random shuffling — order matters in time series)
2. Runs the genome on the held-out final fold ONLY (the OOS test the GA never saw)
3. Records per-fold metrics (Sharpe, max DD, hit rate, n_trades) on `validation_fold` rows
4. Applies Gate B (Sharpe ≥ 1.0, MDD ≤ 10%, Hit Rate ≥ 48%, ≥ 300 trades) to the OOS fold
5. Marks the genome `validator_passed=true` only if Gate B holds on OOS
6. Surfaces failure reasons (specific Gate B thresholds breached) on the genome row for the portal to render

## Acceptance Criteria

- [ ] `ValidatorAgent` replaces the 147-line stub at [src/agentfx/agents/validator.py](../../src/agentfx/agents/validator.py)
- [ ] Walk-forward fold splits are *contiguous and time-ordered* — not k-fold with shuffling. Helper function `make_walk_forward_folds(start, end, k=4) -> list[(start, end)]` in `core/validation.py` is testable in isolation
- [ ] **The final fold is withheld from the GA's fitness evaluation entirely** — this is the integrity guarantee that prevents OOS contamination. Validator reads from this fold only.
- [ ] Gate B logic moved from [src/agentfx/agents/optimizer.py:33-66](../../src/agentfx/agents/optimizer.py#L33-L66) to `src/agentfx/core/gates.py` and reused by both Validator and (still) the legacy optimizer
- [ ] Validator does not re-evolve, optimize, or mutate — it ONLY judges
- [ ] `validation_fold` table populated with one row per fold per genome (4 folds × top-K genomes per run)
- [ ] `strategy_genome.validator_passed` set to true only if OOS fold passes all four Gate B checks
- [ ] `strategy_genome.validation_failures` (jsonb) stores ordered list of failure messages e.g. `["Sharpe 0.82 < 1.0", "Trade count 245 < 300"]` for portal rendering

## Walk-Forward Design

```
historical window:  [=================================================]
                    |_________ training span (folds 1..3) _________|__OOS__|
                                                                    ^
                                                          ↑ GA only sees this far
                                                          ↑ Validator runs here only

Fold split (K=4):
  fold 1:  [=========                                    ]   train
  fold 2:  [          =========                          ]   train
  fold 3:  [                    =========                ]   train
  fold 4:  [                              =================] OOS / validation
```

The GA's fitness evaluator (US-003) operates on folds 1..3 only. The Validator's evaluation operates on fold 4 only. There is no overlap and no leakage.

## Gate B Thresholds (unchanged from v0.2.0)

| Metric | Threshold |
|---|---|
| Sharpe ratio | ≥ 1.0 |
| Max drawdown | ≤ 10% |
| Hit rate | ≥ 48% |
| Trade count (OOS) | ≥ 300 |

Gate B is enforced (not informational) in v0.3.0 — a genome that fails any threshold is marked `validator_passed=false` and is *not* surfaced for human approval. (In v0.2.0 Gate B was informational only.)

## Database Schema (v0.3.0 additions)

```sql
CREATE TABLE validation_fold (
    id              UUID PRIMARY KEY,
    genome_id       UUID REFERENCES strategy_genome(id) ON DELETE CASCADE,
    fold_index      INT NOT NULL,            -- 0..k-1
    fold_start      TIMESTAMPTZ NOT NULL,
    fold_end        TIMESTAMPTZ NOT NULL,
    is_oos          BOOLEAN NOT NULL,        -- true only for the held-out fold
    sharpe          FLOAT,
    max_drawdown    FLOAT,
    hit_rate        FLOAT,
    n_trades        INT,
    passed_gate_b   BOOLEAN NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_validation_fold_genome ON validation_fold(genome_id);
```

The full new-tables set (`evolution_run`, `generation`, `strategy_genome`, `validation_fold`, `decision_log`) is documented under US-003 and US-016.

## Files Involved

### Create
- `src/agentfx/core/gates.py` — `evaluate_gate_b(metrics) -> (bool, list[str])` (lifted from optimizer.py)
- `src/agentfx/core/validation.py` — `make_walk_forward_folds(start, end, k) -> list[Fold]`
- `tests/unit/core/test_gates.py`
- `tests/unit/core/test_validation.py`
- `tests/unit/agents/test_validator.py`
- `tests/integration/evolution/test_validator_walk_forward.py` — runs validator on real OANDA candles for EUR_USD

### Modify
- `src/agentfx/agents/validator.py` — replace stub with real walk-forward validator
- `src/agentfx/agents/optimizer.py` — import `evaluate_gate_b` from `core/gates.py` instead of defining locally; ensure no behaviour change

## Verification Commands

```bash
# Unit tests
make test-file FILE=tests/unit/core/test_gates.py
make test-file FILE=tests/unit/core/test_validation.py
make test-file FILE=tests/unit/agents/test_validator.py

# Integration on real OANDA candles
make test-file FILE=tests/integration/evolution/test_validator_walk_forward.py
```

## Dependencies

- US-006 (Strategy DSL) — validator runs compiled DSL genomes
- US-003 (Evolution Run Lifecycle) — validator is invoked by the orchestrator, not directly
- Existing `run_backtest_from_db()` at [src/agentfx/services/backtest.py:269](../../src/agentfx/services/backtest.py)

## Notes

- The withheld-final-fold guarantee is the most important integrity property in v0.3.0. Any code path that lets the GA see fold-4 data (even indirectly via cached intermediate calculations) is a bug, not a feature. Tests must verify this.
- Walk-forward splits are time-ordered and contiguous because shuffled k-fold is wrong for time series — it leaks future information into training.
- Keeping `optimizer.py` consuming `evaluate_gate_b` from the new shared module ensures v0.2.0 behaviour is unchanged and the v0.2.0 optimization-job test suite still passes after the move.
- Validator runtime is typically << GA runtime because it only runs each genome once on one fold (plus K-1 in-sample folds for record-keeping). The dominant cost is the GA, not validation.
