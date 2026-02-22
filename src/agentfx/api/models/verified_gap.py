from sqlalchemy import Column, DateTime, Float, Index, Integer, String
from sqlalchemy.sql import func

from ..core.database import Base


class VerifiedEmptyGap(Base):
    """A gap in candle data that OANDA confirmed has no data.

    When detect_and_heal_gaps() fetches from OANDA for a gap and receives
    no candles, the gap is recorded here. Subsequent health checks skip
    these gaps until GAP_REVERIFY_DAYS elapses.
    """

    __tablename__ = "verified_empty_gaps"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String, nullable=False, default="oanda")
    instrument = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    gap_start = Column(DateTime(timezone=True), nullable=False)
    gap_end = Column(DateTime(timezone=True), nullable=False)
    gap_duration_seconds = Column(Float, nullable=False)
    attempt_count = Column(Integer, default=1, nullable=False)
    first_verified_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_verified_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason = Column(String, nullable=True)

    __table_args__ = (
        Index(
            "idx_verified_gaps_lookup",
            "exchange",
            "instrument",
            "timeframe",
            "gap_start",
            "gap_end",
            unique=True,
        ),
        Index(
            "idx_verified_gaps_instrument_tf",
            "instrument",
            "timeframe",
        ),
    )

    def __repr__(self):
        return (
            f"<VerifiedEmptyGap {self.instrument}:{self.timeframe} "
            f"{self.gap_start} to {self.gap_end}>"
        )
