from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    Integer,
    Boolean,
    ForeignKey,
    Index,
)
from sqlalchemy.sql import func
from ..core.database import Base


class Candle(Base):
    """Market data candles (OHLCV) for trading instruments."""

    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(
        String, nullable=False, index=True, default="oanda"
    )  # e.g., "oanda"
    instrument = Column(String, nullable=False, index=True)  # e.g., "AUD_USD"
    timeframe = Column(
        String, nullable=False, index=True
    )  # e.g., "M1", "M5", "H1", "D1"

    # OHLCV data
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)

    # Additional metadata
    complete = Column(
        Boolean, default=True, index=True
    )  # Whether the candle is complete
    spread = Column(Float, nullable=True)  # Spread in pips

    # Relationships
    run_id = Column(String, ForeignKey("runs.id"), index=True, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Add a composite index for common query patterns
    __table_args__ = (
        Index(
            "idx_candles_instrument_tf_ts",
            "instrument",
            "timeframe",
            "timestamp",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<Candle {self.instrument} {self.timeframe} {self.timestamp}>"


class MarketDataUpdate(Base):
    """Tracks the last update time for market data."""

    __tablename__ = "market_data_updates"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String, nullable=False, index=True)
    instrument = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    last_updated = Column(DateTime(timezone=True), nullable=False)

    # Metadata
    last_candle_timestamp = Column(DateTime(timezone=True), nullable=True)
    update_status = Column(String, default="idle")  # idle, updating, error
    error_message = Column(String, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index(
            "idx_market_data_updates_ex_instr_tf",
            "exchange",
            "instrument",
            "timeframe",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<MarketDataUpdate {self.exchange}:{self.instrument}:{self.timeframe} @ {self.last_updated}>"
