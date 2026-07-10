from enum import Enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy.sql import func

from ..core.database import Base


class RunStatus(str, Enum):
    # NOTE: values MUST match the Postgres `runstatus` enum labels,
    # which the initial migration created UPPERCASE by name. Don't
    # rename these to lowercase without a migration to add both
    # forms — see 2026_07_06_1500_add_paper_trading_columns_to_runs.py
    # for the pattern we used for the v0.6.0 additions.
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    # v0.6.0 paper-trading states (US-021 / US-023). Approval and halt
    # transitions live on the same `status` column so the existing
    # portal list can render every paper row alongside legacy runs
    # without a schema split.
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    HALTED_MANUAL = "HALTED_MANUAL"


class RunEnvironment(str, Enum):
    DEV = "dev"
    PAPER = "paper"
    LIVE = "live"


# The `oanda_environment` column reuses the enum defined for
# user_oanda_credentials so the vocabulary is consistent across the
# app. See src/agentfx/api/models/user_credentials.py::OandaEnvironment.


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name="fk_run_strategy_id",
            ondelete="CASCADE",
        ),
    )

    id = Column(
        String, primary_key=True, index=True
    )  # Format: run_<timestamp>_<strategy>_<pair>_<env>_<id>
    # Nullable since v0.6.0: paper runs are backed by a StrategyGenome,
    # not a Strategy row. Legacy dev/live runs still populate this.
    strategy_id = Column(String, index=True, nullable=True)
    pair = Column(String, index=True, nullable=False)  # Trading pair, e.g., "AUDUSD"
    environment = Column(SQLEnum(RunEnvironment), index=True, nullable=False)
    status = Column(SQLEnum(RunStatus), default=RunStatus.PENDING, index=True)

    # Configuration
    parameters = Column(JSON, nullable=False)  # Strategy parameters / DSL spec
    initial_balance = Column(Float, default=10000.0)

    # Metrics
    current_balance = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)  # Total PnL
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)

    # Timestamps
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # ─────────────────────────────────────────────────────────────
    # v0.6.0 paper trading extension (US-021 / US-023).
    # ─────────────────────────────────────────────────────────────
    # Which validated genome this paper run trades. Null for legacy
    # dev/live runs backed by a Strategy row.
    strategy_genome_id = Column(
        String(120),
        ForeignKey("strategy_genomes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Owner. Duplicates the legacy inferred ownership (strategy.created_by
    # cascade) but is required for the RBAC filter in US-018 that scopes
    # `list_runs` at the query layer.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Which OANDA account this run trades in (picked from the user's
    # detected accounts in the deploy modal). Format matches OANDA:
    # "101-004-12345678-001".
    oanda_account_id = Column(String(64), nullable=True, index=True)
    # PRACTICE for v0.6.0; LIVE gated by ADMIN + v1.0.0.
    oanda_environment = Column(
        SQLEnum(
            "practice",
            "live",
            name="oandaenvironment",
            create_type=False,
        ),
        nullable=True,
    )
    # Balance snapshotted from OANDA on the first tick. Denominator for
    # every P&L % on the run detail page (Wilson's UX answer #4 — user
    # never types an initial balance; it's read from the sub-account).
    snapshot_balance = Column(Float, nullable=True)
    # Human-readable stop reason. Vocabulary: `USER_KILL`,
    # `DAILY_LOSS_LIMIT`, `BROKER_CIRCUIT`, `process_crashed`,
    # `crashed_pre_init`, `COMPLETED`.
    stop_reason = Column(String(40), nullable=True)
    # Set by `POST /paper-runs/:id/kill`; the runner polls this on
    # every loop tick and halts when it flips from NULL to non-NULL.
    kill_requested_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # {"max_position_size_units": 10000, "daily_loss_pct": 2.0,
    #  "kill_switch_dd_pct": 5.0}
    risk_config = Column(JSON, nullable=True)
    # PID of the runner subprocess. Set by `schedule_paper_run`.
    process_pid = Column(Integer, nullable=True)

    # Relationships
    strategy = relationship("Strategy", back_populates="runs")
    trades = relationship("Trade", back_populates="run", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Run {self.id} - {self.strategy_id} ({self.status})>"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(
        String, primary_key=True, index=True
    )  # Format: tr_<timestamp>_<run_id>_<incremental>
    run_id = Column(String, ForeignKey("runs.id"), index=True, nullable=False)

    # Trade details
    entry_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    exit_timestamp = Column(DateTime(timezone=True), nullable=True, index=True)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    side = Column(String, nullable=False)  # "buy" or "sell"

    # Performance metrics
    pnl = Column(Float, nullable=True)  # Realized PnL
    pnl_pct = Column(Float, nullable=True)  # Realized PnL as percentage

    # v0.6.1 (US-024): where did this trade come from? Always 'live' for
    # paper-runner-executed trades; reserved for the v0.7.0 backtest-vs-
    # live overlay work when the same table will hold both.
    origin = Column(String, nullable=True)
    # v0.6.1 (US-024): the indicator condition that triggered the entry,
    # e.g. "SMA_20 > SMA_50" or "MACD ↑". Rendered in the /paper/:id
    # Trades table's "Reason" column per Wilson's UX answer #1.
    signal_reason = Column(String, nullable=True)

    # Relationships
    run = relationship("Run", back_populates="trades")
    orders = relationship("Order", back_populates="trade", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Trade {self.id} - {self.side} {self.quantity} @ {self.entry_price}>"


class Order(Base):
    __tablename__ = "orders"

    id = Column(
        String, primary_key=True, index=True
    )  # Exchange order ID or generated UUID
    trade_id = Column(
        String, ForeignKey("trades.id"), index=True, nullable=True
    )  # Null for standalone orders
    run_id = Column(String, ForeignKey("runs.id"), index=True, nullable=False)

    # Order details
    order_type = Column(String, nullable=False)  # "market", "limit", "stop", etc.
    side = Column(String, nullable=False)  # "buy" or "sell"
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=True)  # Null for market orders
    status = Column(
        String, nullable=False, default="new"
    )  # new, filled, canceled, rejected

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    filled_at = Column(DateTime(timezone=True), nullable=True)

    # Execution details
    filled_quantity = Column(Float, default=0.0)
    average_fill_price = Column(Float, nullable=True)

    # Metadata
    metadata_ = Column("metadata", JSON, nullable=True)  # Additional order metadata

    # Relationships
    run = relationship("Run", back_populates="orders")
    trade = relationship("Trade", back_populates="orders")

    def __repr__(self):
        return f"<Order {self.id} - {self.side} {self.quantity} {self.order_type}>"
