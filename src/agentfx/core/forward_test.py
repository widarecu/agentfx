"""Pre-agreed paper forward-test contract and live-eligibility evaluation (AFX-154).

Every paper run locks its forward-test contract at start time so the pass
criteria cannot be moved after seeing results. Paper trading itself remains
an un-halted sandbox (CLAUDE.md rule 11); this module governs **eligibility
for live nomination**, never auto-halting paper runs.

Default thresholds align with `planning/MVP_CONSTRAINTS.md` (§1, §4, §5) and
`planning/ROADMAP.md` (±30% paper-vs-backtest drift cap):

- `min_closed_trades`: 100 closed round-trip paper trades
- `min_net_pnl`: 0.0 (strictly net positive realized PnL after financing & commission)
- `min_sharpe`: 1.0 (annualised daily Sharpe)
- `max_drawdown`: 0.10 (10% live drawdown cap; compared by magnitude)
- `max_divergence`: 0.30 (at most 30% Sharpe degradation vs locked backtest OOS Sharpe)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

CriterionStatus = Literal["passed", "failed", "pending", "skipped"]
ForwardTestStatus = Literal["in_progress", "passed", "failed", "no_contract"]

FORWARD_TEST_CONTRACT_VERSION = 1

FORWARD_TEST_DEFAULTS: dict[str, int | float] = {
    "min_closed_trades": 100,
    "min_net_pnl": 0.0,
    "min_sharpe": 1.0,
    "max_drawdown": 0.10,
    "max_divergence": 0.30,
}


def _is_finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


@dataclass(frozen=True)
class ForwardTestContract:
    """Immutable forward-test pass criteria locked when a paper run starts."""

    locked_at: str
    min_closed_trades: int = int(FORWARD_TEST_DEFAULTS["min_closed_trades"])
    min_net_pnl: float = float(FORWARD_TEST_DEFAULTS["min_net_pnl"])
    min_sharpe: float = float(FORWARD_TEST_DEFAULTS["min_sharpe"])
    max_drawdown: float = float(FORWARD_TEST_DEFAULTS["max_drawdown"])
    max_divergence: float = float(FORWARD_TEST_DEFAULTS["max_divergence"])
    backtest_oos_sharpe: float | None = None
    backtest_oos_hit_rate: float | None = None
    backtest_oos_max_drawdown: float | None = None
    version: int = FORWARD_TEST_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.min_closed_trades < 1:
            raise ValueError("min_closed_trades must be >= 1")
        if not math.isfinite(self.min_net_pnl):
            raise ValueError("min_net_pnl must be finite")
        if not math.isfinite(self.min_sharpe):
            raise ValueError("min_sharpe must be finite")
        if not (math.isfinite(self.max_drawdown) and 0.0 < self.max_drawdown <= 1.0):
            raise ValueError("max_drawdown must be in (0, 1]")
        if not (
            math.isfinite(self.max_divergence) and 0.0 < self.max_divergence <= 1.0
        ):
            raise ValueError("max_divergence must be in (0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        min_closed_trades: int | None = None,
        min_net_pnl: float | None = None,
        min_sharpe: float | None = None,
        max_drawdown: float | None = None,
        max_divergence: float | None = None,
        backtest_oos_sharpe: float | None = None,
        backtest_oos_hit_rate: float | None = None,
        backtest_oos_max_drawdown: float | None = None,
        locked_at: datetime | None = None,
    ) -> ForwardTestContract:
        ts = locked_at or datetime.now(UTC)
        return cls(
            locked_at=ts.isoformat(),
            min_closed_trades=(
                int(FORWARD_TEST_DEFAULTS["min_closed_trades"])
                if min_closed_trades is None
                else int(min_closed_trades)
            ),
            min_net_pnl=(
                float(FORWARD_TEST_DEFAULTS["min_net_pnl"])
                if min_net_pnl is None
                else float(min_net_pnl)
            ),
            min_sharpe=(
                float(FORWARD_TEST_DEFAULTS["min_sharpe"])
                if min_sharpe is None
                else float(min_sharpe)
            ),
            max_drawdown=(
                float(FORWARD_TEST_DEFAULTS["max_drawdown"])
                if max_drawdown is None
                else float(max_drawdown)
            ),
            max_divergence=(
                float(FORWARD_TEST_DEFAULTS["max_divergence"])
                if max_divergence is None
                else float(max_divergence)
            ),
            backtest_oos_sharpe=(
                float(backtest_oos_sharpe) if _is_finite(backtest_oos_sharpe) else None
            ),
            backtest_oos_hit_rate=(
                float(backtest_oos_hit_rate)
                if _is_finite(backtest_oos_hit_rate)
                else None
            ),
            backtest_oos_max_drawdown=(
                float(backtest_oos_max_drawdown)
                if _is_finite(backtest_oos_max_drawdown)
                else None
            ),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ForwardTestContract | None:
        if not isinstance(data, dict) or not data.get("locked_at"):
            return None
        try:
            return cls(
                version=int(data.get("version", FORWARD_TEST_CONTRACT_VERSION)),
                locked_at=str(data["locked_at"]),
                min_closed_trades=int(data["min_closed_trades"]),
                min_net_pnl=float(
                    data.get("min_net_pnl", FORWARD_TEST_DEFAULTS["min_net_pnl"])
                ),
                min_sharpe=float(data["min_sharpe"]),
                max_drawdown=float(data["max_drawdown"]),
                max_divergence=float(data["max_divergence"]),
                backtest_oos_sharpe=(
                    float(data["backtest_oos_sharpe"])
                    if _is_finite(data.get("backtest_oos_sharpe"))
                    else None
                ),
                backtest_oos_hit_rate=(
                    float(data["backtest_oos_hit_rate"])
                    if _is_finite(data.get("backtest_oos_hit_rate"))
                    else None
                ),
                backtest_oos_max_drawdown=(
                    float(data["backtest_oos_max_drawdown"])
                    if _is_finite(data.get("backtest_oos_max_drawdown"))
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class CriterionResult:
    """Pass/fail/pending verdict for one criterion in the forward-test contract."""

    name: str
    label: str
    status: CriterionStatus
    actual: float | int | None
    threshold: float | int | None
    message: str


@dataclass(frozen=True)
class ForwardTestEvaluation:
    """Full live-eligibility evaluation of a paper run against its locked contract."""

    status: ForwardTestStatus
    eligible_for_live: bool
    closed_trades: int
    min_closed_trades: int
    trades_remaining: int
    progress_pct: float
    progress_label: str
    early_failure: bool
    criteria: list[CriterionResult]
    failures: list[str]
    metrics: dict[str, float | int | None]
    contract: dict[str, Any] | None


def compute_sharpe_divergence(
    paper_sharpe: float | None,
    backtest_oos_sharpe: float | None,
) -> float | None:
    """Compute downside Sharpe degradation from backtest OOS to paper.

    Returns a non-negative fraction:
    - 0.0 when paper meets or beats backtest OOS Sharpe
    - 0.25 when paper Sharpe is 25% below backtest OOS Sharpe
    - >1.0 when paper Sharpe turns negative against a positive backtest Sharpe
    - None when either value is missing/non-finite or backtest OOS Sharpe <= 0
    """
    if not _is_finite(paper_sharpe) or not _is_finite(backtest_oos_sharpe):
        return None
    assert paper_sharpe is not None and backtest_oos_sharpe is not None
    if backtest_oos_sharpe <= 0.0:
        return None
    shortfall = (backtest_oos_sharpe - paper_sharpe) / backtest_oos_sharpe
    return float(max(0.0, shortfall))


def evaluate_forward_test(
    contract: ForwardTestContract | dict[str, Any] | None,
    metrics: dict[str, Any],
) -> ForwardTestEvaluation:
    """Evaluate paper-run metrics against the run's locked forward-test contract.

    Args:
        contract: Locked `ForwardTestContract` (or its serialized dict). If
            missing or invalid, fails closed (`status="no_contract"`,
            `eligible_for_live=False`).
        metrics: Paper run metrics dict. Recognised keys:
            - "closed_trades" (int, default 0)
            - "net_pnl" (float | None)
            - "sharpe" (float | None)
            - "max_drawdown" (float | None; either sign, compared by magnitude)
            - "hit_rate" (float | None; informational)

    Returns:
        `ForwardTestEvaluation` with `eligible_for_live=True` iff a valid
        contract exists, `closed_trades >= min_closed_trades`, and every
        applicable criterion passes.
    """
    raw_closed = metrics.get("closed_trades", 0)
    closed_trades = max(0, int(raw_closed)) if _is_finite(raw_closed) else 0
    net_pnl = float(metrics["net_pnl"]) if _is_finite(metrics.get("net_pnl")) else None
    sharpe_val = float(metrics["sharpe"]) if _is_finite(metrics.get("sharpe")) else None
    raw_mdd = metrics.get("max_drawdown")
    max_dd_mag = abs(float(raw_mdd)) if _is_finite(raw_mdd) else None
    hit_rate_val = (
        float(metrics["hit_rate"]) if _is_finite(metrics.get("hit_rate")) else None
    )

    resolved: ForwardTestContract | None
    if isinstance(contract, ForwardTestContract):
        resolved = contract
    else:
        resolved = ForwardTestContract.from_dict(contract)

    if resolved is None:
        default_min = int(FORWARD_TEST_DEFAULTS["min_closed_trades"])
        remaining = max(0, default_min - closed_trades)
        progress_pct = min(100.0, round((closed_trades / default_min) * 100.0, 1))
        return ForwardTestEvaluation(
            status="no_contract",
            eligible_for_live=False,
            closed_trades=closed_trades,
            min_closed_trades=default_min,
            trades_remaining=remaining,
            progress_pct=progress_pct,
            progress_label=f"{closed_trades} / {default_min} trades",
            early_failure=False,
            criteria=[],
            failures=[
                "Paper run has no locked forward-test contract; redeploy to start "
                "a pre-agreed forward test before live nomination."
            ],
            metrics={
                "closed_trades": closed_trades,
                "net_pnl": net_pnl,
                "sharpe": sharpe_val,
                "max_drawdown": max_dd_mag,
                "divergence_pct": None,
                "hit_rate": hit_rate_val,
            },
            contract=None,
        )

    min_trades = resolved.min_closed_trades
    trades_remaining = max(0, min_trades - closed_trades)
    progress_pct = min(100.0, round((closed_trades / min_trades) * 100.0, 1))
    progress_label = f"{closed_trades} / {min_trades} trades"
    has_enough_trades = closed_trades >= min_trades

    divergence = compute_sharpe_divergence(sharpe_val, resolved.backtest_oos_sharpe)

    criteria: list[CriterionResult] = []
    failures: list[str] = []

    # 1. Minimum closed trades
    if has_enough_trades:
        criteria.append(
            CriterionResult(
                name="min_closed_trades",
                label="Closed trades",
                status="passed",
                actual=closed_trades,
                threshold=min_trades,
                message=f"{closed_trades} / {min_trades} closed trades reached",
            )
        )
    else:
        msg = (
            f"Insufficient closed trades: {closed_trades} / {min_trades} "
            f"({trades_remaining} remaining)"
        )
        criteria.append(
            CriterionResult(
                name="min_closed_trades",
                label="Closed trades",
                status="pending",
                actual=closed_trades,
                threshold=min_trades,
                message=msg,
            )
        )
        failures.append(msg)

    # 2. Max drawdown within live cap — evaluated immediately so live-risk
    #    breaches fail early even before reaching min_closed_trades.
    early_failure = False
    if max_dd_mag is None:
        if has_enough_trades:
            msg = "Max drawdown unavailable; cannot verify live drawdown cap"
            criteria.append(
                CriterionResult(
                    name="max_drawdown",
                    label="Max drawdown",
                    status="failed",
                    actual=None,
                    threshold=resolved.max_drawdown,
                    message=msg,
                )
            )
            failures.append(msg)
        else:
            criteria.append(
                CriterionResult(
                    name="max_drawdown",
                    label="Max drawdown",
                    status="pending",
                    actual=None,
                    threshold=resolved.max_drawdown,
                    message=f"No drawdown recorded yet (cap <= {resolved.max_drawdown * 100:.1f}%)",
                )
            )
    elif max_dd_mag > resolved.max_drawdown + 1e-12:
        msg = (
            f"Max drawdown {max_dd_mag * 100:.1f}% exceeds live cap "
            f"{resolved.max_drawdown * 100:.1f}%"
        )
        if not has_enough_trades:
            early_failure = True
            msg = f"Early live-risk breach: {msg}"
        criteria.append(
            CriterionResult(
                name="max_drawdown",
                label="Max drawdown",
                status="failed",
                actual=round(max_dd_mag, 6),
                threshold=resolved.max_drawdown,
                message=msg,
            )
        )
        failures.append(msg)
    else:
        criteria.append(
            CriterionResult(
                name="max_drawdown",
                label="Max drawdown",
                status="passed",
                actual=round(max_dd_mag, 6),
                threshold=resolved.max_drawdown,
                message=(
                    f"Max drawdown {max_dd_mag * 100:.1f}% within live cap "
                    f"{resolved.max_drawdown * 100:.1f}%"
                ),
            )
        )

    # 3. Net positive PnL
    if not has_enough_trades:
        pnl_note = (
            f"Current net PnL {net_pnl:+.2f} (evaluated at {min_trades} trades)"
            if net_pnl is not None
            else f"Pending ({min_trades} closed trades required)"
        )
        criteria.append(
            CriterionResult(
                name="net_positive_pnl",
                label="Net PnL",
                status="pending",
                actual=round(net_pnl, 2) if net_pnl is not None else None,
                threshold=resolved.min_net_pnl,
                message=pnl_note,
            )
        )
    elif net_pnl is not None and net_pnl > resolved.min_net_pnl:
        criteria.append(
            CriterionResult(
                name="net_positive_pnl",
                label="Net PnL",
                status="passed",
                actual=round(net_pnl, 2),
                threshold=resolved.min_net_pnl,
                message=f"Net PnL {net_pnl:+.2f} > {resolved.min_net_pnl:.2f}",
            )
        )
    else:
        msg = (
            f"Net PnL {net_pnl:+.2f} is not above {resolved.min_net_pnl:.2f}"
            if net_pnl is not None
            else "Net PnL unavailable"
        )
        criteria.append(
            CriterionResult(
                name="net_positive_pnl",
                label="Net PnL",
                status="failed",
                actual=round(net_pnl, 2) if net_pnl is not None else None,
                threshold=resolved.min_net_pnl,
                message=msg,
            )
        )
        failures.append(msg)

    # 4. Minimum Sharpe ratio
    if not has_enough_trades:
        sharpe_note = (
            f"Current Sharpe {sharpe_val:.2f} (evaluated at {min_trades} trades, "
            f"threshold >= {resolved.min_sharpe:.2f})"
            if sharpe_val is not None
            else f"Pending ({min_trades} closed trades required)"
        )
        criteria.append(
            CriterionResult(
                name="min_sharpe",
                label="Sharpe ratio",
                status="pending",
                actual=round(sharpe_val, 4) if sharpe_val is not None else None,
                threshold=resolved.min_sharpe,
                message=sharpe_note,
            )
        )
    elif sharpe_val is not None and sharpe_val >= resolved.min_sharpe:
        criteria.append(
            CriterionResult(
                name="min_sharpe",
                label="Sharpe ratio",
                status="passed",
                actual=round(sharpe_val, 4),
                threshold=resolved.min_sharpe,
                message=f"Sharpe {sharpe_val:.2f} >= {resolved.min_sharpe:.2f}",
            )
        )
    else:
        msg = (
            f"Sharpe {sharpe_val:.2f} < {resolved.min_sharpe:.2f}"
            if sharpe_val is not None
            else "Sharpe ratio unavailable"
        )
        criteria.append(
            CriterionResult(
                name="min_sharpe",
                label="Sharpe ratio",
                status="failed",
                actual=round(sharpe_val, 4) if sharpe_val is not None else None,
                threshold=resolved.min_sharpe,
                message=msg,
            )
        )
        failures.append(msg)

    # 5. Backtest-vs-paper divergence
    if resolved.backtest_oos_sharpe is None or resolved.backtest_oos_sharpe <= 0.0:
        criteria.append(
            CriterionResult(
                name="backtest_divergence",
                label="Backtest vs paper divergence",
                status="skipped",
                actual=None,
                threshold=resolved.max_divergence,
                message="No positive backtest OOS Sharpe locked at paper start",
            )
        )
    elif not has_enough_trades:
        div_note = (
            f"Current Sharpe degradation {divergence * 100:.1f}% vs OOS "
            f"{resolved.backtest_oos_sharpe:.2f} (evaluated at {min_trades} trades)"
            if divergence is not None
            else f"Pending ({min_trades} closed trades required)"
        )
        criteria.append(
            CriterionResult(
                name="backtest_divergence",
                label="Backtest vs paper divergence",
                status="pending",
                actual=round(divergence, 4) if divergence is not None else None,
                threshold=resolved.max_divergence,
                message=div_note,
            )
        )
    elif divergence is not None and divergence <= resolved.max_divergence + 1e-12:
        criteria.append(
            CriterionResult(
                name="backtest_divergence",
                label="Backtest vs paper divergence",
                status="passed",
                actual=round(divergence, 4),
                threshold=resolved.max_divergence,
                message=(
                    f"Sharpe degradation {divergence * 100:.1f}% within "
                    f"{resolved.max_divergence * 100:.0f}% limit "
                    f"(paper {sharpe_val:.2f} vs OOS {resolved.backtest_oos_sharpe:.2f})"
                ),
            )
        )
    else:
        msg = (
            f"Backtest-vs-paper Sharpe degradation {divergence * 100:.1f}% "
            f"exceeds {resolved.max_divergence * 100:.0f}% limit "
            f"(paper {sharpe_val:.2f} vs OOS {resolved.backtest_oos_sharpe:.2f})"
            if divergence is not None and sharpe_val is not None
            else "Backtest-vs-paper divergence unavailable"
        )
        criteria.append(
            CriterionResult(
                name="backtest_divergence",
                label="Backtest vs paper divergence",
                status="failed",
                actual=round(divergence, 4) if divergence is not None else None,
                threshold=resolved.max_divergence,
                message=msg,
            )
        )
        failures.append(msg)

    any_failed = any(c.status == "failed" for c in criteria)
    if any_failed:
        status: ForwardTestStatus = "failed"
        eligible = False
    elif not has_enough_trades:
        status = "in_progress"
        eligible = False
    else:
        status = "passed"
        eligible = True

    return ForwardTestEvaluation(
        status=status,
        eligible_for_live=eligible,
        closed_trades=closed_trades,
        min_closed_trades=min_trades,
        trades_remaining=trades_remaining,
        progress_pct=progress_pct,
        progress_label=progress_label,
        early_failure=early_failure,
        criteria=criteria,
        failures=failures,
        metrics={
            "closed_trades": closed_trades,
            "net_pnl": round(net_pnl, 2) if net_pnl is not None else None,
            "sharpe": round(sharpe_val, 4) if sharpe_val is not None else None,
            "max_drawdown": round(max_dd_mag, 6) if max_dd_mag is not None else None,
            "divergence_pct": round(divergence, 4) if divergence is not None else None,
            "hit_rate": round(hit_rate_val, 4) if hit_rate_val is not None else None,
        },
        contract=resolved.to_dict(),
    )


__all__ = [
    "FORWARD_TEST_CONTRACT_VERSION",
    "FORWARD_TEST_DEFAULTS",
    "CriterionResult",
    "CriterionStatus",
    "ForwardTestContract",
    "ForwardTestEvaluation",
    "ForwardTestStatus",
    "compute_sharpe_divergence",
    "evaluate_forward_test",
]
