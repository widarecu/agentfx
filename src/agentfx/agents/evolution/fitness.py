"""Fitness evaluation for evolved genomes.

A genome's fitness is its **training-window Sharpe** with five penalties
applied additively or multiplicatively. The training window is the
concatenation of walk-forward folds 0..K-2 (constructed by the
orchestrator in `_training_range()`); the final fold (K-1) is held out
as OOS and is *only* evaluated by the validator at the end of the run.
The GA never sees the OOS fold during evolution — that's the whole
point of the walk-forward setup.

(The earlier draft of this plan called it "OOS Sharpe with penalties";
the implementation correctly trains on the held-in folds and reserves
OOS for the validator's Gate-B verdict.)

Penalties, in order of application:

1. **Zero-trade hard penalty** (additive, -100): a genome that opens
   zero trades on the training window is degenerate (typically: long
   and short DSL conditions are identical, so neither side ever fires
   exclusive of the other). Without this hard floor, raw Sharpe = 0.0
   would beat any active losing strategy with Sharpe < 0, and the
   GA's elitism would lock in do-nothing genomes from gen 2 onward.

2. **Trade-shortfall penalty** (additive, scales 0 → -10): for genomes
   that *do* trade but produce fewer than Gate B's 300-trade minimum.
   Scale chosen so an active losing strategy with sharpe ~ -15 still
   ranks above a 100-trade strategy at Sharpe -5 (because trade count
   is a signal of strategy viability, not just final P&L).

3. **Drawdown breach penalty** (additive): if max_drawdown exceeds
   the Gate B 10% cap, subtract `(max_dd - 0.10) × 50`. A 30% DD
   strategy loses 10 points of fitness.

4. **Gate B dilution** (multiplicative, × 0.5): if *any* Gate B
   threshold fails AND fitness is currently positive, halve the
   fitness. The additive penalties (1–3) already handle the negative
   case, so this only fires for "passes most thresholds but not all"
   strategies that nonetheless cleared training.

5. **Diversity penalty** (multiplicative, × 0.5): if the genome is
   within edit-distance 2 of a current elite (a previously-evaluated
   genome in the top 10%), halve the fitness. Stops the population
   converging on a single archetype too quickly.

The fitness function delegates the actual backtest to an injectable
callable (`backtest_fn`) so unit tests can mock it without needing a
database. Production code passes the real `run_backtest_from_db`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import GATE_B_THRESHOLDS, evaluate_gate_b


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

# Zero-trade hard penalty: a strategy that produces literally zero trades
# isn't trading at all — it's a degenerate genome (typically: long and
# short DSL conditions are identical, or both never fire on the data). A
# proportional shortfall penalty isn't enough; a real losing strategy with
# 100 trades at sharpe=-15 gets penalty -22 (worse than a 0-trade genome
# at -10), so the GA's elitism would keep picking the do-nothing genome.
# Hard floor at -100 guarantees zero-trade genomes always lose tournaments.
# (See v0.3.0 acceptance run #6: this is the regression that uncovered it.)
ZERO_TRADE_HARD_PENALTY = 100.0

# Trade-count shortfall penalty: linear additive penalty for strategies
# that DO trade but produce fewer than Gate B's minimum (300 OOS). Scale
# is intentionally lower than the zero-trade penalty so an active losing
# strategy can still recover via mutation/crossover with more trades.
TRADE_SHORTFALL_PENALTY_SCALE = 10.0

# Drawdown breach penalty: additive, scales with how far over the cap.
# Per Gate B threshold (10%), a strategy at 30% drawdown gets -10.0 added.
DRAWDOWN_BREACH_PENALTY_SCALE = 50.0


def _apply_penalties(
    raw_fitness: float,
    metrics: dict,
    candidate: StrategySpec | None,
    elite_specs: list[StrategySpec] | None,
    diversity_threshold: int,
    gate_b_overrides: dict[str, float] | None = None,
) -> float:
    """Apply Gate B and diversity penalties to a raw fitness value.

    Penalty order (additive, then multiplicative):
      1. Trade-count shortfall: additive penalty that scales 0 → -10 as
         n_trades drops from the Gate B minimum to 0. This is what keeps
         zero-trade degenerate genomes from winning tournaments — without
         it, raw_sharpe=0 beats real-but-losing strategies.
      2. Drawdown breach: additive penalty proportional to how far over
         the Gate B max_drawdown the strategy went.
      3. Gate B dilution: halves *positive* fitness when Gate B fails.
         The additive penalties above already handle the negative case.
      4. Diversity penalty: halves fitness for genomes too close to an
         elite, preventing premature convergence.
    """
    fitness = raw_fitness

    # 1a. Zero-trade hard penalty — for genuinely degenerate genomes that
    # never open a position. Without this hard floor, the proportional
    # shortfall penalty alone lets do-nothing genomes beat active losers.
    n_trades = metrics.get("n_trades", 0) or 0
    if n_trades == 0:
        fitness -= ZERO_TRADE_HARD_PENALTY
    else:
        # 1b. Proportional shortfall — for strategies that DO trade but
        # come up short of Gate B's minimum.
        min_trades = GATE_B_THRESHOLDS["min_trades"]
        if n_trades < min_trades:
            shortfall_ratio = (min_trades - n_trades) / min_trades
            fitness -= shortfall_ratio * TRADE_SHORTFALL_PENALTY_SCALE

    # 2. Drawdown breach — additive, proportional to over-cap excess.
    max_dd = metrics.get("max_drawdown", 0) or 0
    if max_dd > GATE_B_THRESHOLDS["max_drawdown"]:
        breach = max_dd - GATE_B_THRESHOLDS["max_drawdown"]
        fitness -= breach * DRAWDOWN_BREACH_PENALTY_SCALE

    # 3. Gate B dilution: halve positive fitness if any threshold breached.
    passes_gate_b, _ = evaluate_gate_b(metrics, overrides=gate_b_overrides)
    if not passes_gate_b and fitness > 0:
        fitness = fitness * GATE_B_FAILURE_DILUTION

    # 4. Diversity penalty: halve fitness if too close to an elite.
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
    gate_b_overrides: dict[str, float] | None = None,
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
        gate_b_overrides=gate_b_overrides,
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
