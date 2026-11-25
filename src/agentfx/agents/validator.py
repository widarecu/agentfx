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
   metrics, and requires **every** OOS fold to be net profitable after
   costs (CAGR > 0) and within the Gate B drawdown cap. The genome passes
   iff both hold. Per-fold Sharpe, hit rate and trade count are not
   checked: one short fold is too noisy to judge on them.

Why median and not mean: the analysis on 2026-06-07 showed the single-OOS
design routinely surfaced training-fold Sharpes of +1.5 to +3 and OOS
Sharpes of −1 to −2 on the same Apr-Jul 2024 EUR_USD slice — a regime
shift, not GA overfit. Median across 5 sliding OOS slices is robust to one
inverted regime; one terrible fold can't poison the verdict.

Why the per-fold floor (AFX-49): the median alone passed genomes that lost
money or breached the drawdown cap on most OOS folds, as long as the
middle values looked fine.

Backwards compatibility: callers that pass ``k=K`` (legacy K=4 design)
get a single OOS fold (``n_train_folds=K-1, n_oos_folds=1``) — median of
one value is that value, so the legacy behaviour is preserved exactly.

See `planning/requirements/US-004-walk-forward-validation.md` for the
v0.3.0-era contract; this module is the v0.4.0 extension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Protocol

from agentfx.agents.base import AgentResult, BaseAgent
from agentfx.core.deflated_sharpe import SharpeSample, deflated_sharpe
from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import GATE_B_THRESHOLDS, evaluate_gate_b
from agentfx.core.metrics import daily_equity_from_trade_ledger
from agentfx.core.regime import RegimeTag
from agentfx.core.validation import (
    WalkForwardFold,
    make_rolling_walk_forward_folds,
    oos_window,
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
class DsrEvidence:
    trial_count: int
    threshold: float
    probability: float | None
    raw_sharpe: float | None
    sharpe_bar: float | None
    passed: bool
    reason: str


@dataclass
class ValidationResult:
    """The output of validating one genome against a rolling walk-forward
    split.

    ``validator_passed`` is the Gate B verdict on the *aggregated* OOS
    metrics (median across OOS folds for Sharpe/MDD/hit-rate/CAGR; sum for
    trade count), AND'd with the per-fold floor: every OOS fold net
    profitable and within the drawdown cap. ``oos_aggregate_metrics`` exposes those aggregates so
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
    fold_failures: list[str] = field(default_factory=list)
    dsr_sample: SharpeSample | str | None = None
    dsr: DsrEvidence | None = None

    def apply_dsr(self, *, trial_count: int, threshold: float) -> None:
        probability = raw_sharpe = bar = None
        passed = False
        try:
            if not isinstance(self.dsr_sample, SharpeSample):
                raise ValueError(
                    self.dsr_sample or "full OOS returns were not collected"
                )
            raw_sharpe = self.dsr_sample.annualized_sharpe
            probability, bar = deflated_sharpe(
                self.dsr_sample, trial_count=trial_count, threshold=threshold
            )
            passed = probability >= threshold
            comparison = "meets" if passed else "is below"
            reason = (
                f"Deflated Sharpe {probability:.2%} {comparison} the {threshold:.2%} requirement "
                f"({trial_count} trials; full OOS Sharpe {raw_sharpe:.2f}; required Sharpe {bar:.2f})"
            )
        except ValueError as exc:
            reason = f"Deflated Sharpe unavailable ({trial_count} trials): {exc}"
        self.dsr = DsrEvidence(
            trial_count, threshold, probability, raw_sharpe, bar, passed, reason
        )
        if not passed:
            self.validator_passed = False
            self.validation_failures = [*self.validation_failures, reason]

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


def _oos_fold_floor_failures(
    oos_folds: list[FoldEvaluation],
    overrides: dict[str, float] | None = None,
) -> list[str]:
    """Failures for OOS folds that lose money or breach the drawdown cap.

    CAGR is net of spread, slippage and swap, so ``cagr > 0`` means the
    fold made money after costs. A missing metric fails, as in Gate B.
    """
    cap = {**GATE_B_THRESHOLDS, **(overrides or {})}["max_drawdown"]
    failures: list[str] = []
    for n, fe in enumerate(oos_folds, start=1):
        label = f"OOS fold {n}/{len(oos_folds)}"
        cagr = _as_float(fe.metrics.get("cagr"), default=-1.0)
        if cagr <= 0:
            failures.append(f"{label}: CAGR {cagr * 100:+.1f}% is not profitable")
        max_dd = abs(_as_float(fe.metrics.get("max_drawdown"), default=1.0))
        if max_dd > cap:
            failures.append(
                f"{label}: Max DD {max_dd * 100:.1f}% > cap {cap * 100:.0f}%"
            )
    return failures


def _as_float(value: Any, *, default: float) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


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
        collect_dsr_sample: bool = False,
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
            collect_dsr_sample: Run a separate, contiguous OOS backtest for
                DSR. The orchestrator applies DSR after counting the batch.
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
        fold_failures = _oos_fold_floor_failures(oos_folds, gate_b_overrides)

        dsr_sample = None
        if collect_dsr_sample:
            oos_start, oos_end = oos_window(folds)
            end_inclusive = oos_end - timedelta(microseconds=1)
            try:
                full_oos = fn(
                    instrument=instrument,
                    granularity=granularity,
                    start=oos_start,
                    end=end_inclusive,
                    strategy_name="dsl",
                    strategy_params=spec_params,
                    **{
                        **backtest_kwargs,
                        "capture_signals": True,
                        "require_full_window": True,
                    },
                )
                starting_balance = float(full_oos["sizing"]["starting_balance"])
                equity = daily_equity_from_trade_ledger(
                    full_oos["trades"],
                    starting_balance=starting_balance,
                    window_start=oos_start,
                    window_end=end_inclusive,
                ).to_numpy()
                balances = [starting_balance, *equity]
                if min(balances) <= 0:
                    raise ValueError("the backtest account ran out of equity")
                daily_returns = equity / balances[:-1] - 1
                dsr_sample = SharpeSample.from_returns(daily_returns)
            except ValueError as exc:
                dsr_sample = str(exc)
            except Exception:  # noqa: BLE001
                logger.exception("full OOS backtest failed")
                dsr_sample = "the full OOS backtest did not produce a trade ledger"

        return ValidationResult(
            spec=spec,
            folds=fold_evaluations,
            validator_passed=agg_passed and not fold_failures,
            validation_failures=agg_failures + fold_failures,
            oos_aggregate_metrics=aggregate,
            fold_failures=fold_failures,
            dsr_sample=dsr_sample,
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
