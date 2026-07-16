"""Pydantic schemas for the /paper-runs endpoints (US-021 / US-023).

The account-picker flow from Wilson's UX answer #4 lives here:
- Approve accepts an `oanda_account_id` picked from the user's
  detected accounts. `initial_balance` is NOT collected — the runner
  snapshots the account's live balance on the first tick.
- Reject requires a non-trivial reason (see US-023 reject-modal
  wireframe). The frontend's quick-pick chips are advisory; the
  free-text field is what persists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseSchema


class RiskConfig(BaseSchema):
    """Per-run risk knobs collected from the deploy modal."""

    max_position_size_units: int = Field(
        default=10000,
        ge=1,
        le=10_000_000,
        description="Absolute cap on the signed units of any single order.",
    )
    daily_loss_pct: float = Field(
        default=2.0,
        gt=0,
        le=100,
        description="Auto-halt if cumulative loss vs snapshot exceeds this %.",
    )
    kill_switch_dd_pct: float = Field(
        default=5.0,
        gt=0,
        le=100,
        description="Auto-halt if drawdown from snapshot balance exceeds this %.",
    )


class DeployPaperRequest(BaseSchema):
    """`POST /paper-runs/approve` body."""

    strategy_genome_id: str = Field(..., min_length=1, max_length=200)
    instrument: str = Field(
        ...,
        min_length=6,
        max_length=10,
        description="e.g. `EUR_USD` — matches the genome's evaluated instrument.",
    )
    granularity: str = Field(
        ...,
        min_length=2,
        max_length=5,
        description="e.g. `M15`, `H1`. Must match the genome's evaluated granularity.",
    )
    oanda_account_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Must be one of the caller's detected OANDA accounts.",
    )
    name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Display name for the run. Defaults to "
            "`{instrument} {granularity} — {YYYY-MM-DD}` if omitted."
        ),
    )
    risk_config: RiskConfig = Field(default_factory=RiskConfig)


class RejectGenomeRequest(BaseSchema):
    """`POST /paper-runs/reject` body — US-023 requires ≥ 10 chars."""

    strategy_genome_id: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Reason surfaced to the Evolution agent for future variations.",
    )


class PaperRunApproval(BaseSchema):
    """Response body for approve/reject."""

    run_id: str
    status: str
    approval_status: str
    status_url: str


class PaperRunPublic(BaseSchema):
    """`GET /paper-runs/{id}` — everything the run-detail page needs."""

    id: str
    status: str
    strategy_genome_id: str | None = None
    # Parent evolution run — resolved server-side so the frontend can
    # link back to `/evolution/{evolution_run_id}/genomes/{strategy_genome_id}`
    # without a follow-up query. Null when the genome has been deleted.
    evolution_run_id: str | None = None
    instrument: str
    granularity: str
    oanda_account_id: str | None = None
    oanda_environment: str | None = None
    snapshot_balance: float | None = None
    current_balance: float | None = None
    pnl: float | None = None
    risk_config: dict | None = None
    stop_reason: str | None = None
    kill_requested_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_user_id: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    user_id: int | None = None
    # v0.6.1 (Wilson's UAT feedback 2026-07-13): when the runner
    # subprocess crashes, the child persists a structured traceback
    # into `run.parameters.crash_detail` before re-raising. Surfaced
    # here so the /paper/:id page can render a diagnostic panel
    # instead of just showing "process_crashed (exit_code=1)".
    crash_detail: dict | None = None


class PaperRunListItem(BaseSchema):
    """Compact row for the paper-runs list."""

    id: str
    status: str
    instrument: str
    granularity: str
    oanda_account_id: str | None = None
    pnl: float | None = None
    snapshot_balance: float | None = None
    stop_reason: str | None = None
    created_at: datetime | None = None


class PaperRunList(BaseSchema):
    items: list[PaperRunListItem]
    total: int
    page: int
    page_size: int


class DslExitPublic(BaseSchema):
    """DSL exit-block projection for the approval queue.

    Frontend uses these to pre-populate the deploy modal's SL/TP
    fields — v0.6.1 UX fix: SL/TP are always visible and always
    populated from the strategy DSL by default (user can override).
    Prevents Wilson-2026-07-13 case where an empty broker-side SL
    field silently shipped orders without stop-loss protection.
    """

    stop_pips: int
    target_pips: int
    max_hold_bars: int
    min_hold_bars: int
    opposite_signal: bool


class ApprovalQueueItem(BaseSchema):
    """One row in `GET /paper-runs/approval-queue`."""

    strategy_genome_id: str
    run_id: str  # The AWAITING_APPROVAL run row itself
    instrument: str
    granularity: str
    fitness: float | None = None
    metrics_summary: dict | None = None
    created_at: datetime | None = None
    dsl_exit: DslExitPublic | None = None


class ClosestGenome(BaseSchema):
    """One row of the "closest to passing" comparison table (US-027).

    Populated on the approval-queue meta when at least one genome was
    validated but none passed Gate B. The frontend renders a scrollable
    5-row table with these metrics so the user can eyeball which
    near-miss looks most promising rather than trusting a single
    heuristic pick.

    `distance_score` is the sum of normalized deficits across the four
    Gate B thresholds (Sharpe ≥ 1.0, |MDD| ≤ 10%, Hit ≥ 48%, Trades
    ≥ 300). Smaller = closer to passing overall. Table is ordered
    ascending by this field.
    """

    genome_id: str
    evolution_run_id: str | None = None
    # OOS aggregates: median across folds for sharpe / hit_rate,
    # worst-case (most-negative) for max_drawdown, sum for n_trades.
    # `n_trades` is Gate-B-relevant only in the summed form since the
    # threshold is a total across OOS.
    oos_sharpe: float | None = None
    oos_max_drawdown: float | None = None
    oos_hit_rate: float | None = None
    oos_n_trades: int | None = None
    # Number of Gate B thresholds this genome already clears (0..4).
    gates_cleared: int
    # Sum of normalized (deficit / threshold) across failing gates.
    # 0.0 = passes all 4. Higher = further from passing.
    distance_score: float
    failure_summary: str  # "Sharpe 0.82 < 1.0 · Trades 292 < 300"


class ApprovalQueueMeta(BaseSchema):
    """Diagnostic counts rendered in the empty-state banner.

    Distinguishes the four scenarios that all look identical when the
    queue happens to be empty: no evolutions yet, evolutions ran but 0
    validated, validated-none-pass, user has already deployed every
    passing genome. See US-027.
    """

    evolution_run_count: int
    total_genomes: int
    validated_count: int
    passed_count: int
    touched_count: int
    # Top up to 5 non-passing genomes ordered by distance_score
    # ascending (closest-to-passing first). Empty when 0 genomes
    # validated or when passing-untouched genomes exist. Frontend
    # renders as a comparison table so Wilson can pick rather than
    # rubber-stamp a single heuristic-driven recommendation.
    top_close_to_passing: list[ClosestGenome] = []


class ApprovalQueueList(BaseSchema):
    items: list[ApprovalQueueItem]
    total: int
    meta: ApprovalQueueMeta


class KillPaperRunResponse(BaseSchema):
    run_id: str
    kill_requested_at: datetime
    message: Literal["Kill requested — the runner will halt on the next loop tick."] = (
        "Kill requested — the runner will halt on the next loop tick."
    )


# ─────────────────────────────────────────────────────────────────────
# US-025 — trades / decisions / equity read endpoints
# ─────────────────────────────────────────────────────────────────────


class PaperTradePublic(BaseSchema):
    """One row of the /paper/:id Trades table.

    Populated by the runner's signal tick (US-024). `origin='live'` for
    every v0.6.1 row; the column is reserved for v0.7.0's backtest-vs-
    live overlay work when the same table will hold both. `signal_reason`
    is the DSL entry condition summary (e.g. "sma > sma") the trader
    reads to understand WHY the runner entered.
    """

    id: str
    entry_timestamp: datetime
    exit_timestamp: datetime | None = None
    side: str  # "buy" | "sell"
    quantity: float
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    origin: str | None = None
    signal_reason: str | None = None
    # Structured exit classification (v0.6.1 UAT 2026-07-14): one of
    # 'stop' / 'target' / 'max_hold' / 'opposite_flip' / 'user_kill' /
    # 'broker_other'. Null while the trade is still open or for legacy
    # rows written before the migration.
    exit_reason: str | None = None
    # v0.6.1 (Wilson 2026-07-16): accumulated financing (swap/carry
    # rollover charges) and commission from OANDA. Both signed —
    # negative financing = we paid, positive = we received. Null on
    # legacy rows or when neither has been recorded yet.
    financing: float | None = None
    commission: float | None = None
    # Derived: `pnl + financing - commission`. Server-computed so
    # the frontend doesn't have to redo the arithmetic. Null iff
    # pnl is null (trade still open with no realized components).
    net_pnl: float | None = None


class PaperTradeList(BaseSchema):
    items: list[PaperTradePublic]
    total: int
    limit: int
    offset: int


class PaperDecisionPublic(BaseSchema):
    """One paper-run audit-log row.

    Filtered from the shared `decision_logs` table to just the actions
    the paper runner writes (USER_ORDER_PLACED / USER_POSITION_CLOSED /
    USER_SIGNAL_SKIPPED_STALE / USER_KILL_PAPER / USER_DEPLOY_PAPER /
    USER_APPROVE_GENOME / USER_REJECT_GENOME / USER_RESUME_PAPER /
    USER_VALIDATOR_OVERRIDE). Owner-scoping happens at the router.
    """

    id: str
    timestamp: datetime
    action: str
    agent_type: str
    actor_user_id: int | None = None
    reason: str | None = None
    metadata_json: dict | None = None


class PaperDecisionList(BaseSchema):
    items: list[PaperDecisionPublic]
    total: int
    limit: int
    offset: int


class EquitySnapshotPublic(BaseSchema):
    timestamp: datetime
    balance: float
    unrealized_pnl: float


class EquityList(BaseSchema):
    """`GET /paper-runs/{id}/equity` response.

    `run_started_at` + `snapshot_balance` are returned alongside so
    `LivePnLChart` can anchor its curve without a second request.
    """

    items: list[EquitySnapshotPublic]
    run_started_at: datetime | None = None
    snapshot_balance: float | None = None


# ─────────────────────────────────────────────────────────────────────
# US-026 — backtest OOS overlay for LivePnLChart
# ─────────────────────────────────────────────────────────────────────


class BacktestOverlayPoint(BaseSchema):
    """One point on the backtest OOS overlay curve.

    `timestamp_relative_seconds` is normalized to seconds-since-run-
    start so the frontend can render it against LivePnLChart's
    relative x-axis directly.
    """

    timestamp_relative_seconds: float
    balance: float


BacktestOverlayReason = Literal[
    "no_associated_evolution_run",
    "no_oos_equity_persisted",
    "oos_equity_series_not_persisted",
]


class BacktestOverlayResponse(BaseSchema):
    items: list[BacktestOverlayPoint]
    source: Literal["walk_forward_oos", "unavailable"]
    reason: BacktestOverlayReason | None = None


__all__ = [
    "ApprovalQueueItem",
    "ApprovalQueueList",
    "ApprovalQueueMeta",
    "BacktestOverlayPoint",
    "BacktestOverlayResponse",
    "ClosestGenome",
    "DeployPaperRequest",
    "EquityList",
    "EquitySnapshotPublic",
    "KillPaperRunResponse",
    "PaperDecisionList",
    "PaperDecisionPublic",
    "PaperRunApproval",
    "PaperRunList",
    "PaperRunListItem",
    "PaperRunPublic",
    "PaperTradeList",
    "PaperTradePublic",
    "RejectGenomeRequest",
    "RiskConfig",
]
