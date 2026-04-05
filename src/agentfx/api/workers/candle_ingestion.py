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
import time
from datetime import UTC, datetime, timedelta

from ..core.config import settings
from .market_calendar import is_market_closure

logger = logging.getLogger(__name__)

# Worker state
_worker_task: asyncio.Task | None = None
_shutdown_event: asyncio.Event | None = None
_last_health_check: datetime | None = None

# Retry configuration
MAX_BACKOFF_SECONDS = 300  # Max 5 minutes between retries
INITIAL_BACKOFF_SECONDS = 5

# Backfill configuration
# BACKFILL_DAYS is now configurable via settings.BACKFILL_DAYS (default 7, set to 1825 for 5 years)
MAX_CANDLES_PER_REQUEST = 500  # OANDA API limit

# Large gap healing configuration
LARGE_GAP_CHUNK_DAYS = 7  # Process large gaps in 7-day chunks
LARGE_GAP_THRESHOLD_SECONDS = (
    7 * 86400
)  # 7 days - gaps larger than this use chunked healing

# Gap detection sensitivity: flag gaps exceeding this many candle intervals.
# For M1 (60s): gaps > 300s (5 min). For H1 (3600s): gaps > 18000s (5 hours).
# The market_calendar then filters out weekends/holidays/early closes.
GAP_DETECTION_MULTIPLIER = 5

# Adaptive health check: re-run after this many seconds when gaps remain
CATCH_UP_HEALTH_CHECK_INTERVAL = 300  # 5 minutes

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
    return datetime.now(UTC) - timedelta(days=backfill_days)


def fetch_candles_range(
    instrument: str,
    granularity: str,
    from_time: datetime,
    to_time: datetime | None = None,
    max_retries: int = 3,
) -> list[dict]:
    """Fetch candles for a time range from OANDA.

    Handles pagination for large ranges automatically. Includes retry logic
    for transient OANDA API errors (504 gateway timeout, 429 rate limit).

    Args:
        instrument: The instrument symbol (e.g., EUR_USD)
        granularity: The candle granularity (e.g., M1, H1)
        from_time: Start of the time range
        to_time: End of the time range (defaults to now)
        max_retries: Maximum number of retry attempts for transient errors

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
    to_time = to_time or datetime.now(UTC)

    # Calculate chunk duration based on granularity
    # OANDA API doesn't allow count + from/to together, so we paginate by time windows
    granularity_secs = GRANULARITY_SECONDS.get(granularity, 60)
    # Use slightly fewer candles than max to ensure we don't hit limits
    chunk_duration = timedelta(
        seconds=granularity_secs * (MAX_CANDLES_PER_REQUEST - 100)
    )

    last_seen_time = None  # Track last candle time to detect duplicates

    while current_from < to_time:
        # Calculate chunk end time (don't exceed to_time)
        chunk_end = min(current_from + chunk_duration, to_time)

        # Fetch with retry logic for transient errors
        candles = None
        for attempt in range(max_retries):
            try:
                candles = client.get_candles(
                    instrument=instrument,
                    granularity=granularity,
                    from_ts=current_from,
                    to_ts=chunk_end,
                    # NO count parameter - use time range only (OANDA API constraint)
                    include_first=True,
                )
                break  # Success

            except OandaError as e:
                error_str = str(e)
                is_retryable = any(
                    code in error_str
                    for code in ("502", "504", "429", "connection_error")
                )

                if is_retryable and attempt < max_retries - 1:
                    backoff = INITIAL_BACKOFF_SECONDS * (2**attempt)
                    logger.warning(
                        f"OANDA API error (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    time.sleep(backoff)
                else:
                    # Non-retryable error or max retries exceeded
                    logger.warning(f"OANDA API error during backfill: {e}")
                    return all_candles  # Return what we have so far

        if candles is None:
            # All retries failed
            return all_candles

        if not candles:
            # No data in this chunk - move to next
            # Use chunk_end (not +1s) to avoid skipping candle at exact boundary
            current_from = chunk_end
            continue

        # Filter out candles that are before the requested start time
        # OANDA with include_first=True may return the last candle BEFORE the range
        # which causes pagination to get stuck in a loop
        current_from_utc = (
            current_from.astimezone(UTC)
            if current_from.tzinfo
            else current_from.replace(tzinfo=UTC)
        )
        valid_candles = []
        for c in candles:
            c_time_str = c["time"]
            if c_time_str.endswith("Z"):
                c_time_str = c_time_str[:-1] + "+00:00"
            c_time = datetime.fromisoformat(c_time_str)
            if c_time >= current_from_utc:
                valid_candles.append(c)

        if not valid_candles:
            # All returned candles were before the requested start
            # This happens when hitting market closures - move to next chunk
            # Use chunk_end (not +1s) to avoid skipping candle at exact boundary
            current_from = chunk_end
            continue

        # Parse last valid candle time
        last_time_str = valid_candles[-1]["time"]
        if last_time_str.endswith("Z"):
            last_time_str = last_time_str[:-1] + "+00:00"
        last_time = datetime.fromisoformat(last_time_str)

        # If we're receiving candles we've already seen, we're done
        # This handles both real API pagination and mocked tests
        if last_seen_time is not None and last_time <= last_seen_time:
            break

        all_candles.extend(valid_candles)
        last_seen_time = last_time

        # Move past the last candle we received
        current_from = last_time + timedelta(seconds=1)

        # If we've passed the target time, we're done
        if current_from >= to_time:
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
    from ..core.database import SessionLocal
    from ..services.market_data import MarketDataService

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


def heal_large_gap(
    instrument: str,
    granularity: str,
    gap_start: datetime,
    gap_end: datetime,
    db=None,
) -> int:
    """
    Heal a large gap by processing in chunks with retry logic.

    Handles OANDA API errors (504 gateway timeout, 429 rate limit) with
    exponential backoff. Processes gaps in LARGE_GAP_CHUNK_DAYS segments
    to avoid API timeouts on large date ranges.

    Args:
        instrument: The instrument symbol (e.g., EUR_USD)
        granularity: The candle granularity (e.g., M1, H1)
        gap_start: Start of the gap (last candle before gap)
        gap_end: End of the gap (first candle after gap)
        db: Optional database session for testing

    Returns:
        Number of candles stored
    """
    from agentfx.adapters.oanda import OandaError

    MAX_RETRIES = 5
    BASE_BACKOFF = 5  # seconds
    RATE_LIMIT_DELAY = 0.5  # seconds between chunks

    total_stored = 0
    current = gap_start + timedelta(seconds=1)
    chunk_count = 0

    gap_days = (gap_end - gap_start).total_seconds() / 86400
    logger.info(
        f"Healing large gap ({gap_days:.1f} days) for {instrument}:{granularity} "
        f"in {LARGE_GAP_CHUNK_DAYS}-day chunks..."
    )

    while current < gap_end:
        chunk_end = min(current + timedelta(days=LARGE_GAP_CHUNK_DAYS), gap_end)
        chunk_count += 1

        # Fetch with retry logic for transient errors
        for attempt in range(MAX_RETRIES):
            try:
                candles = fetch_candles_range(
                    instrument, granularity, current, chunk_end
                )
                if candles:
                    stored = store_candles(instrument, granularity, candles, db)
                    total_stored += stored
                    if stored > 0:
                        logger.info(
                            f"  Chunk {chunk_count}: {current.date()} to {chunk_end.date()} - "
                            f"{stored} candles"
                        )
                    else:
                        logger.info(
                            f"  Chunk {chunk_count}: {current.date()} to {chunk_end.date()} - "
                            f"data already exists"
                        )
                else:
                    logger.info(
                        f"  Chunk {chunk_count}: {current.date()} to {chunk_end.date()} - "
                        f"no data (market closure)"
                    )
                break  # Success - exit retry loop

            except OandaError as e:
                error_str = str(e)
                is_retryable = any(
                    code in error_str
                    for code in ("502", "504", "429", "connection_error")
                )

                if is_retryable and attempt < MAX_RETRIES - 1:
                    backoff = BASE_BACKOFF * (2**attempt)
                    logger.warning(
                        f"OANDA API error on chunk {chunk_count} (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    time.sleep(backoff)
                else:
                    # Non-retryable error or max retries exceeded
                    logger.error(
                        f"Failed to heal chunk {current.date()} to {chunk_end.date()} "
                        f"after {attempt + 1} attempts: {e}"
                    )
                    # Continue to next chunk rather than failing entirely
                    break

            except Exception as e:
                logger.error(f"Unexpected error healing chunk: {e}")
                break

        # Advance past chunk_end to avoid refetching the last candle
        current = chunk_end + timedelta(seconds=1)
        # Rate limiting between chunks to avoid overwhelming the API
        time.sleep(RATE_LIMIT_DELAY)

    logger.info(
        f"Finished healing gap for {instrument}:{granularity}: "
        f"{total_stored} candles across {chunk_count} chunks"
    )
    return total_stored


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
    from ..core.database import SessionLocal
    from ..services.market_data import MarketDataService

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
            gap_duration = datetime.now(UTC) - from_time

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


def validate_backfill_completeness(instrument: str, granularity: str, db=None) -> bool:
    """
    Check if backfill is complete by verifying data starts from expected date.

    Args:
        instrument: The instrument symbol
        granularity: The candle granularity
        db: Optional database session

    Returns:
        True if data starts from expected date, False otherwise
    """
    from ..core.database import SessionLocal
    from ..services.market_data import MarketDataService

    expected_start = _get_backfill_start_time()

    should_close = db is None
    if db is None:
        db = SessionLocal()

    try:
        earliest = MarketDataService.get_earliest_candle(
            db, "oanda", instrument, granularity
        )

        if earliest is None:
            logger.warning(f"No data for {instrument}:{granularity}")
            return False

        # Allow 7 days tolerance for weekends/holidays
        if earliest.timestamp > expected_start + timedelta(days=7):
            logger.warning(
                f"Incomplete backfill for {instrument}:{granularity}: "
                f"data starts {earliest.timestamp.date()}, expected ~{expected_start.date()}"
            )
            return False

        return True
    finally:
        if should_close:
            db.close()


def _merge_nearby_gaps(
    gaps: list[dict],
    granularity_seconds: int,
    merge_threshold_seconds: int | None = None,
) -> list[dict]:
    """Merge gaps that are close together into larger fetch ranges.

    When gap detection uses a tight threshold, many small gaps may be
    adjacent or separated by only a few candles. Merging them into
    contiguous ranges dramatically reduces the number of OANDA API calls.

    Args:
        gaps: List of gap dicts with 'from', 'to', 'duration_seconds' keys.
        granularity_seconds: Candle interval in seconds.
        merge_threshold_seconds: Gaps separated by less than this are merged.
            Defaults to 10 * granularity_seconds.

    Returns:
        List of merged gap dicts.
    """
    if not gaps:
        return []

    if merge_threshold_seconds is None:
        # Merge gaps within 60 candle intervals of each other (1 hour for M1)
        # to reduce API calls — fetching overlapping data is safe (upsert handles it)
        merge_threshold_seconds = granularity_seconds * 60

    sorted_gaps = sorted(gaps, key=lambda g: g["from"])

    merged = [sorted_gaps[0].copy()]

    for gap in sorted_gaps[1:]:
        last = merged[-1]
        separation = (gap["from"] - last["to"]).total_seconds()
        if separation <= merge_threshold_seconds:
            last["to"] = gap["to"]
            last["duration_seconds"] = (last["to"] - last["from"]).total_seconds()
            last["duration_hours"] = last["duration_seconds"] / 3600
        else:
            merged.append(gap.copy())

    return merged


def detect_and_heal_gaps(
    instrument: str, granularity: str, db=None
) -> tuple[int, bool]:
    """
    Scan candle data for gaps and heal them by fetching missing data.

    Skips normal forex weekend closures (Friday 5 PM to Sunday 5 PM EST).
    Only heals gaps that occur during trading hours.

    Args:
        instrument: The instrument symbol
        granularity: The candle granularity
        db: Optional database session

    Returns:
        Tuple of (candles_healed, hit_max_limit) where hit_max_limit indicates
        more gaps remain to be processed.
    """
    from ..core.database import SessionLocal
    from ..services.market_data import MarketDataService
    from ..services.verified_gap import VerifiedGapService

    granularity_seconds = GRANULARITY_SECONDS.get(granularity, 60)
    max_gap_seconds = granularity_seconds * GAP_DETECTION_MULTIPLIER

    should_close = db is None
    if db is None:
        db = SessionLocal()

    try:
        timestamps = MarketDataService.get_candle_timestamps(
            db, "oanda", instrument, granularity
        )

        if len(timestamps) < 2:
            return 0, False

        # Load verified empty gaps to skip during detection
        verified_set = VerifiedGapService.get_verified_gaps(
            db, "oanda", instrument, granularity
        )

        small_gaps = []
        large_gaps = []
        market_closures_skipped = 0
        verified_skipped = 0

        for i in range(1, len(timestamps)):
            delta = (timestamps[i] - timestamps[i - 1]).total_seconds()
            if delta > max_gap_seconds:
                gap_start = timestamps[i - 1]
                gap_end = timestamps[i]

                # Skip market closures - weekends, holidays, low-liquidity
                is_closure, closure_reason = is_market_closure(gap_start, gap_end)
                if is_closure:
                    logger.debug(
                        f"Skipping gap {gap_start} to {gap_end} "
                        f"({delta / 3600:.1f}h): {closure_reason}"
                    )
                    market_closures_skipped += 1
                    continue

                gap_info = {
                    "from": gap_start,
                    "to": gap_end,
                    "duration_hours": delta / 3600,
                    "duration_seconds": delta,
                }

                # Categorize gaps by size
                if delta > LARGE_GAP_THRESHOLD_SECONDS:
                    large_gaps.append(gap_info)
                else:
                    small_gaps.append(gap_info)

        if market_closures_skipped > 0:
            logger.debug(
                f"Skipped {market_closures_skipped} market closure gaps for {instrument}:{granularity}"
            )

        # Merge nearby small gaps to reduce OANDA API calls
        if small_gaps:
            original_count = len(small_gaps)
            small_gaps = _merge_nearby_gaps(small_gaps, granularity_seconds)
            if len(small_gaps) < original_count:
                logger.info(
                    f"Merged {original_count} small gaps into {len(small_gaps)} "
                    f"fetch ranges for {instrument}:{granularity}"
                )

        # Filter verified gaps AFTER merging — verified records store merged boundaries
        if small_gaps:
            pre_filter = len(small_gaps)
            small_gaps = [
                g for g in small_gaps if (g["from"], g["to"]) not in verified_set
            ]
            verified_skipped += pre_filter - len(small_gaps)

        if large_gaps:
            pre_filter = len(large_gaps)
            large_gaps = [
                g for g in large_gaps if (g["from"], g["to"]) not in verified_set
            ]
            verified_skipped += pre_filter - len(large_gaps)

        if verified_skipped > 0:
            logger.info(
                f"Skipped {verified_skipped} verified empty gaps for {instrument}:{granularity}"
            )

        # Sort gaps newest-first so recent (most likely healable) gaps are prioritized
        small_gaps.sort(key=lambda g: g["from"], reverse=True)
        large_gaps.sort(key=lambda g: g["from"], reverse=True)

        total_gaps = len(small_gaps) + len(large_gaps)
        if total_gaps == 0:
            logger.debug(f"No healable gaps found for {instrument}:{granularity}")
            return 0, False

        if large_gaps:
            logger.info(
                f"Found {len(large_gaps)} large gaps and {len(small_gaps)} small gaps "
                f"in {instrument}:{granularity}"
            )
        elif small_gaps:
            logger.warning(
                f"Found {len(small_gaps)} gaps in {instrument}:{granularity}, healing..."
            )

        total_healed = 0
        gaps_processed = 0

        # Process large gaps first using chunked healing
        for gap in large_gaps:
            if gaps_processed >= settings.MAX_GAPS_PER_HEALTH_CHECK:
                logger.info(
                    f"Reached max gaps limit ({settings.MAX_GAPS_PER_HEALTH_CHECK}) "
                    f"for {instrument}:{granularity}, deferring remaining gaps"
                )
                break
            healed = heal_large_gap(instrument, granularity, gap["from"], gap["to"], db)
            if healed == 0:
                VerifiedGapService.mark_gap_verified(
                    db,
                    "oanda",
                    instrument,
                    granularity,
                    gap["from"],
                    gap["to"],
                    reason="no_data_large",
                )
            total_healed += healed
            gaps_processed += 1

        # Process small gaps with direct fetch
        for gap in small_gaps:
            if gaps_processed >= settings.MAX_GAPS_PER_HEALTH_CHECK:
                logger.info(
                    f"Reached max gaps limit ({settings.MAX_GAPS_PER_HEALTH_CHECK}) "
                    f"for {instrument}:{granularity}, deferring remaining gaps"
                )
                break
            candles = fetch_candles_range(
                instrument,
                granularity,
                from_time=gap["from"] + timedelta(seconds=1),
                to_time=gap["to"],
            )
            if candles:
                stored = store_candles(instrument, granularity, candles, db)
                total_healed += stored
                if stored > 0:
                    logger.info(
                        f"Healed gap {gap['from']} to {gap['to']}: {stored} candles"
                    )
                    # Remove from verified table if it was previously verified empty
                    VerifiedGapService.remove_healed_gap(
                        db,
                        "oanda",
                        instrument,
                        granularity,
                        gap["from"],
                        gap["to"],
                    )
                else:
                    # Data returned but all candles already exist — mark as verified
                    # so we don't re-fetch this gap every cycle
                    logger.info(
                        f"Gap {gap['from']} to {gap['to']}: data already exists, "
                        f"marking as verified"
                    )
                    VerifiedGapService.mark_gap_verified(
                        db,
                        "oanda",
                        instrument,
                        granularity,
                        gap["from"],
                        gap["to"],
                        reason="data_exists",
                    )
            else:
                logger.info(
                    f"No data for gap {gap['from']} to {gap['to']}, "
                    f"marking as verified empty"
                )
                VerifiedGapService.mark_gap_verified(
                    db,
                    "oanda",
                    instrument,
                    granularity,
                    gap["from"],
                    gap["to"],
                    reason="no_data",
                )
            gaps_processed += 1

        hit_max = gaps_processed >= settings.MAX_GAPS_PER_HEALTH_CHECK
        return total_healed, hit_max
    finally:
        if should_close:
            db.close()


def repair_candle_data(
    instruments: list[str] | None = None,
    granularities: list[str] | None = None,
    db=None,
) -> dict:
    """Comprehensive repair sweep of all candle data.

    Scans every instrument/granularity combination for gaps using the tight
    detection threshold, merges nearby gaps, and heals them by fetching
    from OANDA.

    Args:
        instruments: List of instruments to repair. Defaults to all configured.
        granularities: List of granularities to repair. Defaults to all configured.
        db: Optional database session (for testing).

    Returns:
        Dict with repair statistics.
    """
    from ..core.database import SessionLocal

    instruments = instruments or settings.instruments_list
    granularities = granularities or settings.granularities_list

    should_close = db is None
    if db is None:
        db = SessionLocal()

    stats = {
        "total_healed": 0,
        "by_instrument": {},
    }

    try:
        total_combos = len(instruments) * len(granularities)
        combo_count = 0

        for instrument in instruments:
            stats["by_instrument"][instrument] = {}

            for granularity in granularities:
                combo_count += 1
                logger.info(
                    f"[{combo_count}/{total_combos}] Repairing "
                    f"{instrument}:{granularity}..."
                )

                try:
                    healed, _ = detect_and_heal_gaps(instrument, granularity, db)
                    stats["by_instrument"][instrument][granularity] = healed
                    stats["total_healed"] += healed
                except Exception as e:
                    logger.error(f"Repair failed for {instrument}:{granularity}: {e}")
                    stats["by_instrument"][instrument][granularity] = -1

        logger.info(
            f"Repair complete: {stats['total_healed']} candles healed "
            f"across {total_combos} combinations"
        )
        return stats
    finally:
        if should_close:
            db.close()


async def run_health_check() -> tuple[int, bool]:
    """
    Run comprehensive health check: validate completeness and heal gaps.

    This is called periodically during the poll loop based on
    CANDLE_HEALTH_CHECK_INTERVAL setting.

    Returns:
        Tuple of (total_healed, needs_catchup) where needs_catchup indicates
        at least one instrument/granularity hit the max gaps limit.
    """
    import concurrent.futures

    from ..core.database import SessionLocal
    from ..services.market_data import MarketDataService

    logger.info("Running candle data health check...")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    loop = asyncio.get_event_loop()
    total_healed = 0
    any_hit_max = False

    db = SessionLocal()
    try:
        for instrument in settings.instruments_list:
            if _shutdown_event and _shutdown_event.is_set():
                break

            for granularity in settings.granularities_list:
                if _shutdown_event and _shutdown_event.is_set():
                    break

                try:
                    # Step 1: Validate backfill completeness
                    if not validate_backfill_completeness(instrument, granularity, db):
                        # Delete incomplete data and trigger full backfill
                        logger.info(
                            f"Triggering full re-backfill for {instrument}:{granularity}"
                        )
                        MarketDataService.delete_candles(
                            db, "oanda", instrument, granularity
                        )
                        stored = await loop.run_in_executor(
                            executor, backfill_instrument, instrument, granularity
                        )
                        total_healed += stored
                        continue

                    # Step 2: Detect and heal gaps in the middle
                    healed, hit_max = await loop.run_in_executor(
                        executor, detect_and_heal_gaps, instrument, granularity, db
                    )
                    total_healed += healed
                    if hit_max:
                        any_hit_max = True

                except Exception as e:
                    logger.error(
                        f"Health check failed for {instrument}:{granularity}: {e}"
                    )
    finally:
        db.close()

    if any_hit_max:
        logger.info(
            f"Health check complete: {total_healed} candles healed "
            f"(catch-up needed, next check in {CATCH_UP_HEALTH_CHECK_INTERVAL}s)"
        )
    else:
        logger.info(f"Health check complete: {total_healed} candles healed")
    return total_healed, any_hit_max


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
    logger.info(
        f"Instruments: {settings.instruments_list}, "
        f"Granularities: {settings.granularities_list}"
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    loop = asyncio.get_event_loop()

    total_stored = 0

    for instrument in settings.instruments_list:
        if _shutdown_event and _shutdown_event.is_set():
            break

        for granularity in settings.granularities_list:
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
    """Main ingestion loop with resilient retry logic and periodic health checks.

    Flow:
    1. Run startup_backfill() to fill any gaps
    2. Run initial health check (validate completeness + heal gaps)
    3. Enter continuous polling mode
    4. Fetch recent candles at INGESTION_POLL_INTERVAL
    5. Run health check every CANDLE_HEALTH_CHECK_INTERVAL seconds
    """
    import concurrent.futures

    global _last_health_check

    # Run initial backfill first
    await startup_backfill()

    # Run initial health check
    _, needs_catchup = await run_health_check()
    _last_health_check = datetime.now(UTC)
    # Adaptive interval: use short interval when gaps remain, normal otherwise
    current_health_interval = (
        CATCH_UP_HEALTH_CHECK_INTERVAL
        if needs_catchup
        else settings.CANDLE_HEALTH_CHECK_INTERVAL
    )

    logger.info(
        f"Entering continuous polling mode "
        f"(poll: {settings.INGESTION_POLL_INTERVAL}s, "
        f"health check: {current_health_interval}s)"
    )

    consecutive_failures = 0
    backoff_seconds = INITIAL_BACKOFF_SECONDS
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    loop = asyncio.get_event_loop()

    while not _shutdown_event.is_set():
        try:
            cycle_start = datetime.now(UTC)

            # Check if health check is due (uses adaptive interval)
            if current_health_interval > 0 and _last_health_check:
                time_since_check = (cycle_start - _last_health_check).total_seconds()
                if time_since_check >= current_health_interval:
                    _, needs_catchup = await run_health_check()
                    _last_health_check = datetime.now(UTC)
                    # Adapt interval based on whether gaps remain
                    current_health_interval = (
                        CATCH_UP_HEALTH_CHECK_INTERVAL
                        if needs_catchup
                        else settings.CANDLE_HEALTH_CHECK_INTERVAL
                    )

            total_stored = 0

            for instrument in settings.instruments_list:
                if _shutdown_event.is_set():
                    break

                for granularity in settings.granularities_list:
                    if _shutdown_event.is_set():
                        break

                    try:
                        stored = await loop.run_in_executor(
                            executor,
                            fetch_and_store_recent,
                            instrument,
                            granularity,
                            30,  # Fetch last 30 candles to recover from missed polls
                        )
                        total_stored += stored
                    except Exception as e:
                        logger.warning(
                            f"Failed to ingest {instrument}/{granularity}: {e}"
                        )

            cycle_duration = (datetime.now(UTC) - cycle_start).total_seconds()
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


async def start_ingestion_worker() -> bool:
    """Start the candle ingestion background worker.

    This should be called from FastAPI lifespan startup.
    The worker will run until stop_ingestion_worker() is called.
    """
    global _worker_task, _shutdown_event

    # Skip if ingestion is disabled
    if not settings.INGESTION_ENABLED:
        logger.info("Ingestion worker disabled via INGESTION_ENABLED setting")
        return False

    # Skip if OANDA credentials not configured
    if not settings.OANDA_API_KEY or settings.OANDA_API_KEY == "your-api-key-here":
        logger.warning(
            "OANDA credentials not configured - ingestion worker disabled. "
            "Set OANDA_API_KEY in environment to enable."
        )
        return False

    # INGESTION_ENABLED is the single source of truth for controlling ingestion.
    # Set INGESTION_ENABLED=false in .env.test if you want to disable ingestion in tests.

    if _worker_task is not None:
        logger.warning("Ingestion worker already running")
        return False

    _shutdown_event = asyncio.Event()
    _worker_task = asyncio.create_task(ingestion_loop())

    logger.info(
        f"Candle ingestion worker started: "
        f"{len(settings.instruments_list)} instruments, "
        f"{len(settings.granularities_list)} granularities, "
        f"polling every {settings.INGESTION_POLL_INTERVAL}s, "
        f"health check every {settings.CANDLE_HEALTH_CHECK_INTERVAL}s"
    )
    return True


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
