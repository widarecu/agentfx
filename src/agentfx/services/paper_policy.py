"""Per-decision risk gates for the paper runner (US-023).

Kept as pure functions so the runner's loop-body can call them without
touching the DB, and the unit test suite can exercise every branch
with plain dataclasses. Each helper returns a `(pass, reason)` pair;
`pass=False` short-circuits the decision loop and — for the loss-cap
and kill-switch cases — halts the run.

The runner is responsible for persisting `stop_reason` and updating
`status` when a gate refuses; these helpers deliberately do not touch
the database.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskState:
    """Snapshot of the balances + limits the gates operate on.

    Numbers are always in the account's home currency; the runner
    fetches them fresh from OANDA on each tick, so we don't have to
    worry about staleness here.
    """

    snapshot_balance: float
    current_balance: float
    max_position_size_units: int
    loss_cap_pct: float
    kill_switch_dd_pct: float


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str = ""


def check_loss_cap(state: RiskState) -> GateResult:
    """Halt the run if cumulative loss vs snapshot exceeds
    `loss_cap_pct`.

    The cap is measured against the run-start snapshot balance, not
    against a UTC-day open — so this is a **cumulative** loss cap over
    the life of the run, not a per-day reset. Wilson 2026-08-15
    renamed from `daily_loss_pct` / `check_daily_loss_cap` /
    `DAILY_LOSS_LIMIT` after Run B halted with a name that implied
    daily-scoped behaviour it never had.

    Returns ok=False when the loss threshold is met (equality
    counts — a run that lost EXACTLY the cap should halt, not
    keep placing orders).
    """
    if state.snapshot_balance <= 0:
        # Defensive — a zero snapshot means the runner never
        # captured a balance; the run shouldn't be trading and this
        # gate refuses fail-closed rather than dividing by zero.
        return GateResult(False, "LOSS_CAP_HIT")
    loss_pct = (state.snapshot_balance - state.current_balance) / state.snapshot_balance
    if loss_pct * 100 >= state.loss_cap_pct:
        return GateResult(False, "LOSS_CAP_HIT")
    return GateResult(True)


def check_kill_switch_dd(state: RiskState) -> GateResult:
    """Halt if the unrealised drawdown from snapshot exceeds
    `kill_switch_dd_pct`.

    Same denominator + fail-closed policy as the loss cap; exists as
    a separate call so the runner can log which one tripped.
    """
    if state.snapshot_balance <= 0:
        return GateResult(False, "BROKER_CIRCUIT")
    dd_pct = (state.snapshot_balance - state.current_balance) / state.snapshot_balance
    if dd_pct * 100 >= state.kill_switch_dd_pct:
        return GateResult(False, "BROKER_CIRCUIT")
    return GateResult(True)


def check_position_size(state: RiskState, proposed_units: int) -> GateResult:
    """Reject a proposed order that exceeds the configured cap.

    Absolute cap — long and short share the same limit. Returns ok=True
    with an empty reason when the proposed size is inside bounds, and
    ok=False with `POSITION_SIZE_CAP` when it isn't. The reason string
    matches the vocabulary in US-023 so `decision_logs` rows are
    grep-friendly.
    """
    if abs(proposed_units) > state.max_position_size_units:
        return GateResult(False, "POSITION_SIZE_CAP")
    return GateResult(True)


def evaluate_all(state: RiskState, proposed_units: int) -> GateResult:
    """Convenience: run every gate in order and return the first
    refusal.

    Order matches the runner's expected behaviour: kill switch and
    loss cap halt the whole run; position size only rejects the
    single order. The runner treats a refusal at either of the first
    two as "halt", and at the third as "skip this tick".
    """
    for check in (check_kill_switch_dd(state), check_loss_cap(state)):
        if not check.ok:
            return check
    return check_position_size(state, proposed_units)


__all__ = [
    "GateResult",
    "RiskState",
    "check_kill_switch_dd",
    "check_loss_cap",
    "check_position_size",
    "evaluate_all",
]
