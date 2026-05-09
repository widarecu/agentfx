"""Validator agent — walk-forward + Gate B on held-out OOS fold.

The orchestrator calls this agent on the top-K genomes after the
generation loop completes. The validator runs each genome on every fold
(records metrics for the audit trail) but **only the OOS fold's metrics
gate promotion** — Gate B is enforced on the held-out final fold the GA
never saw during fitness evaluation.

The integrity guarantee: the GA's fitness function (in
`agents.evolution.fitness`) operates on `training_window(folds)`, and
this validator operates on `oos_window(folds)`. They never overlap, by
construction. Tests in `tests/unit/core/test_validation.py` enforce the
non-overlap.

See `planning/requirements/US-004-walk-forward-validation.md` for the full
contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from agentfx.agents.base import AgentResult, BaseAgent
from agentfx.core.dsl import StrategySpec
from agentfx.core.gates import evaluate_gate_b
from agentfx.core.validation import (
    WalkForwardFold,
    make_walk_forward_folds,
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
    `api/models/evolution.py`).
    """

    fold: WalkForwardFold
    metrics: dict[str, Any]
    passed_gate_b: bool
    gate_b_failures: list[str]

    @property
    def is_oos(self) -> bool:
        return self.fold.is_oos


@dataclass
class ValidationResult:
    """The output of validating one genome against a walk-forward split."""

    spec: StrategySpec
    folds: list[FoldEvaluation]
    validator_passed: bool  # True iff Gate B passes on the OOS fold
    validation_failures: list[str] = field(default_factory=list)

    @property
    def oos_evaluation(self) -> FoldEvaluation:
        """The held-out fold's evaluation — the one Gate B is enforced on."""
        for fold_eval in self.folds:
            if fold_eval.is_oos:
                return fold_eval
        raise RuntimeError("ValidationResult has no OOS fold — should be impossible")


# ─────────────────────────────────────────────────────────────────────────
# Validator agent
# ─────────────────────────────────────────────────────────────────────────


class ValidatorAgent(BaseAgent):
    """Walk-forward validator that enforces Gate B on the held-out OOS fold.

    Replaces the v0.2.0-era stub. Now consumes a `StrategySpec` (the v0.3.0
    DSL genome) — not the legacy named-strategy pair, though the underlying
    backtest dispatch path supports both.

    The agent does **not** evolve, optimize, or mutate. It only judges:

    1. Split the requested window into K folds via `make_walk_forward_folds`.
    2. Run the genome's backtest on each fold (yes, including the training
       folds — those metrics are recorded for the audit trail in
       `validation_fold` rows; they don't gate promotion).
    3. Apply Gate B (`evaluate_gate_b` from `core/gates.py`) on the OOS
       fold's metrics. The genome passes iff Gate B passes there.
    """

    def __init__(
        self,
        backtest_fn: BacktestFn | None = None,
        k: int = 4,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.k = int(k)
        if self.k < 2:
            raise ValueError(f"k must be >= 2 (got {self.k})")
        self._backtest_fn = backtest_fn  # lazy default in run()

    @property
    def agent_type(self) -> str:
        return "validator"

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
        **backtest_kwargs: Any,
    ) -> ValidationResult:
        """Validate a single genome. The lower-level entry — returns a
        typed `ValidationResult` rather than the framework `AgentResult`.

        The orchestrator generally uses `validate(...)` directly; the
        framework `run(...)` wrapper exists for parity with the BaseAgent
        contract (and for callers that want a uniform AgentResult).
        """
        fn = backtest_fn or self._backtest_fn or _default_backtest_fn

        folds = make_walk_forward_folds(start=start, end=end, k=self.k)
        spec_params = {"spec": spec.model_dump(mode="json")}

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
            passed, failures = evaluate_gate_b(metrics)
            fold_evaluations.append(
                FoldEvaluation(
                    fold=fold,
                    metrics=metrics,
                    passed_gate_b=passed,
                    gate_b_failures=failures,
                )
            )

        # Gate B is ENFORCED on the OOS fold only. Training-fold Gate B
        # results are informational (recorded for the audit trail).
        oos_eval = next(fe for fe in fold_evaluations if fe.is_oos)

        return ValidationResult(
            spec=spec,
            folds=fold_evaluations,
            validator_passed=oos_eval.passed_gate_b,
            validation_failures=oos_eval.gate_b_failures,
        )

    def run(self, **kwargs) -> AgentResult:
        """BaseAgent contract entry point — wraps `validate(...)` in an
        AgentResult."""
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
            "fold_metrics": [
                {
                    "fold_index": fe.fold.index,
                    "fold_start": fe.fold.start.isoformat(),
                    "fold_end": fe.fold.end.isoformat(),
                    "is_oos": fe.is_oos,
                    "metrics": fe.metrics,
                    "passed_gate_b": fe.passed_gate_b,
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
