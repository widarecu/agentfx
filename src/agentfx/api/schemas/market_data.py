from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Any
from enum import Enum
from .base import BaseSchema


class Timeframe(str, Enum):
    """Supported timeframes for market data."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


class CandleBase(BaseModel):
    """Base candle schema."""

    exchange: str = Field(..., description="Exchange identifier (e.g., 'oanda')")
    instrument: str = Field(..., description="Instrument symbol (e.g., 'AUD_USD')")
    timeframe: Timeframe = Field(..., description="Candle timeframe")
    timestamp: datetime = Field(..., description="Candle open time")
    open: float = Field(..., description="Open price")
    high: float = Field(..., description="Highest price")
    low: float = Field(..., description="Lowest price")
    close: float = Field(..., description="Close price")
    volume: float = Field(0.0, description="Trading volume")
    complete: bool = Field(True, description="Whether the candle is complete")
    spread: float | None = Field(None, description="Spread in pips")


class CandleCreate(CandleBase):
    """Schema for creating a new candle."""

    pass


class CandleUpdate(BaseModel):
    """Schema for updating a candle."""

    close: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    complete: bool | None = None
    spread: float | None = None


class CandleInDBBase(CandleBase, BaseSchema):
    """Base schema for candles in the database."""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


class CandlePublic(CandleInDBBase):
    """Schema for public candle data."""

    pass


class CandleList(BaseSchema):
    """Schema for a list of candles with pagination."""

    items: list[CandlePublic]
    total: int
    from_time: datetime
    to_time: datetime
    count: int


class CandleBulkCreate(BaseModel):
    """Schema for bulk candle creation."""

    exchange: str
    instrument: str
    timeframe: Timeframe
    candles: list[dict[str, Any]]

    @validator("candles")
    def validate_candles(cls, v):
        required_fields = {"timestamp", "open", "high", "low", "close"}
        for candle in v:
            if not all(field in candle for field in required_fields):
                missing = required_fields - set(candle.keys())
                raise ValueError(f"Missing required fields in candle data: {missing}")
        return v


class MarketDataUpdateStatus(str, Enum):
    IDLE = "idle"
    UPDATING = "updating"
    ERROR = "error"


class MarketDataUpdateBase(BaseModel):
    """Base schema for market data update status."""

    exchange: str
    instrument: str
    timeframe: Timeframe
    last_updated: datetime
    last_candle_timestamp: datetime | None = None
    update_status: MarketDataUpdateStatus = MarketDataUpdateStatus.IDLE
    error_message: str | None = None


class MarketDataUpdateCreate(MarketDataUpdateBase):
    """Schema for creating a market data update record."""

    pass


class MarketDataUpdateInDB(MarketDataUpdateBase, BaseSchema):
    """Schema for market data update records in the database."""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


class MarketDataUpdatePublic(MarketDataUpdateInDB):
    """Public schema for market data update records."""

    pass


class InstrumentInfo(BaseModel):
    """Basic information about a trading instrument."""

    symbol: str
    name: str
    type: str  # e.g., "forex", "stock", "crypto"
    pip_location: int = 4  # Number of decimal places for pip
    display_precision: int = 5  # Number of decimal places to display
    trade_units_precision: int = 2  # Precision for trade size
    margin_rate: float | None = None  # Margin requirement as a percentage
    minimum_trade_size: float = 1.0
    maximum_trade_size: float = 10000000.0
    minimum_trailing_stop_distance: float | None = None
    maximum_trailing_stop_distance: float | None = None
    minimum_guaranteed_stop_loss_distance: float | None = None
    maximum_guaranteed_stop_loss_distance: float | None = None
    minimum_distance: float | None = None  # Minimum distance from market
    maximum_position_size: float | None = None
    maximum_order_units: float | None = None
    tags: list[str] = []


class ExchangeInfo(BaseModel):
    """Information about a trading exchange."""

    id: str
    name: str
    status: str  # e.g., "online", "maintenance", "offline"
    timezone: str
    server_time: datetime
    available_instruments: list[InstrumentInfo] = []
    supported_timeframes: list[Timeframe] = []
    rate_limit_requests_per_second: int | None = None
    rate_limit_requests_per_minute: int | None = None
    rate_limit_requests_per_day: int | None = None
    is_authenticated: bool = False
    features: dict[str, bool] = {
        "historical_data": True,
        "streaming": True,
        "fractional_pips": False,
        "guaranteed_stop_loss": False,
        "trailing_stop": True,
    }
