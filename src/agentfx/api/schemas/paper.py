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


class ApprovalQueueItem(BaseSchema):
    """One row in `GET /paper-runs/approval-queue`."""

    strategy_genome_id: str
    run_id: str  # The AWAITING_APPROVAL run row itself
    instrument: str
    granularity: str
    fitness: float | None = None
    metrics_summary: dict | None = None
    created_at: datetime | None = None


class ClosestGenome(BaseSchema):
    """Summary of the highest-median-OOS-Sharpe non-passing genome.

    Populated on the approval-queue meta when at least one genome was
    validated but none passed Gate B. Gives the user one concrete signal
    about how close the evolution came instead of a blank "queue empty"
    state (US-027).
    """

    genome_id: str
    oos_sharpe: float | None = None
    oos_max_drawdown: float | None = None
    oos_n_trades: int | None = None
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
    closest_to_passing: ClosestGenome | None = None


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


__all__ = [
    "ApprovalQueueItem",
    "ApprovalQueueList",
    "ApprovalQueueMeta",
    "ClosestGenome",
    "DeployPaperRequest",
    "KillPaperRunResponse",
    "PaperRunApproval",
    "PaperRunList",
    "PaperRunListItem",
    "PaperRunPublic",
    "RejectGenomeRequest",
    "RiskConfig",
]
