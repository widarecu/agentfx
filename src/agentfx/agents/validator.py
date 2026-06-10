"""Validator agent — rolling walk-forward + Gate B on aggregated OOS metrics.

The orchestrator calls this agent on the top-K genomes after the GA's
generation loop completes. The validator:

1. Splits the requested data window into a *rolling* walk-forward set:
   ``n_train_folds`` contiguous training folds in the first ``train_frac`` of
   the window, then ``n_oos_folds`` contiguous OOS folds in the remaining
   portion. Defaults (3 training + 5 OOS, 70/30 split) come from the v0.4.0
   plan; see `core/validation.make_rolling_walk_forward_folds`.
2. Backtests the genome on every fold (training + OOS) and records each
   fold's metrics + regime tag. Training-fold rows are informational; OOS
   rows drive promotion.
3. Aggregates the OOS folds' metrics by **median** — Sharpe, max drawdown,
   hit rate, CAGR — and sums ``n_trades`` across them. Robust to one
   inverted regime slice that would have failed a single-OOS-fold design.
4. Applies Gate B (see `core/gates.evaluate_gate_b`) to the aggregated
   metrics. The genome passes iff Gate B passes on the median.

Why median and not mean: the analysis on 2026-06-07 showed the single-OOS
design routinely surfaced training-fold Sharpes of +1.5 to +3 and OOS
Sharpes of −1 to −2 on the same Apr-Jul 2024 EUR_USD slice — a regime
shift, not GA overfit. Median across 5 sliding OOS slices is robust to one
inverted regime; one terrible fold can't poison the verdict.

Backwards compatibility: callers that pass ``k=K`` (legacy K=4 design)
get a single OOS fold (``n_train_folds=K-1, n_oos_folds=1``) — median of
one value is that value, so the legacy behaviour is preserved exactly.

See `planning/requirements/US-004-walk-forward-validation.md` for the
v0.3.0-era contract; this module is the v0.4.0 extension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Protocol

from agentfx.agents.base import AgentResult, BaseAgent
from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import evaluate_gate_b
from agentfx.core.regime import RegimeTag
from agentfx.core.validation import (
    WalkForwardFold,
    make_rolling_walk_forward_folds,
)

logger = logging.getLogger(__name__)


class BacktestFn(Protocol):
    """Shape the validator's backtest dependency must satisfy. Production
    uses `agentfx.services.backtest.run_backtest_from_db`; tests inject a
    mock."""

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
# Result types
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class FoldEvaluation:
    """Metrics for one genome on one fold.

    Persisted as a `validation_fold` row by the orchestrator (see
    `api/models/evolution.py`). Each row also carries the fold's regime
    tag, which the UI surfaces next to the Sharpe/hit-rate columns so
    reviewers can see at-a-glance whether the OOS fold was a different
    kind of market than the training folds.
    """

    fold: WalkForwardFold
    metrics: dict[str, Any]
    passed_gate_b: bool
    gate_b_failures: list[str]
    regime: RegimeTag | None = None

    @property
    def is_oos(self) -> bool:
        return self.fold.is_oos


@dataclass
class ValidationResult:
    """The output of validating one genome against a rolling walk-forward
    split.

    ``validator_passed`` is the Gate B verdict on the *aggregated* OOS
    metrics (median across OOS folds for Sharpe/MDD/hit-rate/CAGR; sum for
    trade count). ``oos_aggregate_metrics`` exposes those aggregates so
    the UI can show "Median OOS Sharpe" without recomputing.

    For backward compatibility with the v0.3.0-era K=4 design,
    ``oos_evaluation`` still returns the *single* representative OOS fold
    when only one OOS fold exists. With multiple OOS folds, it returns the
    fold whose Sharpe is the median — the most representative for that
    same single-fold framing.
    """

    spec: StrategySpec
    folds: list[FoldEvaluation]
    validator_passed: bool
    validation_failures: list[str] = field(default_factory=list)
    oos_aggregate_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def oos_evaluations(self) -> list[FoldEvaluation]:
        """All OOS fold evaluations, in chronological order."""
        return [fe for fe in self.folds if fe.is_oos]

    @property
    def oos_evaluation(self) -> FoldEvaluation:
        """The single representative OOS fold for legacy K=4 callers.

        With multiple OOS folds, returns the fold whose Sharpe is the
        median — the closest single-fold proxy for the aggregate verdict.
        With one OOS fold, returns that fold (identical to v0.3.0).

        Raises RuntimeError if there are no OOS folds (should be
        unreachable — the validator always produces at least one).
        """
        oos = self.oos_evaluations
        if not oos:
            raise RuntimeError(
                "ValidationResult has no OOS fold — should be impossible"
            )
        if len(oos) == 1:
            return oos[0]
        # Pick the fold whose Sharpe sits closest to the median
        sharpes = [float(fe.metrics.get("sharpe", 0.0) or 0.0) for fe in oos]
        med = median(sharpes)
        idx = min(range(len(oos)), key=lambda i: abs(sharpes[i] - med))
        return oos[idx]


# ─────────────────────────────────────────────────────────────────────────
# Validator agent
# ─────────────────────────────────────────────────────────────────────────


def _aggregate_oos_metrics(
    oos_folds: list[FoldEvaluation],
) -> dict[str, Any]:
    """Median across OOS folds for ratio-style metrics; sum for trade count.

    Returns the metrics dict the validator hands to ``evaluate_gate_b``.
    Empty dict when there are no OOS folds (defensive — caller should not
    invoke this in that state).
    """
    if not oos_folds:
        return {}

    def _values(key: str) -> list[float]:
        vals: list[float] = []
        for fe in oos_folds:
            v = fe.metrics.get(key)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return vals

    aggregate: dict[str, Any] = {}

    # Median for risk-adjusted/ratio metrics — robust to one bad slice
    for key in ("sharpe", "max_drawdown", "hit_rate", "cagr"):
        vs = _values(key)
        if vs:
            aggregate[key] = float(median(vs))

    # Sum for count-style metrics — total trade count across all OOS folds
    n_trades_total = 0
    for fe in oos_folds:
        try:
            n_trades_total += int(fe.metrics.get("n_trades") or 0)
        except (TypeError, ValueError):
            continue
    aggregate["n_trades"] = n_trades_total

    return aggregate


class ValidatorAgent(BaseAgent):
    """Rolling walk-forward validator that enforces Gate B on aggregated
    OOS metrics.

    Replaces the v0.3.0-era K=4 stub. Backwards-compatible at construction
    time: callers that pass ``k=K`` get a fold geometry identical to the
    old design (``n_train_folds=K-1, n_oos_folds=1``). New callers use the
    rolling defaults (3 training folds + 5 OOS folds, 70/30 split).

    The agent does **not** evolve, optimise, or mutate. It only judges:

    1. Split the requested window via
       ``make_rolling_walk_forward_folds(...)``.
    2. Run the genome's backtest on every fold (training rows are recorded
       for the audit trail; OOS rows drive the Gate B verdict).
    3. Optionally tag each fold's slice with a regime label (vol + trend)
       — used only when the caller passes in the candle data so we can
       compute the tag without an extra fetch.
    4. Aggregate OOS metrics by median and apply Gate B.
    """

    def __init__(
        self,
        backtest_fn: BacktestFn | None = None,
        *,
        n_train_folds: int = 3,
        n_oos_folds: int = 5,
        train_frac: float = 0.7,
        k: int | None = None,
        **kwargs,
    ) -> None:
        """Construct the validator.

        Args:
            backtest_fn: Backtest dependency. ``None`` (default) lazy-loads
                ``services.backtest.run_backtest_from_db`` at first call.
            n_train_folds: Number of training folds. Default 3.
            n_oos_folds: Number of OOS folds. Default 5.
            train_frac: Fraction of the window allocated to training.
                Default 0.7 (70%/30%).
            k: Legacy K-fold parameter. When supplied, overrides the
                rolling parameters with ``n_train_folds=k-1,
                n_oos_folds=1, train_frac=(k-1)/k`` so the validator
                behaves identically to the v0.3.0 K=4 design. Existing
                tests use this path.

        Raises:
            ValueError: if the resulting fold counts are invalid.
        """
        super().__init__(**kwargs)
        if k is not None:
            if k < 2:
                raise ValueError(f"k must be >= 2 (got {k})")
            n_train_folds = k - 1
            n_oos_folds = 1
            train_frac = (k - 1) / k
        self.n_train_folds = int(n_train_folds)
        self.n_oos_folds = int(n_oos_folds)
        self.train_frac = float(train_frac)
        # Sanity check the post-resolve geometry
        if self.n_train_folds < 1 or self.n_oos_folds < 1:
            raise ValueError(
                f"validator requires >=1 train fold and >=1 OOS fold "
                f"(got n_train_folds={self.n_train_folds}, "
                f"n_oos_folds={self.n_oos_folds})"
            )
        self._backtest_fn = backtest_fn

    @property
    def agent_type(self) -> str:
        return "validator"

    @property
    def k(self) -> int:
        """Total fold count (training + OOS). Back-compat shim for callers
        that read the legacy K-fold attribute.
        """
        return self.n_train_folds + self.n_oos_folds

    def validate_inputs(self, **kwargs) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for required in ("spec", "instrument", "start", "end"):
            if kwargs.get(required) is None:
                errors.append(f"{required} is required")
        spec = kwargs.get("spec")
        if spec is not None and not isinstance(spec, StrategySpec):
            errors.append(
                f"spec must be a StrategySpec instance, got {type(spec).__name__}"
            )
        return len(errors) == 0, errors

    # ── Main entry ──────────────────────────────────────────────────

    def validate(
        self,
        spec: StrategySpec,
        *,
        instrument: str,
        granularity: str = "M1",
        start: datetime,
        end: datetime,
        backtest_fn: BacktestFn | None = None,
        gate_b_overrides: dict[str, float] | None = None,
        fold_regimes: dict[int, RegimeTag] | None = None,
        **backtest_kwargs: Any,
    ) -> ValidationResult:
        """Validate a single genome over the rolling walk-forward window.

        Args:
            spec: The genome's DSL spec.
            instrument: OANDA pair code (e.g. ``"EUR_USD"``).
            granularity: Candle granularity (e.g. ``"M10"``).
            start, end: Tz-aware UTC window. The rolling generator carves
                training/OOS regions from these.
            backtest_fn: Override the constructor's backtest dependency.
            gate_b_overrides: Optional Gate B threshold overrides
                (``min_cagr`` is the common one, raised from 0 in some
                runs).
            fold_regimes: Optional pre-computed regime tags keyed by fold
                index. When supplied, each FoldEvaluation gets its tag
                attached. When omitted, regimes stay ``None`` (the
                orchestrator usually computes and passes them in).
            **backtest_kwargs: Forwarded to the backtest function
                (e.g. ``spread_pips``, ``account_balance``,
                ``leverage``).

        Returns:
            A typed ``ValidationResult`` with per-fold evaluations and the
            aggregate OOS verdict.
        """
        fn = backtest_fn or self._backtest_fn or _default_backtest_fn

        folds = make_rolling_walk_forward_folds(
            start=start,
            end=end,
            n_train_folds=self.n_train_folds,
            n_oos_folds=self.n_oos_folds,
            train_frac=self.train_frac,
        )
        spec_params = {"spec": spec.model_dump(mode="json")}
        # Force the trade-ledger metric path so per-fold Gate B verdicts
        # are evaluated on the same risk-sized, compounded metrics that
        # power the portal walk-forward fold table and the genome page
        # hero. Without this, validator Sharpe drifts from the metrics
        # tile UI showed.
        backtest_kwargs.setdefault("capture_signals", True)

        fold_evaluations: list[FoldEvaluation] = []
        for fold in folds:
            result = fn(
                instrument=instrument,
                granularity=granularity,
                start=fold.start,
                end=fold.end,
                strategy_name="dsl",
                strategy_params=spec_params,
                **backtest_kwargs,
            )
            metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
            passed, failures = evaluate_gate_b(metrics, overrides=gate_b_overrides)
            tag = fold_regimes.get(fold.index) if fold_regimes else None
            fold_evaluations.append(
                FoldEvaluation(
                    fold=fold,
                    metrics=metrics,
                    passed_gate_b=passed,
                    gate_b_failures=failures,
                    regime=tag,
                )
            )

        # Aggregate OOS metrics → median for ratios, sum for counts
        oos_folds = [fe for fe in fold_evaluations if fe.is_oos]
        aggregate = _aggregate_oos_metrics(oos_folds)
        agg_passed, agg_failures = evaluate_gate_b(
            aggregate, overrides=gate_b_overrides
        )

        return ValidationResult(
            spec=spec,
            folds=fold_evaluations,
            validator_passed=agg_passed,
            validation_failures=agg_failures,
            oos_aggregate_metrics=aggregate,
        )

    def run(self, **kwargs) -> AgentResult:
        """BaseAgent contract entry point — wraps ``validate(...)``."""
        self.started_at = datetime.utcnow()

        is_valid, errors = self.validate_inputs(**kwargs)
        if not is_valid:
            return self._create_result("failed", {}, errors)

        try:
            result = self.validate(
                spec=kwargs["spec"],
                instrument=kwargs["instrument"],
                granularity=kwargs.get("granularity", "M1"),
                start=kwargs["start"],
                end=kwargs["end"],
                backtest_fn=kwargs.get("backtest_fn"),
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k
                    not in (
                        "spec",
                        "instrument",
                        "granularity",
                        "start",
                        "end",
                        "backtest_fn",
                    )
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("validator crashed")
            return self._create_result("failed", {}, [str(e)])

        outputs = {
            "validator_passed": result.validator_passed,
            "validation_failures": result.validation_failures,
            "oos_aggregate_metrics": result.oos_aggregate_metrics,
            "fold_metrics": [
                {
                    "fold_index": fe.fold.index,
                    "fold_start": fe.fold.start.isoformat(),
                    "fold_end": fe.fold.end.isoformat(),
                    "is_oos": fe.is_oos,
                    "metrics": fe.metrics,
                    "passed_gate_b": fe.passed_gate_b,
                    "regime": fe.regime.label if fe.regime else None,
                }
                for fe in result.folds
            ],
        }
        return self._create_result(
            "success" if result.validator_passed else "partial",
            outputs,
        )


# ─────────────────────────────────────────────────────────────────────────
# Default backtest function (lazy import to keep test paths clean)
# ─────────────────────────────────────────────────────────────────────────


def _default_backtest_fn(**kwargs: Any) -> dict[str, Any]:
    from agentfx.services.backtest import run_backtest_from_db

    return run_backtest_from_db(**kwargs)


__all__ = [
    "BacktestFn",
    "FoldEvaluation",
    "ValidationResult",
    "ValidatorAgent",
]
