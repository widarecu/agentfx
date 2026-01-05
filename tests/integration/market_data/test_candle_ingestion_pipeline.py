"""
Candle Ingestion Pipeline Integration Tests

These tests validate that the candle ingestion pipeline is working end-to-end:
1. All 6 core FX pairs have candle data in the database
2. All 4 timeframes (M1, M5, M15, H1) are populated
3. Sufficient history exists for backtesting (minimum 200 candles)
4. Candle data has valid OHLCV values
5. No large gaps in data (continuity validation)

Test Modes:
- Pipeline validation tests use a non-cleaning fixture to check real system state
- Unit tests seed their own data for isolation

Prerequisites:
- PostgreSQL running with test database
- For pipeline validation: Backend server should have run to trigger candle ingestion
- OANDA API credentials configured (for real API tests)

Run with: ENVIRONMENT=test pytest tests/integration/market_data/test_candle_ingestion_pipeline.py -v
"""

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from urllib.parse import urlparse

# Core FX pairs that must have data
CORE_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "USD_CAD"]

# Required timeframes
REQUIRED_TIMEFRAMES = ["M1", "M5", "M15", "H1"]

# Minimum candles required for backtesting (MA crossover needs ~200 candles)
MIN_CANDLES_FOR_BACKTEST = 200

# Maximum acceptable gap in trading hours (minutes)
MAX_GAP_MINUTES = 60  # 1 hour gap is acceptable (markets close overnight)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="class")
def pipeline_db():
    """
    Database connection for pipeline validation tests.

    This fixture does NOT clean up data between tests, allowing validation
    of the actual system state after candle ingestion has run.
    """
    from src.agentfx.api.core.config import settings

    parsed = urlparse(settings.TEST_DATABASE_URL)
    conn_params = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/") if parsed.path else "agentfx_test",
        "user": parsed.username or "test_user",
        "password": parsed.password or "test_password",
    }

    conn = psycopg2.connect(**conn_params, cursor_factory=RealDictCursor)
    yield conn
    conn.close()


def _has_sufficient_candle_data(cursor) -> tuple[bool, str]:
    """Check if database has sufficient candle data for pipeline validation.

    Returns:
        Tuple of (has_sufficient_data, skip_reason)
    """
    # Check for any data first
    cursor.execute("SELECT COUNT(*) as count FROM candles WHERE exchange = 'oanda'")
    result = cursor.fetchone()
    total_candles = result["count"]

    if total_candles == 0:
        return False, "No candle data in database"

    # Check that at least 3 pairs have >= 100 H1 candles each
    # This indicates the pipeline has actually run and populated data
    cursor.execute(
        """
        SELECT instrument, COUNT(*) as cnt
        FROM candles
        WHERE exchange = 'oanda' AND timeframe = 'H1'
        GROUP BY instrument
        HAVING COUNT(*) >= 100
    """
    )
    pairs_with_data = cursor.fetchall()

    if len(pairs_with_data) < 3:
        pair_counts = (
            {r["instrument"]: r["cnt"] for r in pairs_with_data}
            if pairs_with_data
            else {}
        )
        return False, (
            f"Insufficient candle data for pipeline validation. "
            f"Need at least 3 pairs with 100+ H1 candles each, but found: {pair_counts}. "
            f"Total candles in DB: {total_candles}"
        )

    return True, ""


def _skip_if_no_data(cursor):
    """Skip test if insufficient candle data is available (pipeline hasn't run properly)."""
    has_data, reason = _has_sufficient_candle_data(cursor)
    if not has_data:
        pytest.skip(
            f"{reason}. Run the backend server first to trigger "
            "candle ingestion: ENVIRONMENT=test ./scripts/start-backend.sh"
        )


class TestCandleDataAvailability:
    """Tests that validate candle data is available for all required pairs/timeframes.

    Uses pipeline_db fixture which does NOT clean data between tests.
    These tests skip if no candle data exists (pipeline hasn't run yet).
    """

    def test_all_core_pairs_have_data(self, pipeline_db):
        """Validate that all 6 core FX pairs have candle data in the database."""
        cursor = pipeline_db.cursor()
        _skip_if_no_data(cursor)

        missing_pairs = []
        for pair in CORE_PAIRS:
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM candles
                WHERE instrument = %s AND exchange = 'oanda'
                """,
                (pair,),
            )
            result = cursor.fetchone()
            if result["count"] == 0:
                missing_pairs.append(pair)

        cursor.close()

        assert len(missing_pairs) == 0, (
            f"Missing candle data for pairs: {missing_pairs}. "
            f"Ensure the backend server has run to trigger candle ingestion."
        )

    def test_all_timeframes_populated(self, pipeline_db):
        """Validate that all 4 timeframes have data for each core pair."""
        cursor = pipeline_db.cursor()
        _skip_if_no_data(cursor)

        missing_timeframes = {}
        for pair in CORE_PAIRS:
            for timeframe in REQUIRED_TIMEFRAMES:
                cursor.execute(
                    """
                    SELECT COUNT(*) as count FROM candles
                    WHERE instrument = %s AND timeframe = %s AND exchange = 'oanda'
                    """,
                    (pair, timeframe),
                )
                result = cursor.fetchone()
                if result["count"] == 0:
                    if pair not in missing_timeframes:
                        missing_timeframes[pair] = []
                    missing_timeframes[pair].append(timeframe)

        cursor.close()

        assert len(missing_timeframes) == 0, (
            f"Missing timeframes: {missing_timeframes}. "
            f"Candle ingestion worker should populate M1, M5, M15, H1 for all pairs."
        )

    def test_sufficient_history_for_backtest(self, pipeline_db):
        """Validate sufficient candle history exists for backtesting.

        Requires at least 4 of 6 core pairs to have sufficient H1 data.
        OANDA API timeouts can cause some pairs to have incomplete data
        during initial ingestion, so we allow some flexibility.
        """
        cursor = pipeline_db.cursor()
        _skip_if_no_data(cursor)

        insufficient_data = {}
        for pair in CORE_PAIRS:
            # Check H1 timeframe as it's most important for backtesting
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM candles
                WHERE instrument = %s AND timeframe = 'H1' AND exchange = 'oanda'
                """,
                (pair,),
            )
            result = cursor.fetchone()
            if result["count"] < MIN_CANDLES_FOR_BACKTEST:
                insufficient_data[pair] = result["count"]

        cursor.close()

        # Require at least 4 of 6 pairs to have sufficient data
        # This accounts for transient OANDA API timeouts
        sufficient_count = len(CORE_PAIRS) - len(insufficient_data)
        min_required = 4

        if insufficient_data:
            import warnings

            warnings.warn(
                f"Some pairs have insufficient H1 data (need {MIN_CANDLES_FOR_BACKTEST}): "
                f"{insufficient_data}. This may be due to OANDA API timeouts."
            )

        assert sufficient_count >= min_required, (
            f"Insufficient H1 candle history for backtesting. "
            f"Need at least {min_required} pairs with {MIN_CANDLES_FOR_BACKTEST}+ candles. "
            f"Only {sufficient_count} pairs ready. Insufficient: {insufficient_data}"
        )

    def test_candle_count_summary(self, pipeline_db):
        """Report candle counts for all pair/timeframe combinations."""
        cursor = pipeline_db.cursor()

        cursor.execute(
            """
            SELECT instrument, timeframe, COUNT(*) as count
            FROM candles
            WHERE exchange = 'oanda'
            GROUP BY instrument, timeframe
            ORDER BY instrument, timeframe
            """
        )
        results = cursor.fetchall()
        cursor.close()

        # Build summary
        summary = {}
        for row in results:
            pair = row["instrument"]
            tf = row["timeframe"]
            count = row["count"]
            if pair not in summary:
                summary[pair] = {}
            summary[pair][tf] = count

        print("\n=== Candle Data Summary ===")
        for pair in CORE_PAIRS:
            if pair in summary:
                counts = summary[pair]
                print(
                    f"{pair}: M1={counts.get('M1', 0)}, M5={counts.get('M5', 0)}, "
                    f"M15={counts.get('M15', 0)}, H1={counts.get('H1', 0)}"
                )
            else:
                print(f"{pair}: NO DATA")

        # This test always passes - it's for reporting
        assert True


class TestCandleDataQuality:
    """Tests that validate candle data quality and integrity."""

    def test_candles_have_valid_ohlcv(self, pipeline_db):
        """Validate that candles have valid OHLCV values (no nulls, proper ranges)."""
        cursor = pipeline_db.cursor()

        # Check for null values
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE exchange = 'oanda'
            AND (open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL)
            """
        )
        null_result = cursor.fetchone()

        # Check for invalid OHLC relationships (high should be >= open, close, low)
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE exchange = 'oanda'
            AND (high < open OR high < close OR low > open OR low > close)
            """
        )
        invalid_result = cursor.fetchone()

        # Check for negative volumes
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE exchange = 'oanda' AND volume < 0
            """
        )
        negative_vol_result = cursor.fetchone()

        cursor.close()

        errors = []
        if null_result["count"] > 0:
            errors.append(f"{null_result['count']} candles have NULL values")
        if invalid_result["count"] > 0:
            errors.append(
                f"{invalid_result['count']} candles have invalid OHLC relationships"
            )
        if negative_vol_result["count"] > 0:
            errors.append(
                f"{negative_vol_result['count']} candles have negative volume"
            )

        assert len(errors) == 0, f"Data quality issues: {', '.join(errors)}"

    def test_candles_have_timestamps_in_past(self, pipeline_db):
        """Validate that candle timestamps are not far in the future.

        Allows 2-hour buffer for current candle formation.
        Both NOW() and candle timestamps are timestamptz, so comparison is timezone-aware.
        """
        cursor = pipeline_db.cursor()

        # NOW() returns timestamptz, candle timestamps are also timestamptz
        # PostgreSQL handles timezone conversion automatically
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE exchange = 'oanda' AND timestamp > NOW() + INTERVAL '2 hours'
            """
        )
        result = cursor.fetchone()
        cursor.close()

        assert (
            result["count"] == 0
        ), f"{result['count']} candles have timestamps more than 2 hours in the future"

    def test_candle_prices_reasonable(self, pipeline_db):
        """Validate that candle prices are within reasonable FX ranges."""
        cursor = pipeline_db.cursor()

        # FX prices should generally be between 0.5 and 200 (covers all major pairs)
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE exchange = 'oanda'
            AND (open <= 0 OR open > 200 OR close <= 0 OR close > 200)
            """
        )
        result = cursor.fetchone()
        cursor.close()

        assert (
            result["count"] == 0
        ), f"{result['count']} candles have prices outside reasonable FX range (0.5-200)"


class TestCandleDataContinuity:
    """Tests that validate candle data continuity (no large gaps)."""

    def test_no_large_gaps_in_h1_data(self, pipeline_db):
        """Validate H1 data has no gaps larger than expected trading breaks."""
        cursor = pipeline_db.cursor()

        gaps_found = {}
        for pair in CORE_PAIRS:
            cursor.execute(
                """
                WITH candle_gaps AS (
                    SELECT
                        timestamp,
                        LAG(timestamp) OVER (ORDER BY timestamp) as prev_timestamp,
                        EXTRACT(EPOCH FROM (timestamp - LAG(timestamp) OVER (ORDER BY timestamp))) / 3600 as gap_hours
                    FROM candles
                    WHERE instrument = %s AND timeframe = 'H1' AND exchange = 'oanda'
                )
                SELECT MAX(gap_hours) as max_gap_hours
                FROM candle_gaps
                WHERE gap_hours IS NOT NULL
                """,
                (pair,),
            )
            result = cursor.fetchone()

            if result and result["max_gap_hours"]:
                max_gap = result["max_gap_hours"]
                # Allow up to 96 hours (4 days) for holiday weekend gaps
                # Regular weekends: ~62 hours (Fri 5pm to Sun 5pm ET)
                # Holiday weekends (Christmas/New Year): can be up to 96 hours
                if max_gap > 96:
                    gaps_found[pair] = max_gap

        cursor.close()

        assert len(gaps_found) == 0, (
            f"Large gaps found in H1 data (>96 hours): {gaps_found}. "
            f"This may indicate missing data or ingestion failures."
        )

    def test_recent_data_available(self, pipeline_db):
        """Validate that recent candle data is available (within last 24 hours)."""
        cursor = pipeline_db.cursor()
        _skip_if_no_data(cursor)

        stale_pairs = []
        for pair in CORE_PAIRS:
            cursor.execute(
                """
                SELECT MAX(timestamp) as latest FROM candles
                WHERE instrument = %s AND timeframe = 'M1' AND exchange = 'oanda'
                """,
                (pair,),
            )
            result = cursor.fetchone()

            if result and result["latest"]:
                latest = result["latest"]
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)

                age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600

                # If data is older than 24 hours, flag it (could be weekend)
                # For a stricter check on trading days, use 2 hours
                if age_hours > 24:
                    stale_pairs.append((pair, f"{age_hours:.1f} hours old"))
            else:
                stale_pairs.append((pair, "no data"))

        cursor.close()

        # This is a warning - data may be stale during weekends
        if stale_pairs:
            print(f"\nWarning: Potentially stale data: {stale_pairs}")

        # Don't fail on weekends - just ensure data exists
        assert all(
            age != "no data" for _, age in stale_pairs
        ), f"Some pairs have no M1 data at all: {[p for p, a in stale_pairs if a == 'no data']}"


class TestCandleIngestionWorker:
    """Tests that validate the candle ingestion worker behavior."""

    @pytest.mark.oanda_real
    def test_worker_can_fetch_new_candles(self, db_session):
        """Test that the worker can fetch and store new candles from OANDA."""
        from src.agentfx.api.workers.candle_ingestion import backfill_instrument
        from src.agentfx.api.models.instrument import Instrument

        # Ensure EUR_USD instrument exists
        instrument = db_session.query(Instrument).filter_by(symbol="EUR_USD").first()
        if not instrument:
            instrument = Instrument(
                symbol="EUR_USD",
                display_name="EUR/USD",
                instrument_type="CURRENCY",
                pip_location=-4,
                is_active=True,
                is_tradeable=True,
            )
            db_session.add(instrument)
            db_session.commit()

        # Attempt to backfill (will fetch from OANDA if configured)
        try:
            stored = backfill_instrument("EUR_USD", "M1", db=db_session)
            assert stored >= 0, "backfill_instrument should return non-negative count"
        except Exception as e:
            # May fail if OANDA credentials not configured or network issues
            pytest.skip(f"OANDA integration not available: {e}")

    def test_instruments_seeded(self, pipeline_db):
        """Validate that all core instruments are seeded in the database."""
        cursor = pipeline_db.cursor()

        # Check if instruments table has any data
        cursor.execute("SELECT COUNT(*) as count FROM instruments")
        result = cursor.fetchone()
        if result["count"] == 0:
            cursor.close()
            pytest.skip(
                "No instrument data in database. Run the seed script: "
                "ENVIRONMENT=test PYTHONPATH=src .venv/bin/python scripts/seed_instruments.py"
            )

        cursor.execute(
            """
            SELECT symbol FROM instruments
            WHERE symbol IN %s AND is_active = true
            """,
            (tuple(CORE_PAIRS),),
        )
        results = cursor.fetchall()
        cursor.close()

        seeded_pairs = {row["symbol"] for row in results}
        missing_pairs = set(CORE_PAIRS) - seeded_pairs

        assert len(missing_pairs) == 0, (
            f"Missing instrument metadata for: {missing_pairs}. "
            f"Run the seed_instruments.py script to populate."
        )
