"""Fitness evaluation for evolved genomes.

A genome's fitness is its OOS Sharpe with two penalties:
- **Gate B dilution**: a genome that fails Gate B has its positive fitness
  halved (negative fitness untouched). The point is to *deprioritise*
  Gate-B failures in tournament selection without eliminating them
  entirely — they may still contribute useful crossover material.
- **Diversity penalty**: if the genome is structurally near an elite, its
  fitness is reduced by 50%. This stops the population converging onto a
  single archetype too quickly.

The fitness function delegates the actual backtest to an injectable
callable (`backtest_fn`) so unit tests can mock it without needing a
database. Production code passes the real `run_backtest_from_db`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from agentfx.api.models.optimization import StrategyType  # noqa: F401  (re-export safety)
from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import evaluate_gate_b


class BacktestFn(Protocol):
    """Shape of the function fitness depends on. Defaults to
    `agentfx.services.backtest.run_backtest_from_db`."""

    def __call__(
        self,
        instrument: str,
        granularity: str,
        start: datetime | None,
        end: datetime | None,
        strategy_name: str,
        strategy_params: dict | None,
        **extra: Any,
    ) -> dict[str, Any]: ...


# ─────────────────────────────────────────────────────────────────────────
# Edit distance / diversity
# ─────────────────────────────────────────────────────────────────────────


def _count_differing_leaves(a: Any, b: Any) -> int:
    """Recursive crude edit distance over JSON-shaped trees.

    Approximation: count of differing scalar leaves and structural
    mismatches. Sufficient for a "are these two specs near-identical"
    heuristic; not a precise tree-edit distance.
    """
    if type(a) is not type(b):
        return 1
    if isinstance(a, dict):
        all_keys = set(a) | set(b)
        return sum(_count_differing_leaves(a.get(k), b.get(k)) for k in all_keys)
    if isinstance(a, list):
        if len(a) != len(b):
            # Treat any length difference as one diff per missing slot.
            return abs(len(a) - len(b)) + sum(
                _count_differing_leaves(av, bv) for av, bv in zip(a, b)
            )
        return sum(_count_differing_leaves(av, bv) for av, bv in zip(a, b))
    return 0 if a == b else 1


def spec_edit_distance(a: StrategySpec, b: StrategySpec) -> int:
    """Crude edit distance between two strategy specs.

    Used by the diversity penalty to detect when a candidate genome is
    near-identical to an elite. 0 = structurally identical; small
    integers = a few differing leaves.
    """
    return _count_differing_leaves(a.model_dump(mode="json"), b.model_dump(mode="json"))


def is_near_elite(
    candidate: StrategySpec,
    elite_specs: list[StrategySpec],
    threshold: int = 2,
) -> bool:
    """True if `candidate` is within `threshold` edit-distance of any elite."""
    if not elite_specs:
        return False
    return any(
        spec_edit_distance(candidate, elite) < threshold for elite in elite_specs
    )


# ─────────────────────────────────────────────────────────────────────────
# Fitness scoring
# ─────────────────────────────────────────────────────────────────────────


GATE_B_FAILURE_DILUTION = 0.5
DIVERSITY_PENALTY_FACTOR = 0.5


def _apply_penalties(
    raw_fitness: float,
    metrics: dict,
    candidate: StrategySpec | None,
    elite_specs: list[StrategySpec] | None,
    diversity_threshold: int,
) -> float:
    """Apply Gate B and diversity penalties to a raw fitness value."""
    fitness = raw_fitness

    # Gate B dilution: halve positive fitness, leave negative alone.
    passes_gate_b, _ = evaluate_gate_b(metrics)
    if not passes_gate_b and fitness > 0:
        fitness = fitness * GATE_B_FAILURE_DILUTION

    # Diversity penalty: halve fitness if too close to an elite.
    if (
        candidate is not None
        and elite_specs
        and is_near_elite(candidate, elite_specs, threshold=diversity_threshold)
    ):
        fitness = fitness * DIVERSITY_PENALTY_FACTOR

    return fitness


def compute_fitness(
    spec: StrategySpec,
    *,
    instrument: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
    backtest_fn: BacktestFn | None = None,
    elite_specs: list[StrategySpec] | None = None,
    diversity_threshold: int = 2,
    **backtest_kwargs: Any,
) -> tuple[float, dict[str, Any]]:
    """Run the backtest for a genome and compute its fitness.

    Args:
        spec: The strategy genome to evaluate.
        instrument: Currency pair (e.g. "EUR_USD").
        granularity: Candle timeframe (default "M1").
        start, end: Backtest window (None means "all available data").
        backtest_fn: Override the default backtest call (injection point
            for unit tests). Defaults to `run_backtest_from_db`.
        elite_specs: Current elite genomes' specs. Used for diversity
            penalty. Pass an empty list or None to skip the penalty.
        diversity_threshold: Edit-distance below which a candidate is
            considered "near an elite". Default 2.
        **backtest_kwargs: Forwarded to the backtest function (e.g.
            account_balance, position_size, spread_pips).

    Returns:
        (fitness_value, metrics_dict). The metrics dict is the raw output
        from the backtest function — preserved verbatim so the caller can
        record it on the genome row for the portal.
    """
    if backtest_fn is None:
        # Lazy import to avoid pulling DB layer at module import time —
        # important for unit tests of the fitness function itself.
        from agentfx.services.backtest import run_backtest_from_db

        backtest_fn = run_backtest_from_db

    result = backtest_fn(
        instrument=instrument,
        granularity=granularity,
        start=start,
        end=end,
        strategy_name="dsl",
        strategy_params={"spec": spec.model_dump(mode="json")},
        **backtest_kwargs,
    )

    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    raw_sharpe = metrics.get("sharpe")
    if raw_sharpe is None:
        raw_sharpe = float("-inf")

    fitness = _apply_penalties(
        raw_fitness=float(raw_sharpe),
        metrics=metrics,
        candidate=spec,
        elite_specs=elite_specs,
        diversity_threshold=diversity_threshold,
    )

    return fitness, metrics


__all__ = [
    "BacktestFn",
    "DIVERSITY_PENALTY_FACTOR",
    "GATE_B_FAILURE_DILUTION",
    "compute_fitness",
    "is_near_elite",
    "spec_edit_distance",
]
