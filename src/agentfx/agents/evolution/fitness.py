"""Fitness evaluation for evolved genomes.

A genome's fitness is a **Gate-B-proximity score** — a weighted sum of
how close its aggregated training metrics are to passing Gate B
thresholds — plus a fold-consistency penalty.  The training window is
the concatenation of walk-forward folds 0..K-2 (constructed by the
orchestrator in `_training_range()`); the final fold (K-1) is held out
as OOS and is *only* evaluated by the validator at the end of the run.
The GA never sees the OOS fold during evolution — that's the whole
point of the walk-forward setup.

(The earlier draft of this plan called it "OOS Sharpe with penalties";
the implementation correctly trains on the held-in folds and reserves
OOS for the validator's Gate-B verdict.)

Gate-B-proximity scoring (2026-06-28):

Raw fitness = weighted sum of "distance from Gate B threshold" terms:
  - CAGR ≥ 15%       weight 0.35  ← user's top priority: consistent profitability
  - MDD ≤ 10%        weight 0.25  ← capital protection
  - Sharpe ≥ 1.0     weight 0.20  ← risk-adjusted quality
  - Hit Rate ≥ 48%   weight 0.10  ← consistency signal
  - Trade Count ≥ 300 weight 0.10 ← statistical significance
  - Fold-variance penalty: − 0.5 · std(per-fold Sharpe)

Each term is 0 at its threshold, +1 when the metric reaches 2× the
threshold, and capped per-term so no single terrible metric dominates.
A raw fitness ≥ 0 means the genome is roughly passing Gate B on a
weighted-average basis.

Penalties, in order of application:

1. **Zero-trade hard penalty** (additive, -100): a genome that opens
   zero trades on the training window is degenerate (typically: long
   and short DSL conditions are identical, so neither side ever fires
   exclusive of the other). Gate-B-proximity's trades_term only
   contributes −0.10 for zero trades (weight 0.10 × clamp −1), which
   is nowhere near enough to prevent degenerate genomes from winning
   tournaments over active losers. Hard floor at -100 guarantees that.

2. **Diversity penalty** (multiplicative, × 0.5): if the genome is
   within edit-distance 2 of a current elite (a previously-evaluated
   genome in the top 10%), halve the fitness. Stops the population
   converging on a single archetype too quickly.

(Penalties removed in v0.6.0: trade-shortfall, drawdown-breach, and
Gate-B-dilution — all three are now covered by the proximity score
terms. The breakdown keys are kept with no-op values for portal
backward-compat; see _apply_penalties.)

The fitness function delegates the actual backtest to an injectable
callable (`backtest_fn`) so unit tests can mock it without needing a
database. Production code passes the real `run_backtest_from_db`.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Protocol

from agentfx.core.dsl import StrategySpec
from agentfx.core.dsl.compiler import CompilerError

logger = logging.getLogger(__name__)

# Hard penalty for genomes whose DSL passes Pydantic grammar validation
# but fails the compiler's per-indicator semantic checks (e.g. arg arity,
# arg bounds, oscillator/price mixing). Without this catch, a single
# malformed genome (typically from an LLM proposal or a mutation that
# leaves an indicator with the wrong number of args) propagates a
# CompilerError up through `compute_fitness` → `_evaluate_population_fitness`
# → the orchestrator child process, killing the entire run with
# `process_crashed`. The fix is defense-in-depth: skip the bad genome by
# assigning it the same hard floor we use for zero-trade degenerates, log
# the failure for debugging, and let the GA continue.
COMPILER_ERROR_PENALTY = -100.0

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
# short DSL conditions are identical, or both never fire on the data).
# Gate-B-proximity's trades_term only contributes −0.10 for zero trades,
# which is nowhere near enough to prevent degenerate genomes from winning
# tournaments over active losers. Hard floor at -100 guarantees zero-trade
# genomes always lose. (See v0.3.0 acceptance run #6 for the regression.)
ZERO_TRADE_HARD_PENALTY = 100.0

# Kept for reference; no longer applied as of v0.6.0 — covered by
# trades_term and mdd_term in the Gate-B-proximity score.
TRADE_SHORTFALL_PENALTY_SCALE = 10.0
DRAWDOWN_BREACH_PENALTY_SCALE = 50.0


def _gate_b_proximity_score(
    metrics: dict[str, Any],
    fold_std_sharpe: float,
) -> tuple[float, dict[str, float]]:
    """Score = distance from Gate B thresholds, signed, weighted, clamped.

    Each term is 0 when the metric equals its Gate B threshold, positive
    when exceeding it, and negative when failing it.  Terms are clamped
    individually so no single terrible metric can dominate the weighted sum.

    Weights reflect user priority: CAGR > MDD > Sharpe > Hit/Trades.

    Gate B thresholds:
      - Sharpe  ≥ 1.0      weight 0.20
      - CAGR    ≥ 15%      weight 0.35  ← highest: consistent profitability
      - Hit Rate ≥ 48%     weight 0.10
      - MDD     ≤ 10%      weight 0.25
      - Trades  ≥ 300      weight 0.10

    The fold-variance penalty (−0.5·std) punishes regime-fragile strategies
    whose per-fold Sharpes vary widely — same rationale as the old formula.

    Returns:
        (raw_fitness, term_values) where term_values holds the per-criterion
        contributions for portal transparency.
    """

    def clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    sharpe = metrics.get("sharpe", 0.0) or 0.0
    cagr = metrics.get("cagr", 0.0) or 0.0
    hit = metrics.get("hit_rate", 0.0) or 0.0
    mdd = abs(metrics.get("max_drawdown", 0.0) or 0.0)
    n_trades = metrics.get("n_trades", 0) or 0

    # Each term: 0 at threshold, positive when exceeding, negative when
    # failing. Clamped so one terrible metric can't dominate the weighted sum.
    sharpe_term = clamp((sharpe / 1.0) - 1, -2, +2)
    cagr_term = clamp((cagr / 0.15) - 1, -2, +2)
    hit_term = clamp((hit / 0.48) - 1, -1, +1)
    mdd_term = clamp(1 - (mdd / 0.10), -2, +1)  # inverted: smaller MDD = better
    trades_term = clamp((n_trades / 300) - 1, -1, +1)

    raw = (
        0.35 * cagr_term  # user's stated top priority
        + 0.25 * mdd_term  # capital protection
        + 0.20 * sharpe_term  # risk-adjusted quality
        + 0.10 * hit_term  # consistency signal
        + 0.10 * trades_term  # statistical significance
    )
    # Fold-consistency penalty: punish fold-to-fold Sharpe variance.
    score = raw - 0.5 * fold_std_sharpe
    return score, {
        "sharpe_term": sharpe_term,
        "cagr_term": cagr_term,
        "hit_term": hit_term,
        "mdd_term": mdd_term,
        "trades_term": trades_term,
    }


def _apply_penalties(
    raw_fitness: float,
    metrics: dict,
    candidate: StrategySpec | None,
    elite_specs: list[StrategySpec] | None,
    diversity_threshold: int,
    gate_b_overrides: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Apply hard-floor and diversity penalties to a Gate-B-proximity fitness.

    Returns ``(final_fitness, breakdown)`` where ``breakdown`` is a dict
    with the per-step deductions so the caller can show the user exactly what
    got applied.  The portal renders this as a chain: raw → zero-trade →
    ×diversity → final.

    As of v0.6.0:
      - trade_shortfall_penalty, drawdown_breach_penalty, gate_b_dilution_factor
        are kept in the breakdown dict with no-op values (0 / 1.0) for portal
        backward-compat; they are NOT applied.  These effects are now baked into
        the Gate-B-proximity score terms (trades_term, mdd_term).
      - zero_trade hard penalty is kept: Gate-B-proximity's trades_term only
        contributes −0.10 for zero trades, which is nowhere near enough to
        prevent degenerate do-nothing genomes from winning tournaments.
      - diversity penalty is kept: orthogonal to Gate B; still discourages
        premature convergence on a single archetype.
    """
    fitness = raw_fitness
    breakdown: dict[str, Any] = {
        "raw_fitness": float(raw_fitness),
        "zero_trade_penalty": 0.0,
        # Kept as no-op keys for portal backward-compat (v0.6.0 removal).
        "trade_shortfall_penalty": 0.0,
        "drawdown_breach_penalty": 0.0,
        "gate_b_dilution_factor": 1.0,
        "diversity_penalty_factor": 1.0,
    }

    # 1. Zero-trade hard penalty — for genuinely degenerate genomes that
    # never open a position. Gate-B-proximity's trades_term contributes at
    # most −0.10; not nearly enough to outrank active-but-losing strategies.
    n_trades = metrics.get("n_trades", 0) or 0
    if n_trades == 0:
        fitness -= ZERO_TRADE_HARD_PENALTY
        breakdown["zero_trade_penalty"] = -float(ZERO_TRADE_HARD_PENALTY)

    # 2. Diversity penalty: halve fitness if too close to an elite.
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
    """Combine per-fold backtest metrics into one (raw_fitness, aggregated_metrics)
    pair that the penalty stack can score.

    As of v0.6.0, ``raw_fitness`` is the Gate-B-proximity score (see
    ``_gate_b_proximity_score``) rather than ``mean − λ·std(sharpe)``.
    The fold-variance penalty is embedded inside the proximity scorer.

    Aggregation rules:
      - ``sharpe``: **mean** per-fold Sharpe (actual metric, not the score).
      - ``max_drawdown``: **worst** (largest-magnitude) per-fold value —
        if any fold has a 30% DD, the strategy is risky overall, regardless
        of the other folds.
      - ``hit_rate``: **mean** across folds.
      - ``n_trades``: **sum** across folds (total training trades).
      - ``cagr``: **mean** across folds.

    The returned ``aggregated_metrics["per_fold_sharpe"]`` and
    ``["fold_consistency_std"]`` are passed through for visibility in the
    portal (no schema change — they ride on the existing metrics dict).
    ``gate_b_proximity_terms`` and ``raw_fitness`` are added for transparency.
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

    # Worst-case max_drawdown (max magnitude, i.e. most negative since
    # MDD is negative-or-zero by convention from `summarize_from_trade_ledger`).
    dds = _f("max_drawdown")
    worst_dd = min(dds) if dds else 0.0  # most negative

    hit_rates = _f("hit_rate")
    cagrs = _f("cagr")
    trade_counts = [int(m.get("n_trades", 0) or 0) for m in per_fold_metrics]

    aggregated: dict[str, Any] = {
        # Use the actual mean Sharpe — NOT raw_fitness — so the penalty
        # stack sees the real Sharpe signal rather than the composite score.
        # (Pre-v0.6.0 bug: aggregated["sharpe"] == raw_fitness, which
        # confused any consumer that expected a Sharpe value here.)
        "sharpe": mean_sharpe,
        "max_drawdown": worst_dd,
        "hit_rate": sum(hit_rates) / len(hit_rates) if hit_rates else 0.0,
        "cagr": sum(cagrs) / len(cagrs) if cagrs else 0.0,
        "n_trades": sum(trade_counts),
        # Inspection fields — surfaced verbatim on the genome's metrics
        # dict so the portal can render "training fold by fold" the same
        # way the validator's OOS WF table renders the final fold.
        "per_fold_sharpe": sharpes,
        "per_fold_training_metrics": [
            {
                "sharpe": (
                    float(m.get("sharpe")) if m.get("sharpe") is not None else None
                ),
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
        ],
        "fold_consistency_std": std_sharpe,
        "fold_consistency_lambda": fold_consistency_lambda,
        "mean_fold_sharpe": mean_sharpe,
    }

    # Gate-B-proximity score: replaces the old mean − λ·std formula.
    # The fold-variance penalty (−0.5·std) is embedded inside the scorer.
    raw_fitness, term_values = _gate_b_proximity_score(aggregated, std_sharpe)
    aggregated["gate_b_proximity_terms"] = term_values
    aggregated["raw_fitness"] = raw_fitness

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

    try:
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

            # Apply Gate-B-proximity scoring on the single window.
            # fold_std_sharpe=0.0 because there is no fold variance in
            # single-window mode (same rationale as λ=0 / consistent folds).
            raw_fitness, term_values = _gate_b_proximity_score(metrics, 0.0)
            metrics["gate_b_proximity_terms"] = term_values
            metrics["raw_fitness"] = raw_fitness
    except CompilerError as e:
        # Genome's DSL passed grammar validation but fails the compiler's
        # semantic checks (typically arg-arity mismatch from a mutation or
        # LLM proposal). Without this catch the exception kills the
        # orchestrator subprocess and crashes the entire run with
        # `process_crashed`. Caught here so the bad genome is just penalised
        # and the GA continues with the next candidate.
        logger.warning(
            "compute_fitness: genome %s rejected by compiler (%s) — "
            "assigning hard penalty %s and continuing",
            spec.model_dump(mode="json"),
            e,
            COMPILER_ERROR_PENALTY,
        )
        return COMPILER_ERROR_PENALTY, {
            "compiler_error": str(e),
            "n_trades": 0,
            "penalty_breakdown": {
                "raw_fitness": COMPILER_ERROR_PENALTY,
                "final_fitness": COMPILER_ERROR_PENALTY,
                "compiler_rejected": True,
            },
        }

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
