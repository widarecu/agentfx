from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime
from ..models.run import RunStatus, RunEnvironment
from .base import BaseSchema


class RunBase(BaseModel):
    strategy_id: str = Field(..., description="ID of the strategy")
    pair: str = Field(..., description="Trading pair (e.g., 'AUDUSD')")
    environment: RunEnvironment = Field(
        ..., description="Execution environment (dev/paper/live)"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Strategy parameters"
    )
    initial_balance: float = Field(10000.0, description="Initial account balance")


class RunCreate(RunBase):
    pass


class RunUpdate(BaseModel):
    status: RunStatus | None = None
    current_balance: float | None = None
    pnl: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    parameters: dict[str, Any] | None = None


class RunInDBBase(RunBase, BaseSchema):
    id: str
    status: RunStatus
    current_balance: float
    pnl: float
    sharpe_ratio: float | None
    max_drawdown: float | None
    win_rate: float | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


class Run(RunInDBBase):
    pass


class RunInDB(RunInDBBase):
    pass


class RunList(BaseSchema):
    items: list[Run]
    total: int


class TradeBase(BaseModel):
    entry_timestamp: datetime
    exit_timestamp: datetime | None = None
    entry_price: float
    exit_price: float | None = None
    quantity: float
    side: str  # "buy" or "sell"
    pnl: float | None = None
    pnl_pct: float | None = None


class TradeCreate(TradeBase):
    run_id: str


class TradeInDBBase(TradeBase, BaseSchema):
    id: str
    run_id: str
    created_at: datetime
    updated_at: datetime | None


class Trade(TradeInDBBase):
    pass


class OrderBase(BaseModel):
    order_type: str  # "market", "limit", "stop", etc.
    side: str  # "buy" or "sell"
    quantity: float
    price: float | None = None
    status: str = "new"  # new, filled, canceled, rejected
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    metadata: dict[str, Any] | None = None


class OrderCreate(OrderBase):
    run_id: str
    trade_id: str | None = None


class OrderInDBBase(OrderBase, BaseSchema):
    id: str
    run_id: str
    trade_id: str | None
    created_at: datetime
    updated_at: datetime | None
    filled_at: datetime | None


class Order(OrderInDBBase):
    pass


class RunMetrics(BaseModel):
    timestamp: datetime
    run_id: str
    balance: float
    equity: float
    pnl: float
    drawdown: float
    position_size: float | None = None
    position_value: float | None = None
    position_side: str | None = None  # "long", "short", or None
    metrics: dict[str, float] = Field(default_factory=dict)  # Strategy-specific metrics


class RunSignal(BaseModel):
    timestamp: datetime
    run_id: str
    signal_type: str  # e.g., "entry", "exit", "stop_loss", "take_profit"
    price: float
    quantity: float | None = None
    side: str | None = None  # "buy" or "sell"
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
