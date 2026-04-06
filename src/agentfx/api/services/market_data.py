import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)


class MarketDataService:
    """Service for handling market data operations."""

    @staticmethod
    def _validate_candle_data(candle_data: dict[str, Any]) -> bool:
        """
        Validate candle data for quality and consistency.

        Returns True if valid, raises ValueError if invalid.
        """
        required_fields = {"timestamp", "open", "high", "low", "close"}
        missing_fields = required_fields - set(candle_data.keys())
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        # Validate price relationships
        open_price = float(candle_data["open"])
        high_price = float(candle_data["high"])
        low_price = float(candle_data["low"])
        close_price = float(candle_data["close"])

        if high_price < low_price:
            raise ValueError("High price cannot be lower than low price")

        if high_price < max(open_price, close_price):
            raise ValueError("High price must be >= both open and close")

        if low_price > min(open_price, close_price):
            raise ValueError("Low price must be <= both open and close")

        # Validate non-negative values
        if any(price < 0 for price in [open_price, high_price, low_price, close_price]):
            raise ValueError("Prices cannot be negative")

        # Validate volume
        if "volume" in candle_data and float(candle_data["volume"]) < 0:
            raise ValueError("Volume cannot be negative")

        return True

    @staticmethod
    def _validate_and_clean_candles(
        candles_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Validate, clean, and deduplicate a list of candle data.
        Returns only valid, unique candles, logs warnings for invalid/duplicate ones.

        Deduplication: When duplicate timestamps exist, keeps the last occurrence
        (assuming later data is more up-to-date from API responses).
        """
        # Use dict to deduplicate by timestamp - last occurrence wins
        candles_by_timestamp: dict[str, dict[str, Any]] = {}
        invalid_count = 0

        for i, candle in enumerate(candles_data):
            try:
                MarketDataService._validate_candle_data(candle)
                # Ensure consistent data types
                cleaned_candle = {
                    "timestamp": candle["timestamp"],
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume", 0.0)),
                    "complete": bool(candle.get("complete", True)),
                }

                # Add optional fields if present
                if "spread" in candle:
                    cleaned_candle["spread"] = float(candle["spread"])

                # Store by timestamp, overwriting duplicates
                ts_key = str(candle["timestamp"])
                candles_by_timestamp[ts_key] = cleaned_candle

            except ValueError as e:
                invalid_count += 1
                logger.warning(f"Invalid candle at index {i}: {e}")
                continue

        if invalid_count > 0:
            logger.warning(
                f"Rejected {invalid_count} invalid candles out of {len(candles_data)} total"
            )

        # Count duplicates removed
        duplicates_removed = (
            len(candles_data) - invalid_count - len(candles_by_timestamp)
        )
        if duplicates_removed > 0:
            logger.debug(f"Removed {duplicates_removed} duplicate candles by timestamp")

        return list(candles_by_timestamp.values())

    @staticmethod
    def get_candles(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
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
            query = (
                query.order_by(desc(models.Candle.timestamp))
                .offset(offset)
                .limit(limit)
            )
        else:
            query = query.order_by(models.Candle.timestamp).offset(offset).limit(limit)

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
    def get_earliest_candle(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
    ) -> models.Candle | None:
        """Get the earliest candle for the given parameters."""
        return (
            db.query(models.Candle)
            .filter(
                models.Candle.exchange == exchange,
                models.Candle.instrument == instrument.upper(),
                models.Candle.timeframe == timeframe.upper(),
            )
            .order_by(models.Candle.timestamp.asc())
            .first()
        )

    @staticmethod
    def get_candle_timestamps(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
    ) -> list[datetime]:
        """
        Get all candle timestamps ordered ascending for gap detection.

        Args:
            db: Database session
            exchange: Exchange identifier
            instrument: Instrument symbol
            timeframe: Candle timeframe

        Returns:
            List of timestamps in ascending order
        """
        results = (
            db.query(models.Candle.timestamp)
            .filter(
                models.Candle.exchange == exchange,
                models.Candle.instrument == instrument.upper(),
                models.Candle.timeframe == timeframe.upper(),
            )
            .order_by(models.Candle.timestamp.asc())
            .all()
        )
        return [r[0] for r in results]

    @staticmethod
    def delete_candles(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
    ) -> int:
        """
        Delete all candles for an instrument/timeframe.

        Args:
            db: Database session
            exchange: Exchange identifier
            instrument: Instrument symbol
            timeframe: Candle timeframe

        Returns:
            Number of candles deleted
        """
        count = (
            db.query(models.Candle)
            .filter(
                models.Candle.exchange == exchange,
                models.Candle.instrument == instrument.upper(),
                models.Candle.timeframe == timeframe.upper(),
            )
            .delete()
        )
        db.commit()
        logger.info(f"Deleted {count} candles for {instrument}:{timeframe}")
        return count

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

        # Validate and clean candle data
        validated_candles = MarketDataService._validate_and_clean_candles(candles_data)

        if not validated_candles:
            logger.warning("No valid candles to create after validation")
            return []

        result = []
        created_count = 0
        updated_count = 0
        skipped_count = 0

        # Batch-fetch existing candle timestamps to avoid N individual queries.
        # For a 7-day M1 chunk (~7000 candles), this reduces 7000 SELECTs to 7.
        all_timestamps = [c["timestamp"] for c in validated_candles]
        existing_complete = set()
        existing_incomplete = {}  # timestamp -> Candle object
        BATCH_SIZE = 1000

        if update_if_exists:
            for i in range(0, len(all_timestamps), BATCH_SIZE):
                batch_ts = all_timestamps[i : i + BATCH_SIZE]
                rows = (
                    db.query(models.Candle)
                    .filter(
                        models.Candle.exchange == exchange,
                        models.Candle.instrument == instrument,
                        models.Candle.timeframe == timeframe,
                        models.Candle.timestamp.in_(batch_ts),
                    )
                    .all()
                )
                for row in rows:
                    if row.complete:
                        existing_complete.add(row.timestamp)
                    else:
                        existing_incomplete[row.timestamp] = row

        for candle_data in validated_candles:
            ts = candle_data["timestamp"]

            if ts in existing_complete:
                # Complete candles never change — skip
                skipped_count += 1
                continue

            if ts in existing_incomplete:
                # Update existing incomplete candle
                existing = existing_incomplete[ts]
                for key, value in candle_data.items():
                    setattr(existing, key, value)
                if run_id:
                    existing.run_id = run_id
                db.add(existing)
                result.append(existing)
                updated_count += 1
            else:
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
                created_count += 1

        try:
            db.commit()
            # Log with distinction between created, updated, and skipped
            if skipped_count > 0:
                logger.debug(
                    f"Skipped {skipped_count} complete candles for {instrument}:{timeframe}"
                )
            if created_count > 0:
                logger.info(
                    f"Created {created_count} candles for {instrument}:{timeframe}"
                )
            if updated_count > 0:
                logger.info(
                    f"Updated {updated_count} incomplete candles for {instrument}:{timeframe}"
                )
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to commit candles: {e}")
            raise

        # Convert SQLAlchemy objects to Pydantic schemas for proper serialization
        from agentfx.api.schemas.market_data import CandlePublic

        return [CandlePublic.model_validate(candle) for candle in result]

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

    @staticmethod
    def get_market_data_status(
        db: Session,
        exchange: str | None = None,
        instrument: str | None = None,
        timeframe: str | None = None,
    ) -> list[models.MarketDataUpdate]:
        """
        Get market data status updates with optional filtering.

        Args:
            db: Database session
            exchange: Optional exchange filter
            instrument: Optional instrument filter
            timeframe: Optional timeframe filter

        Returns:
            List of market data status updates
        """
        query = db.query(models.MarketDataUpdate)

        if exchange:
            query = query.filter(models.MarketDataUpdate.exchange == exchange)
        if instrument:
            query = query.filter(
                models.MarketDataUpdate.instrument == instrument.upper()
            )
        if timeframe:
            query = query.filter(models.MarketDataUpdate.timeframe == timeframe.upper())

        return query.order_by(models.MarketDataUpdate.last_updated.desc()).all()

    # Display names and sort order for timeframes (OANDA granularity codes)
    TIMEFRAME_DISPLAY = {
        "S5": "5 Sec",
        "S10": "10 Sec",
        "S15": "15 Sec",
        "S30": "30 Sec",
        "M1": "1 Min",
        "M2": "2 Min",
        "M4": "4 Min",
        "M5": "5 Min",
        "M10": "10 Min",
        "M15": "15 Min",
        "M30": "30 Min",
        "H1": "1 Hour",
        "H2": "2 Hour",
        "H3": "3 Hour",
        "H4": "4 Hour",
        "H6": "6 Hour",
        "H8": "8 Hour",
        "H12": "12 Hour",
        "D": "Daily",
        "W": "Weekly",
        "M": "Monthly",
    }
    TIMEFRAME_ORDER = list(TIMEFRAME_DISPLAY.keys())

    @staticmethod
    def get_timeframes(
        db: Session,
        exchange: str | None = None,
        instrument: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Get list of available timeframes from ingested candle data.

        Args:
            db: Database session
            exchange: Optional exchange filter
            instrument: Optional instrument filter

        Returns:
            List of dicts with 'code' and 'display_name' keys, sorted by duration
        """
        query = db.query(models.Candle.timeframe).distinct()

        if exchange:
            query = query.filter(models.Candle.exchange == exchange)
        if instrument:
            query = query.filter(models.Candle.instrument == instrument.upper())

        timeframes = [row[0] for row in query.all()]
        order = MarketDataService.TIMEFRAME_ORDER
        timeframes.sort(key=lambda t: order.index(t) if t in order else 999)
        display = MarketDataService.TIMEFRAME_DISPLAY
        return [{"code": tf, "display_name": display.get(tf, tf)} for tf in timeframes]

    @staticmethod
    def get_instruments(
        db: Session,
        exchange: str | None = None,
    ) -> list[str]:
        """
        Get list of available instruments from the instruments table.

        Args:
            db: Database session
            exchange: Optional exchange filter (not used currently, for future extension)

        Returns:
            List of instrument symbols
        """
        # Query the instruments table which is seeded by seed_instruments.py
        # This ensures instruments are available even before candle data is ingested
        query = db.query(models.Instrument.symbol).filter(
            models.Instrument.is_active.is_(True)
        )

        instruments = [row[0] for row in query.all()]
        return sorted(instruments)

    @staticmethod
    def get_exchange_info(
        db: Session,
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """
        Get exchange information and statistics.

        Args:
            db: Database session
            exchange: Optional exchange filter

        Returns:
            Dictionary with exchange information
        """
        query = db.query(models.Candle)

        if exchange:
            query = query.filter(models.Candle.exchange == exchange)

        # Get basic statistics
        total_candles = query.count()
        exchanges = db.query(models.Candle.exchange).distinct().all()
        exchange_list = [e[0] for e in exchanges]
        instruments = db.query(models.Candle.instrument).distinct().all()
        instrument_list = [i[0] for i in instruments]

        # Get time range
        first_candle = query.order_by(models.Candle.timestamp.asc()).first()
        last_candle = query.order_by(models.Candle.timestamp.desc()).first()

        return {
            "exchange": exchange or "all",
            "total_candles": total_candles,
            "available_exchanges": exchange_list,
            "available_instruments": sorted(instrument_list),
            "data_start_time": first_candle.timestamp if first_candle else None,
            "data_end_time": last_candle.timestamp if last_candle else None,
        }


# Create a singleton instance
market_data_service = MarketDataService()
