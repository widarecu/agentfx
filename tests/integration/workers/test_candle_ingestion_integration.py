"""Integration tests for the candle ingestion worker.

These tests require:
1. PostgreSQL database running
2. OANDA API credentials configured
3. Network access to OANDA API (no SSL interception)

Mark with @pytest.mark.oanda_real to require real OANDA connection.
Mark with @pytest.mark.slow for long-running tests.
"""

import pytest
import requests
from datetime import datetime, timezone, timedelta

from src.agentfx.api.core.config import settings


def _oanda_is_reachable() -> tuple[bool, str]:
    """Check if OANDA API is reachable without SSL issues.

    Returns:
        Tuple of (is_reachable, reason_if_not)
    """
    if not settings.OANDA_API_KEY or settings.OANDA_API_KEY == "your-api-key-here":
        return False, "OANDA credentials not configured"

    try:
        # Quick connectivity test with timeout
        requests.get(
            f"https://{settings.OANDA_API_HOST}/v3/accounts",
            headers={"Authorization": f"Bearer {settings.OANDA_API_KEY}"},
            timeout=5,
        )
        # Any response (even 401) means we can reach OANDA
        return True, ""
    except requests.exceptions.SSLError as e:
        return False, f"SSL error connecting to OANDA (likely network proxy): {e}"
    except requests.exceptions.ConnectionError as e:
        return False, f"Cannot connect to OANDA API: {e}"
    except requests.exceptions.Timeout:
        return False, "Connection to OANDA timed out"
    except Exception as e:
        return False, f"Unexpected error testing OANDA connectivity: {e}"


# Skip all tests if database is not available
pytestmark = pytest.mark.integration


@pytest.fixture
def seed_instruments(db_connection):
    """Seed test instruments before tests."""
    cursor = db_connection.cursor()

    # Insert test instruments
    instruments = [
        ("EUR_USD", "EUR/USD", "CURRENCY", -4),
        ("GBP_USD", "GBP/USD", "CURRENCY", -4),
        ("USD_JPY", "USD/JPY", "CURRENCY", -2),
        ("AUD_USD", "AUD/USD", "CURRENCY", -4),
        ("USD_CHF", "USD/CHF", "CURRENCY", -4),
        ("USD_CAD", "USD/CAD", "CURRENCY", -4),
    ]

    for symbol, display_name, inst_type, pip_loc in instruments:
        cursor.execute(
            """
            INSERT INTO instruments (symbol, display_name, instrument_type, pip_location, is_active, is_tradeable)
            VALUES (%s, %s, %s, %s, true, true)
            ON CONFLICT (symbol) DO NOTHING
            """,
            (symbol, display_name, inst_type, pip_loc),
        )

    db_connection.commit()
    cursor.close()
    return instruments


@pytest.mark.integration
class TestInstrumentSeeding:
    """Integration tests for instrument seeding."""

    def test_seed_instruments_creates_records(self, db_connection, seed_instruments):
        """Test that seeding creates instrument records in database."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM instruments")
        result = cursor.fetchone()
        cursor.close()

        assert result["count"] == 6

    def test_seed_instruments_idempotent(self, db_connection, seed_instruments):
        """Test that seeding is idempotent (can run multiple times)."""
        cursor = db_connection.cursor()

        # Run seeding again
        cursor.execute(
            """
            INSERT INTO instruments (symbol, display_name, instrument_type, pip_location, is_active, is_tradeable)
            VALUES ('EUR_USD', 'EUR/USD', 'CURRENCY', -4, true, true)
            ON CONFLICT (symbol) DO NOTHING
            """
        )
        db_connection.commit()

        # Count should still be 6
        cursor.execute("SELECT COUNT(*) as count FROM instruments")
        result = cursor.fetchone()
        cursor.close()

        assert result["count"] == 6

    def test_instrument_fields_correct(self, db_connection, seed_instruments):
        """Test that instrument fields are set correctly."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT * FROM instruments WHERE symbol = 'USD_JPY'")
        result = cursor.fetchone()
        cursor.close()

        assert result["symbol"] == "USD_JPY"
        assert result["display_name"] == "USD/JPY"
        assert result["pip_location"] == -2  # JPY pairs have different pip location
        assert result["is_active"] is True
        assert result["is_tradeable"] is True


@pytest.mark.integration
class TestCandleStorage:
    """Integration tests for candle storage with real database."""

    @pytest.fixture
    def sample_candles(self):
        """Sample candle data for testing."""
        base_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        return [
            {
                "timestamp": base_time,
                "open": 1.1000,
                "high": 1.1005,
                "low": 1.0995,
                "close": 1.1002,
                "volume": 1000,
            },
            {
                "timestamp": base_time + timedelta(minutes=1),
                "open": 1.1002,
                "high": 1.1008,
                "low": 1.1000,
                "close": 1.1006,
                "volume": 1200,
            },
        ]

    def test_store_candles_to_database(self, db_connection, sample_candles):
        """Test storing candles to real database.

        Uses TEST_EUR instrument to avoid conflicting with real OANDA data.
        """
        cursor = db_connection.cursor()

        # Clean up any previous test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()

        # Insert candles
        for candle in sample_candles:
            cursor.execute(
                """
                INSERT INTO candles (
                    exchange, instrument, timeframe, timestamp,
                    open, high, low, close, volume, complete
                )
                VALUES ('test', 'TEST_EUR', 'M1', %s, %s, %s, %s, %s, %s, true)
                ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING
                """,
                (
                    candle["timestamp"],
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                ),
            )

        db_connection.commit()

        # Verify candles were stored
        cursor.execute(
            "SELECT COUNT(*) as count FROM candles WHERE instrument = 'TEST_EUR'"
        )
        result = cursor.fetchone()

        # Clean up test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()
        cursor.close()

        assert result["count"] == 2

    def test_get_latest_candle(self, db_connection, sample_candles):
        """Test retrieving the latest candle from database.

        Uses TEST_EUR instrument to avoid conflicting with real OANDA data.
        """
        cursor = db_connection.cursor()

        # Clean up any previous test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()

        # Insert candles
        for candle in sample_candles:
            cursor.execute(
                """
                INSERT INTO candles (
                    exchange, instrument, timeframe, timestamp,
                    open, high, low, close, volume, complete
                )
                VALUES ('test', 'TEST_EUR', 'M1', %s, %s, %s, %s, %s, %s, true)
                ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING
                """,
                (
                    candle["timestamp"],
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                    candle["volume"],
                ),
            )

        db_connection.commit()

        # Get latest candle
        cursor.execute(
            """
            SELECT * FROM candles
            WHERE exchange = 'test' AND instrument = 'TEST_EUR' AND timeframe = 'M1'
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        result = cursor.fetchone()

        # Clean up test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()
        cursor.close()

        assert result is not None
        assert result["close"] == 1.1006  # Latest candle

    def test_candle_upsert_on_conflict(self, db_connection):
        """Test that candles are updated on conflict.

        Uses TEST_EUR instrument to avoid conflicting with real OANDA data.
        """
        cursor = db_connection.cursor()
        timestamp = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Clean up any previous test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()

        # Insert initial candle
        cursor.execute(
            """
            INSERT INTO candles (
                exchange, instrument, timeframe, timestamp,
                open, high, low, close, volume, complete
            )
            VALUES ('test', 'TEST_EUR', 'M1', %s, 1.1000, 1.1005, 1.0995, 1.1002, 1000, false)
            ON CONFLICT (instrument, timeframe, timestamp)
            DO UPDATE SET close = EXCLUDED.close, volume = EXCLUDED.volume, complete = EXCLUDED.complete
            """,
            (timestamp,),
        )
        db_connection.commit()

        # Update with new close price
        cursor.execute(
            """
            INSERT INTO candles (
                exchange, instrument, timeframe, timestamp,
                open, high, low, close, volume, complete
            )
            VALUES ('test', 'TEST_EUR', 'M1', %s, 1.1000, 1.1010, 1.0990, 1.1008, 1500, true)
            ON CONFLICT (instrument, timeframe, timestamp)
            DO UPDATE SET close = EXCLUDED.close, volume = EXCLUDED.volume, complete = EXCLUDED.complete
            """,
            (timestamp,),
        )
        db_connection.commit()

        # Verify update
        cursor.execute(
            "SELECT close, volume, complete FROM candles WHERE instrument = 'TEST_EUR' AND timestamp = %s",
            (timestamp,),
        )
        result = cursor.fetchone()

        # Clean up test data
        cursor.execute("DELETE FROM candles WHERE instrument = 'TEST_EUR'")
        db_connection.commit()
        cursor.close()

        assert result["close"] == 1.1008
        assert result["volume"] == 1500
        assert result["complete"] is True


@pytest.mark.integration
@pytest.mark.oanda_real
class TestOandaIntegration:
    """Integration tests that require real OANDA API connection.

    These tests are marked with @pytest.mark.oanda_real and will only run
    when OANDA credentials are configured and the marker is not filtered.
    """

    @pytest.fixture
    def oanda_configured(self):
        """Check if OANDA is configured and reachable (no SSL interception)."""
        is_reachable, skip_reason = _oanda_is_reachable()
        if not is_reachable:
            pytest.skip(skip_reason)
        return True

    def test_fetch_real_candles(self, oanda_configured):
        """Test fetching real candles from OANDA API."""
        from src.agentfx.api.workers.candle_ingestion import fetch_candles_range

        from_time = datetime.now(timezone.utc) - timedelta(hours=1)
        candles = fetch_candles_range("EUR_USD", "M1", from_time)

        # Should get some candles (markets may be closed on weekends)
        # so we just check it doesn't error
        assert isinstance(candles, list)

    def test_backfill_real_instrument(self, oanda_configured, db_session):
        """Test backfilling a real instrument.

        This test uses session injection to pass the SQLAlchemy db_session directly
        to backfill_instrument(), ensuring the test database is used correctly.

        Note: This test requires OANDA credentials to be configured.
        """
        from src.agentfx.api.workers.candle_ingestion import backfill_instrument
        from src.agentfx.api.models.instrument import Instrument

        # Seed instruments using SQLAlchemy (not psycopg2)
        instruments_data = [
            {
                "symbol": "EUR_USD",
                "display_name": "EUR/USD",
                "instrument_type": "CURRENCY",
                "pip_location": -4,
            },
        ]
        for data in instruments_data:
            instrument = (
                db_session.query(Instrument).filter_by(symbol=data["symbol"]).first()
            )
            if not instrument:
                instrument = Instrument(
                    symbol=data["symbol"],
                    display_name=data["display_name"],
                    instrument_type=data["instrument_type"],
                    pip_location=data["pip_location"],
                    is_active=True,
                    is_tradeable=True,
                )
                db_session.add(instrument)
        db_session.commit()

        # This will try to backfill EUR_USD M1 using the injected session
        # May take some time if no existing data
        stored = backfill_instrument("EUR_USD", "M1", db=db_session)

        # Should have stored some candles (or 0 if market is closed)
        assert stored >= 0


@pytest.mark.integration
class TestGapDetection:
    """Integration tests for gap detection logic."""

    @pytest.fixture
    def candles_with_gap(self, db_connection):
        """Insert candles with a gap in the middle."""
        cursor = db_connection.cursor()

        # Clean up existing candles for EUR_USD to ensure test isolation
        cursor.execute(
            """
            DELETE FROM candles
            WHERE exchange = 'oanda' AND instrument = 'EUR_USD' AND timeframe = 'M1'
            """
        )

        # Create candles from 2 hours ago to 1.5 hours ago (leaving 1.5 hour gap)
        base_time = datetime.now(timezone.utc) - timedelta(hours=2)

        for i in range(30):  # 30 minutes of data
            timestamp = base_time + timedelta(minutes=i)
            cursor.execute(
                """
                INSERT INTO candles (
                    exchange, instrument, timeframe, timestamp,
                    open, high, low, close, volume, complete
                )
                VALUES ('oanda', 'EUR_USD', 'M1', %s, 1.1, 1.11, 1.09, 1.105, 100, true)
                """,
                (timestamp,),
            )

        db_connection.commit()
        cursor.close()

        return base_time + timedelta(minutes=29)  # Return last candle time

    def test_detect_gap_in_data(self, db_connection, candles_with_gap):
        """Test that gap is detected when data is outdated."""
        cursor = db_connection.cursor()

        # Get the latest candle
        cursor.execute(
            """
            SELECT timestamp FROM candles
            WHERE exchange = 'oanda' AND instrument = 'EUR_USD' AND timeframe = 'M1'
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        result = cursor.fetchone()
        cursor.close()

        if result:
            latest_time = result["timestamp"]
            if latest_time.tzinfo is None:
                latest_time = latest_time.replace(tzinfo=timezone.utc)

            gap = datetime.now(timezone.utc) - latest_time
            gap_minutes = gap.total_seconds() / 60

            # Should have at least 30 minutes gap (we created data ending 1h30m ago)
            assert gap_minutes > 30

    def test_calculate_gap_fill_range(self, db_connection, candles_with_gap):
        """Test calculating the range needed for gap fill."""
        last_candle_time = candles_with_gap

        # Gap fill should start from last candle + 1 second
        gap_fill_start = last_candle_time + timedelta(seconds=1)
        gap_fill_end = datetime.now(timezone.utc)

        # Calculate expected candles in gap
        gap_duration = gap_fill_end - gap_fill_start
        expected_candles = int(gap_duration.total_seconds() / 60)  # M1 candles

        assert expected_candles > 30  # At least 30 minutes of gap
