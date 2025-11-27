from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from ..models.run import RunStatus, RunEnvironment
from .base import BaseSchema

class RunBase(BaseModel):
    strategy_id: str = Field(..., description="ID of the strategy")
    pair: str = Field(..., description="Trading pair (e.g., 'AUDUSD')")
    environment: RunEnvironment = Field(..., description="Execution environment (dev/paper/live)")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Strategy parameters")
    initial_balance: float = Field(10000.0, description="Initial account balance")

class RunCreate(RunBase):
    pass

class RunUpdate(BaseModel):
    status: Optional[RunStatus] = None
    current_balance: Optional[float] = None
    pnl: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    parameters: Optional[Dict[str, Any]] = None

class RunInDBBase(RunBase, BaseSchema):
    id: str
    status: RunStatus
    current_balance: float
    pnl: float
    sharpe_ratio: Optional[float]
    max_drawdown: Optional[float]
    win_rate: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

class Run(RunInDBBase):
    pass

class RunInDB(RunInDBBase):
    pass

class RunList(BaseSchema):
    items: List[Run]
    total: int

class TradeBase(BaseModel):
    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    side: str  # "buy" or "sell"
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None

class TradeCreate(TradeBase):
    run_id: str

class TradeInDBBase(TradeBase, BaseSchema):
    id: str
    run_id: str
    created_at: datetime
    updated_at: Optional[datetime]

class Trade(TradeInDBBase):
    pass

class OrderBase(BaseModel):
    order_type: str  # "market", "limit", "stop", etc.
    side: str  # "buy" or "sell"
    quantity: float
    price: Optional[float] = None
    status: str = "new"  # new, filled, canceled, rejected
    filled_quantity: float = 0.0
    average_fill_price: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None

class OrderCreate(OrderBase):
    run_id: str
    trade_id: Optional[str] = None

class OrderInDBBase(OrderBase, BaseSchema):
    id: str
    run_id: str
    trade_id: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    filled_at: Optional[datetime]

class Order(OrderInDBBase):
    pass

class RunMetrics(BaseModel):
    timestamp: datetime
    run_id: str
    balance: float
    equity: float
    pnl: float
    drawdown: float
    position_size: Optional[float] = None
    position_value: Optional[float] = None
    position_side: Optional[str] = None  # "long", "short", or None
    metrics: Dict[str, float] = Field(default_factory=dict)  # Strategy-specific metrics

class RunSignal(BaseModel):
    timestamp: datetime
    run_id: str
    signal_type: str  # e.g., "entry", "exit", "stop_loss", "take_profit"
    price: float
    quantity: Optional[float] = None
    side: Optional[str] = None  # "buy" or "sell"
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
