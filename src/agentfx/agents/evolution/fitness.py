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

import math
from datetime import datetime
from typing import Any, Protocol

from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import GATE_B_THRESHOLDS, evaluate_gate_b

# Penalty weight applied to the std-dev of per-fold Sharpe when the GA
# evaluates a genome on multiple training folds. With λ=0.5 a strategy
# whose Sharpes spread by σ=1.0 across folds loses 0.5 of its mean
# Sharpe as fitness — heavy enough to push the GA away from one-fold-
# wonder strategies (which look great on average but collapse OOS),
# moderate enough that genuinely-good-but-imperfect strategies still
# rank ahead of consistently-mediocre ones. Tunable: λ=0 reverts to
# pure-mean (no consistency bonus), λ=1.0 is much stricter (closer to
# min-of-folds behaviour). 0.5 is the starting heuristic; revisit
# after seeing a few real runs.
FOLD_CONSISTENCY_LAMBDA_DEFAULT = 0.5


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
) -> tuple[float, dict[str, Any]]:
    """Apply Gate B and diversity penalties to a raw fitness value.

    Returns ``(final_fitness, breakdown)`` where ``breakdown`` is a dict
    with the per-step deductions so the caller can show the user
    exactly what got applied. The portal renders this as a chain:
    raw → zero-trade → shortfall → drawdown → ×gate_b → ×diversity →
    final. Zero-cost (the values are computed anyway) — we just stopped
    throwing them away.

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
    breakdown: dict[str, Any] = {
        "raw_fitness": float(raw_fitness),
        "zero_trade_penalty": 0.0,
        "trade_shortfall_penalty": 0.0,
        "drawdown_breach_penalty": 0.0,
        "gate_b_dilution_factor": 1.0,
        "diversity_penalty_factor": 1.0,
    }

    # 1a. Zero-trade hard penalty — for genuinely degenerate genomes that
    # never open a position. Without this hard floor, the proportional
    # shortfall penalty alone lets do-nothing genomes beat active losers.
    n_trades = metrics.get("n_trades", 0) or 0
    if n_trades == 0:
        fitness -= ZERO_TRADE_HARD_PENALTY
        breakdown["zero_trade_penalty"] = -float(ZERO_TRADE_HARD_PENALTY)
    else:
        # 1b. Proportional shortfall — for strategies that DO trade but
        # come up short of Gate B's minimum.
        min_trades = GATE_B_THRESHOLDS["min_trades"]
        if n_trades < min_trades:
            shortfall_ratio = (min_trades - n_trades) / min_trades
            deduction = shortfall_ratio * TRADE_SHORTFALL_PENALTY_SCALE
            fitness -= deduction
            breakdown["trade_shortfall_penalty"] = -float(deduction)

    # 2. Drawdown breach — additive, proportional to over-cap excess.
    max_dd = metrics.get("max_drawdown", 0) or 0
    if max_dd > GATE_B_THRESHOLDS["max_drawdown"]:
        breach = max_dd - GATE_B_THRESHOLDS["max_drawdown"]
        deduction = breach * DRAWDOWN_BREACH_PENALTY_SCALE
        fitness -= deduction
        breakdown["drawdown_breach_penalty"] = -float(deduction)

    # 3. Gate B dilution: halve positive fitness if any threshold breached.
    passes_gate_b, _ = evaluate_gate_b(metrics, overrides=gate_b_overrides)
    if not passes_gate_b and fitness > 0:
        fitness = fitness * GATE_B_FAILURE_DILUTION
        breakdown["gate_b_dilution_factor"] = float(GATE_B_FAILURE_DILUTION)

    # 4. Diversity penalty: halve fitness if too close to an elite.
    if (
        candidate is not None
        and elite_specs
        and is_near_elite(candidate, elite_specs, threshold=diversity_threshold)
    ):
        fitness = fitness * DIVERSITY_PENALTY_FACTOR
        breakdown["diversity_penalty_factor"] = float(DIVERSITY_PENALTY_FACTOR)

    breakdown["final_fitness"] = float(fitness)
    return fitness, breakdown


def _aggregate_fold_metrics(
    per_fold_metrics: list[dict[str, Any]],
    *,
    fold_consistency_lambda: float,
) -> tuple[float, dict[str, Any]]:
    """Combine per-fold backtest metrics into one (raw_sharpe, aggregated_metrics)
    pair that the existing penalty stack can score.

    The raw Sharpe used for fitness is **`mean(fold_sharpes) − λ × std(fold_sharpes)`**.
    Per-fold Sharpe variance is a direct proxy for "regime-fragile"
    strategies — the ones that look fine on training average but
    collapse OOS because their wins came from one good fold. The
    variance penalty pushes the GA away from those without taking the
    extreme step of `min(folds)` (which can be so strict that nothing
    ever clears).

    Other Gate-B-relevant fields are aggregated for the penalty
    function as follows:
      - ``max_drawdown``: **worst** (largest-magnitude) per-fold value —
        if any fold has a 30% DD, the strategy is risky overall, regardless
        of the other folds.
      - ``hit_rate``: **mean** across folds.
      - ``n_trades``: **sum** across folds (total training trades).
      - ``cagr``: **mean** across folds.

    The returned `aggregated_metrics["per_fold_sharpe"]` and
    `["fold_consistency_std"]` are passed through for visibility in the
    portal (no schema change yet — they ride on the existing metrics
    dict).
    """
    if not per_fold_metrics:
        return float("-inf"), {}

    def _f(field: str, default: float = 0.0) -> list[float]:
        return [
            float(m.get(field, default)) if m.get(field) is not None else default
            for m in per_fold_metrics
        ]

    sharpes = _f("sharpe", default=float("-inf"))
    # If ANY fold completely failed to produce metrics (e.g. -inf
    # Sharpe), keep that signal in — the variance term will blow up the
    # fitness, which is the correct outcome.
    finite_sharpes = [s for s in sharpes if math.isfinite(s)]
    if not finite_sharpes:
        return float("-inf"), {"sharpe": float("-inf"), "n_trades": 0}

    n = len(finite_sharpes)
    mean_sharpe = sum(finite_sharpes) / n
    if n >= 2:
        var = sum((s - mean_sharpe) ** 2 for s in finite_sharpes) / n
        std_sharpe = math.sqrt(var)
    else:
        std_sharpe = 0.0

    raw_fitness = mean_sharpe - fold_consistency_lambda * std_sharpe

    # Worst-case max_drawdown (max magnitude, i.e. most negative since
    # MDD is negative-or-zero by convention from `summarize_from_trade_ledger`).
    dds = _f("max_drawdown")
    worst_dd = min(dds) if dds else 0.0  # most negative

    hit_rates = _f("hit_rate")
    cagrs = _f("cagr")
    trade_counts = [int(m.get("n_trades", 0) or 0) for m in per_fold_metrics]

    # Full per-fold projection — kept lightweight (just the fields the
    # portal's WF-style table renders). The full metrics dict per fold
    # would balloon the row size; this is the audit trail the user needs.
    per_fold_summary = [
        {
            "sharpe": (float(m.get("sharpe")) if m.get("sharpe") is not None else None),
            "max_drawdown": (
                float(m.get("max_drawdown"))
                if m.get("max_drawdown") is not None
                else None
            ),
            "hit_rate": (
                float(m.get("hit_rate")) if m.get("hit_rate") is not None else None
            ),
            "n_trades": int(m.get("n_trades", 0) or 0),
            "cagr": float(m.get("cagr")) if m.get("cagr") is not None else None,
            "total_return": (
                float(m.get("total_return"))
                if m.get("total_return") is not None
                else None
            ),
        }
        for m in per_fold_metrics
    ]

    aggregated = {
        "sharpe": raw_fitness,
        "max_drawdown": worst_dd,
        "hit_rate": sum(hit_rates) / len(hit_rates) if hit_rates else 0.0,
        "cagr": sum(cagrs) / len(cagrs) if cagrs else 0.0,
        "n_trades": sum(trade_counts),
        # Inspection fields — surfaced verbatim on the genome's metrics
        # dict so the portal can render "training fold by fold" the same
        # way the validator's OOS WF table renders the final fold.
        "per_fold_sharpe": sharpes,
        "per_fold_training_metrics": per_fold_summary,
        "fold_consistency_std": std_sharpe,
        "fold_consistency_lambda": fold_consistency_lambda,
        "mean_fold_sharpe": mean_sharpe,
    }
    return raw_fitness, aggregated


def compute_fitness(
    spec: StrategySpec,
    *,
    instrument: str,
    granularity: str = "M1",
    start: datetime | None = None,
    end: datetime | None = None,
    training_folds: list[tuple[datetime, datetime]] | None = None,
    fold_consistency_lambda: float = FOLD_CONSISTENCY_LAMBDA_DEFAULT,
    backtest_fn: BacktestFn | None = None,
    elite_specs: list[StrategySpec] | None = None,
    diversity_threshold: int = 2,
    gate_b_overrides: dict[str, float] | None = None,
    **backtest_kwargs: Any,
) -> tuple[float, dict[str, Any]]:
    """Run the backtest(s) for a genome and compute its fitness.

    Two modes:

      - **Single-window** (legacy, used by tests): pass ``start`` + ``end``,
        leave ``training_folds`` as None. One backtest is run over that
        window and its Sharpe becomes raw fitness.

      - **Fold-consistent** (production, v0.5.0+): pass ``training_folds`` =
        list of ``(start, end)`` tuples for each training fold. One
        backtest is run per fold, then per-fold Sharpes are aggregated
        via ``mean − λ × std``. This rewards strategies that work
        *consistently* across all training folds, not just on average
        — which is the GA-search signal that maps to "will generalise
        on OOS".

    The same penalty stack applies in both modes (zero-trade, trade-
    shortfall, drawdown breach, Gate-B dilution, diversity). In fold-
    consistent mode, the penalties operate on per-fold-aggregated
    metrics (sum of trades, worst DD, mean hit rate, etc.).

    Args:
        spec: The strategy genome to evaluate.
        instrument: Currency pair (e.g. "EUR_USD").
        granularity: Candle timeframe (default "M1").
        start, end: Single-window mode bounds. Ignored when
            ``training_folds`` is provided.
        training_folds: Fold-consistent mode. List of ``(start, end)``
            datetime tuples — one per *training* fold (the validator's
            OOS fold MUST NOT appear here). When set, one backtest is
            run per entry and metrics are aggregated.
        fold_consistency_lambda: Penalty weight on std-of-Sharpe across
            folds. See ``FOLD_CONSISTENCY_LAMBDA_DEFAULT`` for rationale.
        backtest_fn: Override the default backtest call (injection point
            for unit tests). Defaults to ``run_backtest_from_db``.
        elite_specs: Current elite genomes' specs. Used for diversity
            penalty. Pass an empty list or None to skip the penalty.
        diversity_threshold: Edit-distance below which a candidate is
            considered "near an elite". Default 2.
        **backtest_kwargs: Forwarded to the backtest function (e.g.
            account_balance, position_size, spread_pips).

    Returns:
        (fitness_value, metrics_dict). In fold-consistent mode,
        metrics_dict includes ``per_fold_sharpe``,
        ``fold_consistency_std``, ``fold_consistency_lambda``, and
        ``mean_fold_sharpe`` for inspection.
    """
    if backtest_fn is None:
        # Lazy import to avoid pulling DB layer at module import time —
        # important for unit tests of the fitness function itself.
        from agentfx.services.backtest import run_backtest_from_db

        backtest_fn = run_backtest_from_db

    # capture_signals=True triggers the trade-ledger walk inside
    # run_backtest_from_db, which overwrites the bar-level Sharpe/MaxDD/
    # CAGR/HitRate with trade-ledger (risk-sized, compounded) values.
    # This is the canonical metric definition the platform uses end-to-end
    # — without it, GA fitness would optimise against a fictional always-
    # 100%-invested unit-position pipeline that disagrees with what the
    # account actually does. The trades[] / signals[] in the response are
    # ignored here (fitness only needs `metrics`), but the trade walk
    # itself is what makes the metrics ledger-based.
    backtest_kwargs.setdefault("capture_signals", True)
    strategy_params = {"spec": spec.model_dump(mode="json")}

    if training_folds is not None and len(training_folds) > 0:
        # Fold-consistent mode: one backtest per fold, then aggregate.
        per_fold_metrics: list[dict[str, Any]] = []
        for fold_start, fold_end in training_folds:
            result = backtest_fn(
                instrument=instrument,
                granularity=granularity,
                start=fold_start,
                end=fold_end,
                strategy_name="dsl",
                strategy_params=strategy_params,
                **backtest_kwargs,
            )
            per_fold_metrics.append(
                result.get("metrics", {}) if isinstance(result, dict) else {}
            )

        raw_fitness, metrics = _aggregate_fold_metrics(
            per_fold_metrics,
            fold_consistency_lambda=fold_consistency_lambda,
        )
    else:
        # Single-window mode (legacy, used by tests).
        result = backtest_fn(
            instrument=instrument,
            granularity=granularity,
            start=start,
            end=end,
            strategy_name="dsl",
            strategy_params=strategy_params,
            **backtest_kwargs,
        )

        metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
        raw_sharpe = metrics.get("sharpe")
        if raw_sharpe is None:
            raw_sharpe = float("-inf")
        raw_fitness = float(raw_sharpe)

    fitness, penalty_breakdown = _apply_penalties(
        raw_fitness=raw_fitness,
        metrics=metrics,
        candidate=spec,
        elite_specs=elite_specs,
        diversity_threshold=diversity_threshold,
        gate_b_overrides=gate_b_overrides,
    )
    # Surface the chain (raw → each penalty → final) on the metrics dict
    # so the persister can save it and the portal can render it. Cost:
    # ~80 bytes per genome added to the JSON column. Worth it — without
    # this the user can't see why a 1.5 raw-Sharpe strategy got fitness
    # 0.39 (variance penalty + Gate-B dilution + diversity).
    metrics["penalty_breakdown"] = penalty_breakdown

    return fitness, metrics


__all__ = [
    "BacktestFn",
    "DIVERSITY_PENALTY_FACTOR",
    "GATE_B_FAILURE_DILUTION",
    "compute_fitness",
    "is_near_elite",
    "spec_edit_distance",
]
