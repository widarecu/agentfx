from enum import Enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
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
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class RunEnvironment(str, Enum):
    DEV = "dev"
    PAPER = "paper"
    LIVE = "live"


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
    strategy_id = Column(String, index=True, nullable=False)
    pair = Column(String, index=True, nullable=False)  # Trading pair, e.g., "AUDUSD"
    environment = Column(SQLEnum(RunEnvironment), index=True, nullable=False)
    status = Column(SQLEnum(RunStatus), default=RunStatus.PENDING, index=True)

    # Configuration
    parameters = Column(JSON, nullable=False)  # Strategy parameters
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
