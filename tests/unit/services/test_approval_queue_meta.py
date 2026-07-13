"""Unit tests for `list_approval_queue_for_user` meta computation (US-027).

The service-layer function was extended in v0.6.1 to return
diagnostic counts alongside the deployable rows. These tests lock the
five scenarios the frontend banner switches over:

1. Zero evolutions for the user.
2. Evolutions completed but 0 genomes validated.
3. Validated genomes exist but 0 passed Gate B (Wilson's UAT reality
   at 2026-07-08 — the finding that prompted the reshape).
4. Passing genomes exist AND the user has deployed them all.
5. Mix: some passing untouched, some already touched.

The `closest_to_passing` selection is table-driven within scenario 3.

Uses an in-memory SQLite session — same fixture pattern as
`test_evolution_service.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from agentfx.api import models
from agentfx.api.core.database import Base
from agentfx.api.services import paper as paper_service


pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _enable_fks(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


def _make_user(db: Session, email: str = "wilson@example.com") -> models.User:
    u = models.User(
        email=email,
        hashed_password="stub",
        role="trader",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_run(db: Session, user_id: int, evo_id: str) -> models.EvolutionRun:
    evo = models.EvolutionRun(
        id=evo_id,
        status=models.EvolutionRunStatus.COMPLETED,
        instrument="EUR_USD",
        granularity="M1",
        config_json={},
        stop_conditions_json={},
        current_generation=1,
        llm_cost_usd=0,
        created_by=user_id,
    )
    db.add(evo)
    db.commit()
    db.refresh(evo)
    return evo


def _make_genome(
    db: Session,
    evo_id: str,
    idx: int,
    *,
    validator_passed: bool | None,
    validation_failures: list[str] | None = None,
) -> models.StrategyGenome:
    gen_id = f"gen_{evo_id}_{idx}"
    if (
        db.query(models.Generation).filter(models.Generation.id == gen_id).first()
        is None
    ):
        db.add(models.Generation(id=gen_id, run_id=evo_id, index=idx))
        db.commit()
    genome = models.StrategyGenome(
        id=f"genome_{gen_id}_0",
        generation_id=gen_id,
        dsl_json={"stub": True},
        parent_ids=[],
        origin=models.GenomeOrigin.SEED,
        validator_passed=validator_passed,
        validation_failures=validation_failures or [],
        fitness=1.5,
        metrics_json={"sharpe": 1.5},
    )
    db.add(genome)
    db.commit()
    db.refresh(genome)
    return genome


def _make_oos_fold(
    db: Session,
    genome_id: str,
    *,
    fold_index: int,
    sharpe: float,
    max_drawdown: float = -0.05,
    n_trades: int = 100,
    passed_gate_b: bool = False,
) -> None:
    fold = models.ValidationFold(
        id=f"fold_{genome_id}_{fold_index}",
        genome_id=genome_id,
        fold_index=fold_index,
        fold_start=datetime(2026, 1, 1, tzinfo=UTC),
        fold_end=datetime(2026, 2, 1, tzinfo=UTC),
        is_oos=True,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        hit_rate=0.5,
        n_trades=n_trades,
        cagr=0.05,
        passed_gate_b=passed_gate_b,
    )
    db.add(fold)
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────


def test_meta_zero_evolutions(db):
    """No evolutions → all counts zero, closest is None."""
    user = _make_user(db)

    result = paper_service.list_approval_queue_for_user(db, user.id)

    assert result.items == []
    assert result.meta.evolution_run_count == 0
    assert result.meta.total_genomes == 0
    assert result.meta.validated_count == 0
    assert result.meta.passed_count == 0
    assert result.meta.touched_count == 0
    assert result.meta.top_close_to_passing == []


def test_meta_evolutions_but_none_validated(db):
    """Evolutions produced genomes but validator never ran on any of
    them (all `validator_passed IS NULL`). Rare — indicates a run
    crashed mid-loop before reaching validation."""
    user = _make_user(db)
    evo = _make_run(db, user.id, "evorun_a")
    _make_genome(db, evo.id, 1, validator_passed=None)
    _make_genome(db, evo.id, 2, validator_passed=None)

    result = paper_service.list_approval_queue_for_user(db, user.id)

    assert result.items == []
    assert result.meta.evolution_run_count == 1
    assert result.meta.total_genomes == 2
    assert result.meta.validated_count == 0
    assert result.meta.passed_count == 0
    assert result.meta.top_close_to_passing == []  # nothing to compare


def test_meta_validated_but_none_passed_ranks_by_distance(db):
    """Wilson's UAT reality — validator ran and correctly wrote `false`
    for every genome. The top-N list is ordered by aggregate distance
    to passing (smaller = closer), NOT by max median OOS Sharpe.

    Regression against the pre-v0.6.1-UAT ranking that Wilson called
    out as noisy: a genome with one fluky-high-Sharpe fold could
    beat a genome with more, stabler OOS folds because the old
    heuristic ignored trade counts. Distance-to-passing weights
    every gate deficit equally, so low trade counts drag the score.
    """
    user = _make_user(db)
    evo = _make_run(db, user.id, "evorun_b")

    # "Poor" — Sharpe 0.2 avg, low trades. Fails everything.
    poor = _make_genome(
        db,
        evo.id,
        1,
        validator_passed=False,
        validation_failures=["Sharpe 0.2 < 1.0"],
    )
    _make_oos_fold(db, poor.id, fold_index=0, sharpe=0.2, n_trades=100)
    _make_oos_fold(db, poor.id, fold_index=1, sharpe=0.1, n_trades=100)

    # "Close" — Sharpe 0.82, 292 trades. Fails Sharpe + Trades but by
    # small margins → smallest distance score.
    close = _make_genome(
        db,
        evo.id,
        2,
        validator_passed=False,
        validation_failures=["Sharpe 0.82 < 1.0", "Trades 292 < 300"],
    )
    _make_oos_fold(db, close.id, fold_index=0, sharpe=0.9, n_trades=140)
    _make_oos_fold(db, close.id, fold_index=1, sharpe=0.74, n_trades=152)

    # "Fluky" — one super-high-Sharpe fold on 7 trades. Under the old
    # heuristic this would win because max median Sharpe. Under
    # distance-to-passing, the tiny trade count pushes it well below
    # `close`.
    fluky = _make_genome(
        db,
        evo.id,
        3,
        validator_passed=False,
        validation_failures=["Trades 7 < 300"],
    )
    _make_oos_fold(db, fluky.id, fold_index=0, sharpe=3.0, n_trades=7)

    result = paper_service.list_approval_queue_for_user(db, user.id)

    assert result.items == []
    assert result.meta.validated_count == 3
    assert result.meta.passed_count == 0
    rows = result.meta.top_close_to_passing
    assert len(rows) == 3
    # `close` should rank #1 despite `fluky` having higher median Sharpe.
    assert rows[0].genome_id == close.id
    # `close` metrics: median Sharpe 0.82, 292 total trades.
    assert rows[0].oos_sharpe == pytest.approx(0.82)
    assert rows[0].oos_n_trades == 292
    assert rows[0].evolution_run_id == "evorun_b"
    # Distance score is deterministic: (1 - 0.82)/1 + (300 - 292)/300
    #                               = 0.18 + 0.02666... = 0.20666...
    assert rows[0].distance_score == pytest.approx(0.20666, abs=0.001)
    assert rows[0].gates_cleared == 2  # Passes MDD (0%) + Hit (0.5)

    # `fluky` ranks MIDDLE despite its 3.0 median Sharpe — the
    # 7-trade sample kills its distance score to 0.977. Under the
    # OLD "max median Sharpe" heuristic fluky would have won.
    assert rows[1].genome_id == fluky.id
    assert rows[1].gates_cleared == 3  # Only trades fails
    # `poor` ranks LAST — worst on both Sharpe (0.15 vs 1.0) and trades
    # (200 vs 300). Distance score ≈ 0.85 + 0.333 = 1.183.
    assert rows[-1].genome_id == poor.id
    # Distance strictly increases across the list — deterministic
    # ranking guarantee.
    assert rows[0].distance_score <= rows[1].distance_score <= rows[-1].distance_score

    # Table stays capped at 5 (only 3 genomes here, but bound is <=5).
    assert len(rows) <= 5


def test_meta_all_passing_deployed_returns_empty_but_marks_touched(db):
    """User has deployed every passing genome → items empty, but
    passed_count and touched_count reflect the history so the banner
    shows the 'you've deployed them all' variant."""
    user = _make_user(db)
    evo = _make_run(db, user.id, "evorun_c")
    genome = _make_genome(db, evo.id, 1, validator_passed=True)

    # Simulate a deployed paper run for this genome.
    run = models.Run(
        id="run_paper_1",
        strategy_id=None,
        strategy_genome_id=genome.id,
        pair="EUR_USD",
        environment=models.RunEnvironment.PAPER,
        status=models.RunStatus.HALTED_MANUAL,
        parameters={},
        initial_balance=10_000.0,
        user_id=user.id,
    )
    db.add(run)
    db.commit()

    result = paper_service.list_approval_queue_for_user(db, user.id)

    assert result.items == []
    assert result.meta.passed_count == 1
    assert result.meta.touched_count == 1
    # top_close_to_passing suppressed when passing genomes exist
    assert result.meta.top_close_to_passing == []


def test_meta_mixed_passing_untouched_and_touched(db):
    """A passing untouched genome is deployable; a passing touched one
    contributes to `touched_count` but not to `items`. Closest-to-
    passing suppressed because deployables exist."""
    user = _make_user(db)
    evo = _make_run(db, user.id, "evorun_d")
    untouched = _make_genome(db, evo.id, 1, validator_passed=True)
    touched = _make_genome(db, evo.id, 2, validator_passed=True)

    run = models.Run(
        id="run_paper_touched",
        strategy_id=None,
        strategy_genome_id=touched.id,
        pair="EUR_USD",
        environment=models.RunEnvironment.PAPER,
        status=models.RunStatus.COMPLETED,
        parameters={},
        initial_balance=10_000.0,
        user_id=user.id,
    )
    db.add(run)
    db.commit()

    result = paper_service.list_approval_queue_for_user(db, user.id)

    assert len(result.items) == 1
    assert result.items[0].id == untouched.id
    assert result.meta.passed_count == 2
    assert result.meta.touched_count == 1
    assert result.meta.top_close_to_passing == []


def test_meta_ignores_other_users_evolutions(db):
    """Cross-user RBAC — user B's evolution runs don't inflate user A's
    counts. Regress guard against the v0.6.0 leak that motivated
    US-018."""
    alice = _make_user(db, email="alice@example.com")
    bob = _make_user(db, email="bob@example.com")

    bob_evo = _make_run(db, bob.id, "evorun_bob")
    _make_genome(db, bob_evo.id, 1, validator_passed=True)

    result = paper_service.list_approval_queue_for_user(db, alice.id)

    assert result.meta.evolution_run_count == 0
    assert result.meta.total_genomes == 0
    assert result.meta.passed_count == 0
