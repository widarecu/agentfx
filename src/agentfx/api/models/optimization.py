"""
SQLAlchemy models for Bayesian Parameter Optimization (v0.2.0)

Defines database tables for:
- OptimizationJob: Parameter search jobs with configuration and status
- OptimizationTrial: Individual backtest trials with metrics
- OptimizationSignal: Trade signals for chart visualization
"""

from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class JobStatus(str, Enum):
    """Status states for optimization jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StrategyType(str, Enum):
    """Supported strategy types for optimization."""

    MA = "ma"  # Moving Average Crossover
    RSI = "rsi"  # RSI Mean Reversion
    DONCHIAN = "donchian"  # Donchian Breakout


class OptimizationJob(Base):
    """
    Optimization job representing a Bayesian parameter search.

    Each job searches for optimal parameters for a given strategy
    and forex pair combination.
    """

    __tablename__ = "optimization_jobs"

    id = Column(
        String, primary_key=True, index=True
    )  # Format: optjob_{timestamp}_{short_uuid}

    # Job configuration
    strategy = Column(
        SQLEnum(StrategyType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    pair = Column(String(10), nullable=False, index=True)  # EUR_USD, GBP_USD, etc.

    # Status and progress
    status = Column(
        SQLEnum(JobStatus, values_callable=lambda x: [e.value for e in x]),
        default=JobStatus.QUEUED,
        index=True,
    )
    progress = Column(Integer, default=0)  # 0-100
    estimated_iterations = Column(Integer, nullable=False)
    completed_iterations = Column(Integer, default=0)

    # Best result tracking
    best_sharpe = Column(Float, nullable=True)
    best_trial_id = Column(String, nullable=True)

    # Account/position settings
    account_balance = Column(Float, default=10000.0)  # Starting balance in dollars
    position_size = Column(Integer, default=10000)  # Units (10000 = 0.1 lot)
    data_years = Column(Integer, default=5)  # Years of historical data

    # Parameter ranges (JSON: {param_name: {min, max, step?}})
    param_ranges = Column(JSON, nullable=False)

    # Data range
    data_range_start = Column(DateTime(timezone=True), nullable=True)
    data_range_end = Column(DateTime(timezone=True), nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # User tracking
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Relationships
    trials = relationship(
        "OptimizationTrial",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="desc(OptimizationTrial.created_at)",
    )
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return (
            f"<OptimizationJob {self.id} - {self.strategy}/{self.pair} ({self.status})>"
        )


class OptimizationTrial(Base):
    """
    Single optimization trial result.

    Each trial represents a backtest with specific parameter values,
    storing performance metrics and Gate B evaluation.
    """

    __tablename__ = "optimization_trials"

    id = Column(String, primary_key=True, index=True)  # Format: trial_{job_id}_{index}
    job_id = Column(
        String,
        ForeignKey("optimization_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Trial parameters (JSON: {param_name: value})
    params = Column(JSON, nullable=False)

    # Core metrics
    sharpe = Column(Float, nullable=False)
    max_drawdown = Column(Float, nullable=False)  # 0.06 = 6%
    hit_rate = Column(Float, nullable=False)  # 0.54 = 54%
    trade_count = Column(Integer, nullable=False)
    total_return = Column(Float, nullable=False)  # 0.15 = 15%
    profit_factor = Column(Float, nullable=False)

    # Extended metrics
    avg_win = Column(Float, nullable=True)
    avg_loss = Column(Float, nullable=True)
    max_consecutive_losses = Column(Integer, nullable=True)
    avg_trade_duration = Column(Float, nullable=True)  # in hours

    # Pips-based metrics (without financing)
    total_pips = Column(Float, nullable=True)
    avg_pips_per_trade = Column(Float, nullable=True)

    # Financing breakdown
    total_spread_cost_pips = Column(Float, nullable=True)
    total_swap_cost_pips = Column(Float, nullable=True)
    net_pips = Column(Float, nullable=True)

    # Dollar-based metrics
    gross_pnl_dollars = Column(Float, nullable=True)
    total_spread_cost_dollars = Column(Float, nullable=True)
    total_swap_cost_dollars = Column(Float, nullable=True)
    net_pnl_dollars = Column(Float, nullable=True)

    # Gate B evaluation
    passes_gate_b = Column(Boolean, default=False)
    gate_b_failures = Column(JSON, nullable=True)  # List of failure reasons

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    job = relationship("OptimizationJob", back_populates="trials")
    signals = relationship(
        "OptimizationSignal",
        back_populates="trial",
        cascade="all, delete-orphan",
        order_by="OptimizationSignal.timestamp",
    )
    trades = relationship(
        "OptimizationTrade",
        back_populates="trial",
        cascade="all, delete-orphan",
        order_by="OptimizationTrade.entry_timestamp",
    )

    def __repr__(self):
        return f"<OptimizationTrial {self.id} - Sharpe: {self.sharpe:.2f}>"


class OptimizationSignal(Base):
    """
    Trade signal for chart visualization.

    Stores entry/exit points and indicator values at each signal.
    """

    __tablename__ = "optimization_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trial_id = Column(
        String,
        ForeignKey("optimization_trials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Signal details
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    signal = Column(Integer, nullable=False)  # -1 = short, 0 = flat, 1 = long
    signal_type = Column(String(20), nullable=False)  # entry_long, exit_long, etc.
    price = Column(Float, nullable=False)
    pnl = Column(Float, nullable=True)  # P&L for exit signals

    # Indicator values at signal time (JSON: {indicator_name: value})
    indicator_values = Column(JSON, nullable=True)

    # Relationship
    trial = relationship("OptimizationTrial", back_populates="signals")

    def __repr__(self):
        return f"<OptimizationSignal {self.signal_type} @ {self.price}>"


class OptimizationTrade(Base):
    """
    Individual completed trade record with financing breakdown.

    Stores entry/exit details and calculates P&L with spread and swap costs.
    """

    __tablename__ = "optimization_trades"

    id = Column(
        String, primary_key=True, index=True
    )  # Format: trade_{trial_id}_{index}
    trial_id = Column(
        String,
        ForeignKey("optimization_trials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Trade details
    direction = Column(String(10), nullable=False)  # 'long' or 'short'
    entry_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    exit_timestamp = Column(DateTime(timezone=True), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)

    # Results without financing (raw strategy performance)
    pips = Column(Float, nullable=False)  # P&L in pips

    # Financing/costs
    spread_cost_pips = Column(Float, nullable=False)  # Spread cost in pips
    swap_cost_pips = Column(Float, nullable=False)  # Overnight financing in pips

    # Results with financing
    net_pips = Column(Float, nullable=False)  # pips - spread - swap

    # Dollar values
    gross_pnl_dollars = Column(Float, nullable=False)
    spread_cost_dollars = Column(Float, nullable=False)
    swap_cost_dollars = Column(Float, nullable=False)
    net_pnl_dollars = Column(Float, nullable=False)

    # Metadata
    duration_minutes = Column(Integer, nullable=False)
    position_size = Column(Integer, nullable=False)  # Units

    # Relationship
    trial = relationship("OptimizationTrial", back_populates="trades")

    def __repr__(self):
        return f"<OptimizationTrade {self.direction} {self.pips:.1f} pips>"
