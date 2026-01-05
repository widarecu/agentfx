"""Resilient candle ingestion worker with intelligent backfill.

This worker runs as a background task during the FastAPI server lifecycle.
It is the SINGLE SOURCE OF TRUTH for fetching candle data from OANDA.

Handles:
1. Initial backfill on first startup (no existing data)
2. Gap backfill on subsequent startups (filling gaps from downtime)
3. Continuous polling to keep data current

The seed_instruments.py script handles instrument METADATA only.
This worker handles ALL candle data operations.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from ..core.config import settings

logger = logging.getLogger(__name__)

# Worker state
_worker_task: asyncio.Task | None = None
_shutdown_event: asyncio.Event | None = None

# Configuration - instruments and timeframes to ingest
INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "USD_CAD"]
GRANULARITIES = ["M1", "M5", "M15", "H1"]

# Retry configuration
MAX_BACKOFF_SECONDS = 300  # Max 5 minutes between retries
INITIAL_BACKOFF_SECONDS = 5

# Backfill configuration
# BACKFILL_DAYS is now configurable via settings.BACKFILL_DAYS (default 7, set to 1825 for 5 years)
MAX_CANDLES_PER_REQUEST = 500  # OANDA API limit

# Granularity to seconds mapping for time-based pagination
# OANDA API doesn't allow count + from/to together, so we paginate by time windows
GRANULARITY_SECONDS = {
    "S5": 5,
    "S10": 10,
    "S15": 15,
    "S30": 30,
    "M1": 60,
    "M2": 120,
    "M4": 240,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H3": 10800,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D": 86400,
    "W": 604800,
    "M": 2592000,
}


def _get_backfill_start_time(days: int | None = None) -> datetime:
    """Calculate backfill start time based on configured days.

    Args:
        days: Number of days to backfill. If None, uses settings.BACKFILL_DAYS.
    """
    backfill_days = days if days is not None else settings.BACKFILL_DAYS
    return datetime.now(timezone.utc) - timedelta(days=backfill_days)


def fetch_candles_range(
    instrument: str,
    granularity: str,
    from_time: datetime,
    to_time: datetime | None = None,
) -> list[dict]:
    """Fetch candles for a time range from OANDA.

    Handles pagination for large ranges automatically.

    Args:
        instrument: The instrument symbol (e.g., EUR_USD)
        granularity: The candle granularity (e.g., M1, H1)
        from_time: Start of the time range
        to_time: End of the time range (defaults to now)

    Returns:
        List of candle dictionaries
    """
    from agentfx.adapters.oanda import OandaClient, OandaError

    client = OandaClient(
        api_key=settings.OANDA_API_KEY,
        host=settings.OANDA_API_HOST,
    )

    all_candles = []
    current_from = from_time
    to_time = to_time or datetime.now(timezone.utc)

    # Calculate chunk duration based on granularity
    # OANDA API doesn't allow count + from/to together, so we paginate by time windows
    granularity_secs = GRANULARITY_SECONDS.get(granularity, 60)
    # Use slightly fewer candles than max to ensure we don't hit limits
    chunk_duration = timedelta(
        seconds=granularity_secs * (MAX_CANDLES_PER_REQUEST - 100)
    )

    last_seen_time = None  # Track last candle time to detect duplicates

    while current_from < to_time:
        try:
            # Calculate chunk end time (don't exceed to_time)
            chunk_end = min(current_from + chunk_duration, to_time)

            candles = client.get_candles(
                instrument=instrument,
                granularity=granularity,
                from_ts=current_from,
                to_ts=chunk_end,
                # NO count parameter - use time range only (OANDA API constraint)
                include_first=True,
            )

            if not candles:
                # No data in this chunk - move to next
                current_from = chunk_end + timedelta(seconds=1)
                continue

            # Parse last candle time
            last_time_str = candles[-1]["time"]
            if last_time_str.endswith("Z"):
                last_time_str = last_time_str[:-1] + "+00:00"
            last_time = datetime.fromisoformat(last_time_str)

            # If we're receiving candles we've already seen, we're done
            # This handles both real API pagination and mocked tests
            if last_seen_time is not None and last_time <= last_seen_time:
                break

            all_candles.extend(candles)
            last_seen_time = last_time

            # Move past the last candle we received
            current_from = last_time + timedelta(seconds=1)

            # If we've passed the target time, we're done
            if current_from >= to_time:
                break

        except OandaError as e:
            logger.warning(f"OANDA API error during backfill: {e}")
            break

    return all_candles


def store_candles(
    instrument: str,
    granularity: str,
    candles: list[dict],
    db=None,
) -> int:
    """Store candles via MarketDataService.

    Args:
        instrument: The instrument symbol
        granularity: The candle granularity
        candles: List of candle dictionaries from OANDA
        db: Optional database session for testing. If None, creates a new session.

    Returns:
        Number of candles stored
    """
    from ..services.market_data import MarketDataService
    from ..core.database import SessionLocal

    if not candles:
        return 0

    candles_data = [
        {
            "timestamp": candle["time"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
            "complete": candle.get("complete", True),
        }
        for candle in candles
    ]

    # Support session injection for testing
    should_close = db is None
    if db is None:
        db = SessionLocal()

    try:
        result = MarketDataService.create_candles(
            db=db,
            exchange="oanda",
            instrument=instrument,
            timeframe=granularity,
            candles_data=candles_data,
            update_if_exists=True,
        )
        return len(result)
    finally:
        if should_close:
            db.close()


def backfill_instrument(instrument: str, granularity: str, db=None) -> int:
    """Backfill candles for a single instrument/granularity.

    Detects gaps and fills appropriately:
    - If no data exists: fetch INITIAL_BACKFILL_DAYS of history
    - If data exists: fetch from last candle to now

    This is the key function that ensures data continuity across server restarts.

    Args:
        instrument: The instrument symbol
        granularity: The candle granularity
        db: Optional database session for testing. If None, creates a new session.

    Returns:
        Number of candles stored
    """
    from ..services.market_data import MarketDataService
    from ..core.database import SessionLocal

    # Support session injection for testing
    should_close = db is None
    if db is None:
        db = SessionLocal()

    try:
        latest = MarketDataService.get_latest_candle(
            db=db,
            exchange="oanda",
            instrument=instrument,
            timeframe=granularity,
        )

        if latest is None:
            # No data - full backfill
            from_time = _get_backfill_start_time()
            logger.info(
                f"No existing data for {instrument}:{granularity}, "
                f"backfilling {settings.BACKFILL_DAYS} days of history"
            )
        else:
            # Gap fill from last candle
            from_time = latest.timestamp + timedelta(seconds=1)
            gap_duration = datetime.now(timezone.utc) - from_time

            # Skip if gap is negligible (< 1 minute)
            if gap_duration.total_seconds() < 60:
                logger.debug(f"No gap to fill for {instrument}:{granularity}")
                return 0

            hours = gap_duration.total_seconds() / 3600
            logger.info(
                f"Backfilling gap for {instrument}:{granularity}: "
                f"{hours:.1f} hours since last candle"
            )
    finally:
        if should_close:
            db.close()

    # Fetch and store (pass db if we're using injected session)
    candles = fetch_candles_range(instrument, granularity, from_time)
    stored = store_candles(
        instrument, granularity, candles, db=db if not should_close else None
    )

    if stored > 0:
        logger.info(f"Backfilled {stored} candles for {instrument}:{granularity}")

    return stored


async def startup_backfill() -> int:
    """Run initial backfill for all instruments at startup.

    This runs ONCE when the worker starts, before entering the polling loop.
    It ensures all instruments have current data regardless of how long
    the server was down.

    Returns:
        Total number of candles stored across all instruments
    """
    import concurrent.futures

    logger.info("Starting candle backfill...")
    logger.info(f"Instruments: {INSTRUMENTS}, Granularities: {GRANULARITIES}")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    loop = asyncio.get_event_loop()

    total_stored = 0

    for instrument in INSTRUMENTS:
        if _shutdown_event and _shutdown_event.is_set():
            break

        for granularity in GRANULARITIES:
            if _shutdown_event and _shutdown_event.is_set():
                break

            try:
                stored = await loop.run_in_executor(
                    executor,
                    backfill_instrument,
                    instrument,
                    granularity,
                )
                total_stored += stored
            except Exception as e:
                logger.error(f"Backfill failed for {instrument}:{granularity}: {e}")

    logger.info(f"Backfill complete: {total_stored} total candles stored")
    return total_stored


def fetch_and_store_recent(instrument: str, granularity: str, count: int = 10) -> int:
    """Fetch recent candles for continuous polling.

    This is used in the main ingestion loop to keep data current.

    Args:
        instrument: The instrument symbol
        granularity: The candle granularity
        count: Number of recent candles to fetch

    Returns:
        Number of candles stored
    """
    from agentfx.adapters.oanda import OandaClient, OandaError

    try:
        client = OandaClient(
            api_key=settings.OANDA_API_KEY,
            host=settings.OANDA_API_HOST,
        )
        candles = client.get_candles(
            instrument=instrument,
            granularity=granularity,
            count=count,
        )
        return store_candles(instrument, granularity, candles)
    except OandaError as e:
        logger.warning(f"OANDA API error for {instrument}/{granularity}: {e}")
        raise


async def ingestion_loop():
    """Main ingestion loop with resilient retry logic.

    Flow:
    1. Run startup_backfill() to fill any gaps
    2. Enter continuous polling mode
    3. Fetch recent candles at INGESTION_POLL_INTERVAL
    """
    import concurrent.futures

    # Run initial backfill first
    await startup_backfill()

    logger.info(
        f"Entering continuous polling mode "
        f"(interval: {settings.INGESTION_POLL_INTERVAL}s)"
    )

    consecutive_failures = 0
    backoff_seconds = INITIAL_BACKOFF_SECONDS
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    loop = asyncio.get_event_loop()

    while not _shutdown_event.is_set():
        try:
            cycle_start = datetime.now(timezone.utc)
            total_stored = 0

            for instrument in INSTRUMENTS:
                if _shutdown_event.is_set():
                    break

                for granularity in GRANULARITIES:
                    if _shutdown_event.is_set():
                        break

                    try:
                        stored = await loop.run_in_executor(
                            executor,
                            fetch_and_store_recent,
                            instrument,
                            granularity,
                            10,  # Fetch last 10 candles
                        )
                        total_stored += stored
                    except Exception as e:
                        logger.warning(
                            f"Failed to ingest {instrument}/{granularity}: {e}"
                        )

            cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            logger.debug(
                f"Ingestion cycle completed: {total_stored} candles in {cycle_duration:.1f}s"
            )

            # Reset backoff on successful cycle
            consecutive_failures = 0
            backoff_seconds = INITIAL_BACKOFF_SECONDS

            # Wait for next poll interval
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=settings.INGESTION_POLL_INTERVAL,
            )

        except TimeoutError:
            # Normal timeout from wait_for - continue to next cycle
            continue

        except asyncio.CancelledError:
            logger.info("Ingestion worker cancelled")
            break

        except Exception as e:
            consecutive_failures += 1
            logger.error(
                f"Ingestion cycle failed (attempt {consecutive_failures}): {e}"
            )

            # Exponential backoff with max limit
            backoff_seconds = min(backoff_seconds * 2, MAX_BACKOFF_SECONDS)

            logger.info(f"Retrying in {backoff_seconds} seconds...")

            try:
                await asyncio.wait_for(_shutdown_event.wait(), timeout=backoff_seconds)
            except TimeoutError:
                continue

    logger.info("Candle ingestion worker stopped")


async def start_ingestion_worker() -> None:
    """Start the candle ingestion background worker.

    This should be called from FastAPI lifespan startup.
    The worker will run until stop_ingestion_worker() is called.
    """
    global _worker_task, _shutdown_event

    # Skip if ingestion is disabled
    if not settings.INGESTION_ENABLED:
        logger.info("Ingestion worker disabled via INGESTION_ENABLED setting")
        return

    # Skip if OANDA credentials not configured
    if not settings.OANDA_API_KEY or settings.OANDA_API_KEY == "your-api-key-here":
        logger.warning(
            "OANDA credentials not configured - ingestion worker disabled. "
            "Set OANDA_API_KEY in environment to enable."
        )
        return

    # INGESTION_ENABLED is the single source of truth for controlling ingestion.
    # Set INGESTION_ENABLED=false in .env.test if you want to disable ingestion in tests.

    if _worker_task is not None:
        logger.warning("Ingestion worker already running")
        return

    _shutdown_event = asyncio.Event()
    _worker_task = asyncio.create_task(ingestion_loop())

    logger.info(
        f"Candle ingestion worker started: "
        f"{len(INSTRUMENTS)} instruments, {len(GRANULARITIES)} granularities, "
        f"polling every {settings.INGESTION_POLL_INTERVAL}s"
    )


async def stop_ingestion_worker() -> None:
    """Stop the candle ingestion background worker.

    This should be called from FastAPI lifespan shutdown.
    """
    global _worker_task, _shutdown_event

    if _worker_task is None:
        return

    logger.info("Stopping candle ingestion worker...")

    # Signal shutdown
    if _shutdown_event:
        _shutdown_event.set()

    # Wait for worker to finish (with timeout)
    try:
        await asyncio.wait_for(_worker_task, timeout=10.0)
    except TimeoutError:
        logger.warning("Ingestion worker did not stop gracefully, cancelling...")
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass

    _worker_task = None
    _shutdown_event = None

    logger.info("Candle ingestion worker stopped")
