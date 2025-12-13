"""Instrument metadata model."""

from sqlalchemy import Column, String, Integer, Boolean, Float, DateTime
from sqlalchemy.sql import func

from ..core.database import Base


class Instrument(Base):
    """Tradeable instrument metadata.

    This table stores metadata about supported trading instruments.
    It is populated once during initial deployment via seed_instruments.py.
    """

    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, unique=True, index=True)  # e.g., "EUR_USD"
    display_name = Column(String, nullable=False)  # e.g., "EUR/USD"
    instrument_type = Column(
        String, nullable=False, default="CURRENCY"
    )  # CURRENCY, CFD, etc.

    # Trading parameters
    pip_location = Column(Integer, default=-4)  # For EUR_USD, pip is at 0.0001
    trade_units_precision = Column(Integer, default=0)
    minimum_trade_size = Column(Float, default=1.0)
    maximum_trade_size = Column(Float, default=10000000.0)

    # Status
    is_tradeable = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)  # Soft delete flag

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Instrument {self.symbol}>"
