"""
Pydantic schemas for Bayesian Parameter Optimization (v0.2.0)

Defines request/response models for optimization API endpoints.
Follows the *Base/*Create/*Public pattern from existing schemas.
"""

from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime
from ..models.optimization import JobStatus, StrategyType
from .base import BaseSchema


# =============================================================================
# Parameter Range
# =============================================================================


class ParamRange(BaseModel):
    """Definition of a parameter search range."""

    min: float = Field(..., description="Minimum value")
    max: float = Field(..., description="Maximum value")
    step: float | None = Field(None, description="Step size (optional)")


# =============================================================================
# Optimization Job Schemas
# =============================================================================


class OptimizationJobBase(BaseModel):
    """Base schema for optimization jobs."""

    strategy: StrategyType = Field(..., description="Strategy type (ma, rsi, donchian)")
    pair: str = Field(..., description="Forex pair (e.g., EUR_USD)")
    param_ranges: dict[str, ParamRange] = Field(
        ..., description="Parameter search ranges"
    )
    account_balance: float = Field(
        10000.0, description="Starting account balance in dollars"
    )
    position_size: int = Field(
        10000, description="Position size in units (10000 = 0.1 lot)"
    )
    data_years: int = Field(5, description="Years of historical data to use")


class OptimizationJobCreate(OptimizationJobBase):
    """Schema for creating a new optimization job."""

    pass


class OptimizationJobPublic(OptimizationJobBase, BaseSchema):
    """Public schema for optimization job responses."""

    id: str
    status: JobStatus
    progress: int
    estimated_iterations: int
    completed_iterations: int
    best_sharpe: float | None
    best_trial_id: str | None
    data_range_start: datetime | None
    data_range_end: datetime | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    created_by: int | None


class OptimizationJobList(BaseSchema):
    """Paginated list of optimization jobs."""

    items: list[OptimizationJobPublic]
    total: int
    page: int
    page_size: int


class OptimizationJobCreated(BaseSchema):
    """Response for async job creation (202 Accepted)."""

    job_id: str
    status: JobStatus
    status_url: str
    estimated_iterations: int
    created_at: datetime


# =============================================================================
# Trial Metrics Schemas
# =============================================================================


class TrialMetrics(BaseModel):
    """Core backtest metrics."""

    sharpe: float = Field(..., description="Sharpe ratio")
    max_drawdown: float = Field(..., description="Maximum drawdown (0.06 = 6%)")
    hit_rate: float = Field(..., description="Win rate (0.54 = 54%)")
    trade_count: int = Field(..., description="Total number of trades")
    total_return: float = Field(..., description="Total return (0.15 = 15%)")
    profit_factor: float = Field(..., description="Profit factor")
    avg_win: float | None = None
    avg_loss: float | None = None
    max_consecutive_losses: int | None = None
    avg_trade_duration: float | None = None


class TrialMetricsExtended(TrialMetrics):
    """Extended metrics with financing breakdown."""

    # Pips-based (without financing)
    total_pips: float | None = None
    avg_pips_per_trade: float | None = None
    # Financing costs
    total_spread_cost_pips: float | None = None
    total_swap_cost_pips: float | None = None
    total_cost_pips: float | None = None
    # Net (with financing)
    net_pips: float | None = None
    avg_net_pips_per_trade: float | None = None
    # Dollar-based
    gross_pnl_dollars: float | None = None
    total_spread_cost_dollars: float | None = None
    total_swap_cost_dollars: float | None = None
    net_pnl_dollars: float | None = None


# =============================================================================
# Optimization Trial Schemas
# =============================================================================


class OptimizationTrialBase(BaseModel):
    """Base schema for optimization trials."""

    params: dict[str, float] = Field(..., description="Parameter values used")
    metrics: TrialMetrics = Field(..., description="Backtest performance metrics")
    passes_gate_b: bool = Field(
        False, description="Whether trial passes Gate B thresholds"
    )
    gate_b_failures: list[str] | None = Field(
        None, description="Gate B failure reasons"
    )


class OptimizationTrialPublic(BaseSchema):
    """Public schema for optimization trial responses."""

    id: str
    job_id: str
    params: dict[str, float]
    sharpe: float
    max_drawdown: float
    hit_rate: float
    trade_count: int
    total_return: float
    profit_factor: float
    avg_win: float | None
    avg_loss: float | None
    max_consecutive_losses: int | None
    avg_trade_duration: float | None
    total_pips: float | None
    net_pips: float | None
    gross_pnl_dollars: float | None
    net_pnl_dollars: float | None
    passes_gate_b: bool
    gate_b_failures: list[str] | None
    created_at: datetime


class OptimizationTrialList(BaseSchema):
    """List of optimization trials for a job."""

    items: list[OptimizationTrialPublic]
    total: int
    job_id: str


# =============================================================================
# Trade Signal Schemas
# =============================================================================


class TradeSignalPublic(BaseSchema):
    """Trade signal for chart visualization."""

    timestamp: datetime
    signal: int = Field(..., description="-1 = short, 0 = flat, 1 = long")
    signal_type: str = Field(
        ..., description="entry_long, exit_long, entry_short, exit_short"
    )
    price: float
    pnl: float | None = None
    indicator_values: dict[str, float] | None = None


class TradeSignalList(BaseSchema):
    """List of trade signals for a trial."""

    items: list[TradeSignalPublic]
    total: int
    trial_id: str


# =============================================================================
# Trade Schemas
# =============================================================================


class OptimizationTradePublic(BaseSchema):
    """Individual completed trade with financing breakdown."""

    id: str
    trial_id: str
    direction: str  # 'long' or 'short'
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    # Results without financing
    pips: float
    # Financing costs
    spread_cost_pips: float
    swap_cost_pips: float
    # Results with financing
    net_pips: float
    # Dollar values
    gross_pnl_dollars: float
    spread_cost_dollars: float
    swap_cost_dollars: float
    net_pnl_dollars: float
    # Metadata
    duration_minutes: int
    position_size: int


class OptimizationTradeList(BaseSchema):
    """List of trades for a trial."""

    items: list[OptimizationTradePublic]
    total: int
    trial_id: str


# =============================================================================
# Trial Detail Schema (for visualization)
# =============================================================================


class CandleData(BaseModel):
    """OHLCV candle data for chart."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorLine(BaseModel):
    """Technical indicator line data."""

    name: str
    color: str
    data: list[dict[str, Any]]  # [{timestamp, value}]


class TrialDetailData(BaseSchema):
    """Complete trial detail with chart data."""

    trial: OptimizationTrialPublic
    job: OptimizationJobPublic
    candles: list[CandleData]
    signals: list[TradeSignalPublic]
    indicators: list[IndicatorLine]
    trades: list[OptimizationTradePublic]
    extended_metrics: TrialMetricsExtended


# =============================================================================
# Gate B Threshold Constants
# =============================================================================


class GateBThresholds(BaseModel):
    """Gate B validation thresholds."""

    sharpe_min: float = 1.0
    max_drawdown: float = 0.10  # 10%
    hit_rate_min: float = 0.48  # 48%
    min_trades: int = 300


# Default thresholds
GATE_B_THRESHOLDS = GateBThresholds()


def evaluate_gate_b(metrics: TrialMetrics) -> tuple[bool, list[str]]:
    """
    Evaluate whether metrics pass Gate B thresholds.

    Returns:
        Tuple of (passes: bool, failures: list[str])
    """
    failures: list[str] = []

    if metrics.sharpe < GATE_B_THRESHOLDS.sharpe_min:
        failures.append(f"Sharpe {metrics.sharpe:.2f} < {GATE_B_THRESHOLDS.sharpe_min}")

    if metrics.max_drawdown > GATE_B_THRESHOLDS.max_drawdown:
        failures.append(
            f"Max DD {metrics.max_drawdown * 100:.1f}% > {GATE_B_THRESHOLDS.max_drawdown * 100}%"
        )

    if metrics.hit_rate < GATE_B_THRESHOLDS.hit_rate_min:
        failures.append(
            f"Hit Rate {metrics.hit_rate * 100:.1f}% < {GATE_B_THRESHOLDS.hit_rate_min * 100}%"
        )

    if metrics.trade_count < GATE_B_THRESHOLDS.min_trades:
        failures.append(
            f"Trade Count {metrics.trade_count} < {GATE_B_THRESHOLDS.min_trades}"
        )

    return len(failures) == 0, failures
