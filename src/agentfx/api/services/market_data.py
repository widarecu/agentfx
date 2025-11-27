from typing import Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import models


class MarketDataService:
    """Service for handling market data operations."""

    @staticmethod
    def get_candles(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 1000,
        run_id: str | None = None,
    ) -> tuple[list[models.Candle], int]:
        """
        Retrieve candles with optional filtering.

        Args:
            db: Database session
            exchange: Exchange identifier (e.g., 'oanda')
            instrument: Instrument symbol (e.g., 'AUD_USD')
            timeframe: Candle timeframe (e.g., 'M1', 'M5', 'H1')
            from_time: Filter candles after this timestamp
            to_time: Filter candles before this timestamp
            limit: Maximum number of candles to return
            run_id: Optional run ID to filter by

        Returns:
            Tuple of (list of candles, total count)
        """
        query = db.query(models.Candle).filter(
            models.Candle.exchange == exchange,
            models.Candle.instrument == instrument.upper(),
            models.Candle.timeframe == timeframe.upper(),
        )

        if from_time:
            query = query.filter(models.Candle.timestamp >= from_time)
        if to_time:
            query = query.filter(models.Candle.timestamp <= to_time)
        if run_id:
            query = query.filter(models.Candle.run_id == run_id)

        total = query.count()

        # Get the most recent candles if no time range is specified
        if not from_time and not to_time:
            query = query.order_by(desc(models.Candle.timestamp)).limit(limit)
        else:
            query = query.order_by(models.Candle.timestamp).limit(limit)

        return query.all(), total

    @staticmethod
    def get_latest_candle(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        run_id: str | None = None,
    ) -> models.Candle | None:
        """Get the most recent candle for the given parameters."""
        query = db.query(models.Candle).filter(
            models.Candle.exchange == exchange,
            models.Candle.instrument == instrument.upper(),
            models.Candle.timeframe == timeframe.upper(),
        )

        if run_id:
            query = query.filter(models.Candle.run_id == run_id)

        return query.order_by(desc(models.Candle.timestamp)).first()

    @staticmethod
    def create_candles(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        candles_data: list[dict[str, Any]],
        run_id: str | None = None,
        update_if_exists: bool = True,
    ) -> list[models.Candle]:
        """
        Create multiple candles, with optional update if they already exist.

        Args:
            db: Database session
            exchange: Exchange identifier
            instrument: Instrument symbol
            timeframe: Candle timeframe
            candles_data: List of candle data dictionaries
            run_id: Optional run ID to associate with the candles
            update_if_exists: Whether to update existing candles

        Returns:
            List of created/updated candle objects
        """
        instrument = instrument.upper()
        timeframe = timeframe.upper()
        result = []

        for candle_data in candles_data:
            # Check if candle already exists
            existing = (
                db.query(models.Candle)
                .filter(
                    models.Candle.exchange == exchange,
                    models.Candle.instrument == instrument,
                    models.Candle.timeframe == timeframe,
                    models.Candle.timestamp == candle_data["timestamp"],
                )
                .first()
            )

            if existing and update_if_exists:
                # Update existing candle
                for key, value in candle_data.items():
                    setattr(existing, key, value)
                if run_id:
                    existing.run_id = run_id
                db.add(existing)
                result.append(existing)
            elif not existing:
                # Create new candle
                db_candle = models.Candle(
                    exchange=exchange,
                    instrument=instrument,
                    timeframe=timeframe,
                    run_id=run_id,
                    **candle_data,
                )
                db.add(db_candle)
                result.append(db_candle)

        db.commit()
        return result

    @staticmethod
    def update_market_data_status(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        last_updated: datetime,
        status: str = "idle",
        last_candle_timestamp: datetime | None = None,
        error_message: str | None = None,
    ) -> models.MarketDataUpdate:
        """
        Update the status of market data for a specific instrument and timeframe.
        """
        # Try to get existing record
        update = (
            db.query(models.MarketDataUpdate)
            .filter(
                models.MarketDataUpdate.exchange == exchange,
                models.MarketDataUpdate.instrument == instrument.upper(),
                models.MarketDataUpdate.timeframe == timeframe.upper(),
            )
            .first()
        )

        if update:
            # Update existing record
            update.last_updated = last_updated
            update.update_status = status
            update.last_candle_timestamp = last_candle_timestamp
            update.error_message = error_message
        else:
            # Create new record
            update = models.MarketDataUpdate(
                exchange=exchange,
                instrument=instrument.upper(),
                timeframe=timeframe.upper(),
                last_updated=last_updated,
                update_status=status,
                last_candle_timestamp=last_candle_timestamp,
                error_message=error_message,
            )

        db.add(update)
        db.commit()
        db.refresh(update)
        return update


# Create a singleton instance
market_data_service = MarketDataService()
