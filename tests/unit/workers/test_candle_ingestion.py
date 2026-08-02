"""Unit tests for the candle ingestion worker."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from src.agentfx.api.core.config import settings
from src.agentfx.api.workers.candle_ingestion import (
    GAP_DETECTION_MULTIPLIER,
    GRANULARITY_SECONDS,
    INGESTION_ALIGNMENT_BUFFER_SECONDS,
    LARGE_GAP_CHUNK_DAYS,
    LARGE_GAP_THRESHOLD_SECONDS,
    MAX_CANDLES_PER_REQUEST,
    _get_backfill_start_time,
    _merge_nearby_gaps,
    _next_poll_wake_time,
    backfill_instrument,
    detect_and_heal_gaps,
    fetch_and_store_recent,
    fetch_candles_range,
    heal_large_gap,
    repair_candle_data,
    store_candles,
)


@pytest.mark.unit
class TestIngestionConfiguration:
    """Test cases for ingestion worker configuration."""

    def test_supported_instruments_count(self):
        """Test that we have exactly 6 supported instruments."""
        assert len(settings.instruments_list) == 6

    def test_supported_instruments_list(self):
        """Test that all expected instruments are configured."""
        expected = {"EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "USD_CAD"}
        assert set(settings.instruments_list) == expected

    def test_granularities_configured(self):
        """Test that multiple granularities are configured."""
        assert len(settings.granularities_list) >= 4
        assert "M1" in settings.granularities_list
        assert "H1" in settings.granularities_list

    def test_initial_backfill_days(self):
        """Test that backfill days is configured to a positive value."""
        # BACKFILL_DAYS is configurable: default 7, test env uses 1825 for backtesting
        assert settings.BACKFILL_DAYS > 0
        assert isinstance(settings.BACKFILL_DAYS, int)

    def test_max_candles_per_request(self):
        """Test OANDA API pagination limit."""
        assert MAX_CANDLES_PER_REQUEST == 500

    def test_large_gap_threshold_is_7_days(self):
        """Test that large gap threshold is 7 days (604800 seconds)."""
        assert LARGE_GAP_THRESHOLD_SECONDS == 604800  # 7 days in seconds
        assert LARGE_GAP_THRESHOLD_SECONDS == 7 * 24 * 60 * 60

    def test_large_gap_chunk_days(self):
        """Test that large gaps are processed in 7-day chunks."""
        assert LARGE_GAP_CHUNK_DAYS == 7


@pytest.mark.unit
class TestGetBackfillStartTime:
    """Test cases for _get_backfill_start_time helper."""

    def test_default_backfill_days(self):
        """Test default backfill calculation uses settings.BACKFILL_DAYS."""
        before = datetime.now(UTC)
        result = _get_backfill_start_time()
        after = datetime.now(UTC)

        expected_start = before - timedelta(days=settings.BACKFILL_DAYS)
        expected_end = after - timedelta(days=settings.BACKFILL_DAYS)

        assert expected_start <= result <= expected_end

    def test_custom_backfill_days(self):
        """Test custom backfill days calculation."""
        before = datetime.now(UTC)
        result = _get_backfill_start_time(days=30)
        after = datetime.now(UTC)

        expected_start = before - timedelta(days=30)
        expected_end = after - timedelta(days=30)

        assert expected_start <= result <= expected_end

    def test_backfill_returns_utc_datetime(self):
        """Test that backfill time has UTC timezone."""
        result = _get_backfill_start_time()
        assert result.tzinfo == UTC


@pytest.mark.unit
class TestFetchCandlesRange:
    """Test cases for fetch_candles_range function."""

    @pytest.fixture
    def mock_oanda_client(self):
        """Create a mock OANDA client.

        Note: OandaClient is imported inside the function, so we patch at the source.
        """
        with patch("agentfx.adapters.oanda.OandaClient") as mock:
            yield mock

    @pytest.fixture
    def sample_candles(self):
        """Sample candle data from OANDA."""
        return [
            {
                "time": "2023-01-01T12:00:00Z",
                "open": 1.1000,
                "high": 1.1005,
                "low": 1.0995,
                "close": 1.1002,
                "volume": 1000,
                "complete": True,
            },
            {
                "time": "2023-01-01T12:01:00Z",
                "open": 1.1002,
                "high": 1.1008,
                "low": 1.1000,
                "close": 1.1006,
                "volume": 1200,
                "complete": True,
            },
        ]

    def test_fetch_candles_success(self, mock_oanda_client, sample_candles):
        """Test successful candle fetching."""
        mock_client_instance = MagicMock()
        mock_oanda_client.return_value = mock_client_instance
        mock_client_instance.get_candles.return_value = sample_candles

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        result = fetch_candles_range("EUR_USD", "M1", from_time, to_time)

        assert len(result) == 2
        assert result[0]["open"] == 1.1000
        mock_client_instance.get_candles.assert_called()

    def test_fetch_candles_empty_response(self, mock_oanda_client):
        """Test handling empty response from OANDA."""
        mock_client_instance = MagicMock()
        mock_oanda_client.return_value = mock_client_instance
        mock_client_instance.get_candles.return_value = []

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = fetch_candles_range("EUR_USD", "M1", from_time)

        assert result == []

    def test_fetch_candles_pagination(self, mock_oanda_client):
        """Test pagination when fetching more than MAX_CANDLES_PER_REQUEST.

        Note: Uses time-based pagination. After the second batch, subsequent calls
        return empty (simulating real API behavior where no more data exists).
        """
        mock_client_instance = MagicMock()
        mock_oanda_client.return_value = mock_client_instance

        # First call returns full page, second call returns partial
        # For M1 candles, generate proper HH:MM timestamps (500 minutes = 8h20m)
        first_batch = [
            {
                "time": f"2023-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
            for i in range(MAX_CANDLES_PER_REQUEST)
        ]
        # Last candle at 08:19:00Z (500th minute from 00:00)

        second_batch = [
            {
                "time": "2023-01-01T08:20:00Z",  # Next minute after first batch
                "open": 1.15,
                "high": 1.2,
                "low": 1.1,
                "close": 1.18,
                "volume": 100,
            }
        ]

        # Generator yields data batches then empty lists forever (simulates real API behavior)
        def candle_generator():
            yield first_batch
            yield second_batch
            while True:
                yield []

        mock_client_instance.get_candles.side_effect = candle_generator()

        from_time = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)  # 10:00, after 08:20

        result = fetch_candles_range("EUR_USD", "M1", from_time, to_time)

        # Should have made multiple calls and combined results
        assert len(result) == MAX_CANDLES_PER_REQUEST + 1
        assert mock_client_instance.get_candles.call_count >= 2

    def test_fetch_candles_oanda_error_handling(self, mock_oanda_client):
        """Test handling of OANDA API errors."""
        from agentfx.adapters.oanda import OandaError

        mock_client_instance = MagicMock()
        mock_oanda_client.return_value = mock_client_instance
        mock_client_instance.get_candles.side_effect = OandaError("API Error")

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = fetch_candles_range("EUR_USD", "M1", from_time)

        # Should return empty list on error, not raise
        assert result == []


@pytest.mark.unit
class TestStoreCandles:
    """Test cases for store_candles function."""

    @pytest.fixture
    def mock_market_data_service(self):
        """Create a mock MarketDataService.

        Note: MarketDataService is imported inside the function using relative import.
        """
        with patch("src.agentfx.api.services.market_data.MarketDataService") as mock:
            yield mock

    @pytest.fixture
    def mock_session_local(self):
        """Create a mock SessionLocal.

        Note: SessionLocal is imported inside the function using relative import.
        """
        with patch("src.agentfx.api.core.database.SessionLocal") as mock:
            mock_db = MagicMock()
            mock.return_value = mock_db
            yield mock, mock_db

    @pytest.fixture
    def sample_candles(self):
        """Sample candle data."""
        return [
            {
                "time": "2023-01-01T12:00:00Z",
                "open": 1.1000,
                "high": 1.1005,
                "low": 1.0995,
                "close": 1.1002,
                "volume": 1000,
                "complete": True,
            },
        ]

    def test_store_candles_success(
        self, mock_market_data_service, mock_session_local, sample_candles
    ):
        """Test successful candle storage."""
        _, mock_db = mock_session_local
        mock_market_data_service.create_candles.return_value = [
            MagicMock(),
            MagicMock(),
        ]

        result = store_candles("EUR_USD", "M1", sample_candles)

        assert result == 2
        mock_market_data_service.create_candles.assert_called_once()
        mock_db.close.assert_called_once()

    def test_store_candles_empty_list(
        self, mock_market_data_service, mock_session_local
    ):
        """Test storing empty candle list."""
        result = store_candles("EUR_USD", "M1", [])

        assert result == 0
        mock_market_data_service.create_candles.assert_not_called()

    def test_store_candles_transforms_data(
        self, mock_market_data_service, mock_session_local, sample_candles
    ):
        """Test that candle data is transformed correctly."""
        _, mock_db = mock_session_local
        mock_market_data_service.create_candles.return_value = [MagicMock()]

        store_candles("EUR_USD", "M1", sample_candles)

        # Verify the call arguments
        call_args = mock_market_data_service.create_candles.call_args
        assert call_args.kwargs["exchange"] == "oanda"
        assert call_args.kwargs["instrument"] == "EUR_USD"
        assert call_args.kwargs["timeframe"] == "M1"
        assert call_args.kwargs["update_if_exists"] is True

        # Verify candle data transformation
        candles_data = call_args.kwargs["candles_data"]
        assert len(candles_data) == 1
        assert candles_data[0]["timestamp"] == "2023-01-01T12:00:00Z"
        assert candles_data[0]["open"] == 1.1000


@pytest.mark.unit
class TestBackfillInstrument:
    """Test cases for backfill_instrument function."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for backfill_instrument.

        Note: Functions and classes are imported inside the function using relative imports.
        """
        with (
            patch(
                "src.agentfx.api.services.market_data.MarketDataService"
            ) as mock_service,
            patch("src.agentfx.api.core.database.SessionLocal") as mock_session,
            patch(
                "src.agentfx.api.workers.candle_ingestion.fetch_candles_range"
            ) as mock_fetch,
            patch(
                "src.agentfx.api.workers.candle_ingestion.store_candles"
            ) as mock_store,
            patch("src.agentfx.api.workers.candle_ingestion.time.sleep"),
        ):
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            yield {
                "service": mock_service,
                "session": mock_session,
                "db": mock_db,
                "fetch": mock_fetch,
                "store": mock_store,
            }

    def test_backfill_no_existing_data(self, mock_dependencies):
        """Test backfill when no existing data — uses chunked backfill for large ranges."""
        mock_dependencies["service"].get_latest_candle.return_value = None
        mock_dependencies["fetch"].return_value = [{"time": "2023-01-01T12:00:00Z"}]
        mock_dependencies["store"].return_value = 100

        result = backfill_instrument("EUR_USD", "M1")

        mock_dependencies["service"].get_latest_candle.assert_called_once()
        # For BACKFILL_DAYS > LARGE_GAP_CHUNK_DAYS (7), uses chunked processing
        # which calls fetch+store once per chunk instead of once total
        if settings.BACKFILL_DAYS > LARGE_GAP_CHUNK_DAYS:
            assert mock_dependencies["fetch"].call_count > 0
            assert mock_dependencies["store"].call_count > 0
            assert result == 100 * mock_dependencies["store"].call_count
        else:
            assert result == 100
            mock_dependencies["fetch"].assert_called_once()
            mock_dependencies["store"].assert_called_once()

    def test_backfill_with_existing_data_gap(self, mock_dependencies):
        """Test backfill when there's a gap (data exists but outdated)."""
        # Latest candle is 2 hours ago
        latest_candle = MagicMock()
        latest_candle.timestamp = datetime.now(UTC) - timedelta(hours=2)

        mock_dependencies["service"].get_latest_candle.return_value = latest_candle
        mock_dependencies["fetch"].return_value = [{"time": "2023-01-01T12:00:00Z"}]
        mock_dependencies["store"].return_value = 50

        result = backfill_instrument("EUR_USD", "M1")

        assert result == 50
        mock_dependencies["fetch"].assert_called_once()

    def test_backfill_no_gap_to_fill(self, mock_dependencies):
        """Test backfill when gap is negligible (<1 minute)."""
        # Latest candle is 30 seconds ago
        latest_candle = MagicMock()
        latest_candle.timestamp = datetime.now(UTC) - timedelta(seconds=30)

        mock_dependencies["service"].get_latest_candle.return_value = latest_candle

        result = backfill_instrument("EUR_USD", "M1")

        assert result == 0
        mock_dependencies["fetch"].assert_not_called()
        mock_dependencies["store"].assert_not_called()


@pytest.mark.unit
class TestFetchAndStoreRecent:
    """Test cases for fetch_and_store_recent function."""

    @pytest.fixture
    def mock_oanda(self):
        """Mock OANDA client.

        Note: OandaClient is imported inside the function, so we patch at the source.
        """
        with patch("agentfx.adapters.oanda.OandaClient") as mock:
            yield mock

    @pytest.fixture
    def mock_store(self):
        """Mock store_candles function."""
        with patch("src.agentfx.api.workers.candle_ingestion.store_candles") as mock:
            yield mock

    def test_fetch_and_store_recent_success(self, mock_oanda, mock_store):
        """Test successful recent candle fetch and store."""
        mock_client = MagicMock()
        mock_oanda.return_value = mock_client
        mock_client.get_candles.return_value = [
            {
                "time": "2023-01-01T12:00:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
        ]
        mock_store.return_value = 1

        result = fetch_and_store_recent("EUR_USD", "M1", count=10)

        assert result == 1
        mock_client.get_candles.assert_called_once_with(
            instrument="EUR_USD",
            granularity="M1",
            count=10,
        )

    def test_fetch_and_store_recent_oanda_error(self, mock_oanda, mock_store):
        """Test handling OANDA errors in recent fetch."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda.return_value = mock_client
        mock_client.get_candles.side_effect = OandaError("Connection failed")

        with pytest.raises(OandaError):
            fetch_and_store_recent("EUR_USD", "M1")


@pytest.mark.unit
@pytest.mark.asyncio
class TestIngestionLoop:
    """Test cases for the async ingestion loop functions."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for ingestion."""
        with patch("src.agentfx.api.workers.candle_ingestion.settings") as mock:
            mock.INGESTION_ENABLED = True
            mock.OANDA_API_KEY = "test-key"
            mock.OANDA_API_HOST = "api-fxpractice.oanda.com"
            mock.INGESTION_POLL_INTERVAL = 60
            mock.CANDLE_HEALTH_CHECK_INTERVAL = 3600
            mock.BACKFILL_DAYS = 1825
            mock.instruments_list = [
                "EUR_USD",
                "GBP_USD",
                "USD_JPY",
                "AUD_USD",
                "USD_CHF",
                "USD_CAD",
            ]
            mock.granularities_list = [
                "M1",
                "M5",
                "M10",
                "M30",
                "H1",
                "H4",
                "H12",
                "D",
                "W",
                "M",
            ]
            yield mock

    async def test_startup_backfill_runs_for_all_instruments(self, mock_settings):
        """Test that startup_backfill processes all instruments."""
        # Mock the settings to have predictable values
        mock_settings.instruments_list = ["EUR_USD", "GBP_USD"]
        mock_settings.granularities_list = ["M1", "H1"]

        with (
            patch(
                "src.agentfx.api.workers.candle_ingestion.backfill_instrument"
            ) as mock_backfill,
            patch("src.agentfx.api.workers.candle_ingestion._shutdown_event", None),
        ):
            mock_backfill.return_value = 10

            from src.agentfx.api.workers.candle_ingestion import startup_backfill

            result = await startup_backfill()

            # Should call backfill for each instrument/granularity combo
            expected_calls = len(mock_settings.instruments_list) * len(
                mock_settings.granularities_list
            )
            assert mock_backfill.call_count == expected_calls
            assert result == 10 * expected_calls

    async def test_start_ingestion_worker_disabled(self, mock_settings):
        """Test that worker doesn't start when disabled."""
        mock_settings.INGESTION_ENABLED = False

        from src.agentfx.api.workers.candle_ingestion import start_ingestion_worker

        await start_ingestion_worker()

        # No task should be created when disabled
        from src.agentfx.api.workers.candle_ingestion import _worker_task as task_after

        assert task_after is None

    async def test_start_ingestion_worker_no_credentials(self, mock_settings):
        """Test that worker doesn't start without OANDA credentials."""
        mock_settings.INGESTION_ENABLED = True
        mock_settings.OANDA_API_KEY = "your-api-key-here"  # Default placeholder

        from src.agentfx.api.workers.candle_ingestion import start_ingestion_worker

        await start_ingestion_worker()

        from src.agentfx.api.workers.candle_ingestion import _worker_task as task_after

        assert task_after is None


@pytest.mark.unit
class TestExponentialBackoff:
    """Test cases for exponential backoff logic."""

    def test_backoff_doubles_on_failure(self):
        """Test that backoff time doubles after each failure."""
        from src.agentfx.api.workers.candle_ingestion import (
            INITIAL_BACKOFF_SECONDS,
            MAX_BACKOFF_SECONDS,
        )

        backoff = INITIAL_BACKOFF_SECONDS
        assert backoff == 5

        # Simulate failures
        for _ in range(3):
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

        assert backoff == 40  # 5 -> 10 -> 20 -> 40

    def test_backoff_caps_at_max(self):
        """Test that backoff doesn't exceed MAX_BACKOFF_SECONDS."""
        from src.agentfx.api.workers.candle_ingestion import (
            INITIAL_BACKOFF_SECONDS,
            MAX_BACKOFF_SECONDS,
        )

        backoff = INITIAL_BACKOFF_SECONDS

        # Simulate many failures
        for _ in range(20):
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

        assert backoff == MAX_BACKOFF_SECONDS
        assert backoff == 300  # 5 minutes max


@pytest.mark.unit
class TestHealLargeGap:
    """Test cases for heal_large_gap function."""

    @pytest.fixture
    def mock_fetch_and_store(self):
        """Mock fetch_candles_range and store_candles."""
        with (
            patch(
                "src.agentfx.api.workers.candle_ingestion.fetch_candles_range"
            ) as mock_fetch,
            patch(
                "src.agentfx.api.workers.candle_ingestion.store_candles"
            ) as mock_store,
        ):
            yield {"fetch": mock_fetch, "store": mock_store}

    def test_heal_large_gap_processes_in_chunks(self, mock_fetch_and_store):
        """Test that large gaps are processed in 7-day chunks."""
        # 21-day gap should result in 3 chunks
        mock_fetch_and_store["fetch"].return_value = [{"time": "2023-01-01T12:00:00Z"}]
        mock_fetch_and_store["store"].return_value = 100

        gap_start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        gap_end = datetime(2023, 1, 22, 0, 0, 0, tzinfo=UTC)  # 21 days later

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = heal_large_gap("EUR_USD", "M1", gap_start, gap_end)

        # Should have 3 chunks (7 + 7 + 7 days)
        assert mock_fetch_and_store["fetch"].call_count == 3
        assert result == 300  # 100 candles per chunk * 3 chunks

    def test_heal_large_gap_handles_empty_chunks(self, mock_fetch_and_store):
        """Test that empty chunks (market closures) are handled gracefully."""
        # First chunk returns data, second is empty (weekend), third returns data
        mock_fetch_and_store["fetch"].side_effect = [
            [{"time": "2023-01-01T12:00:00Z"}],
            [],  # Empty chunk
            [{"time": "2023-01-15T12:00:00Z"}],
        ]
        mock_fetch_and_store["store"].return_value = 50

        gap_start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        gap_end = datetime(2023, 1, 22, 0, 0, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = heal_large_gap("EUR_USD", "M1", gap_start, gap_end)

        # Only 2 chunks had data
        assert mock_fetch_and_store["store"].call_count == 2
        assert result == 100

    def test_heal_large_gap_retries_on_504_error(self, mock_fetch_and_store):
        """Test that 504 errors trigger retry with exponential backoff."""
        from agentfx.adapters.oanda import OandaError

        # First call fails with 504, second succeeds
        mock_fetch_and_store["fetch"].side_effect = [
            OandaError("504 Gateway Timeout"),
            [{"time": "2023-01-01T12:00:00Z"}],
        ]
        mock_fetch_and_store["store"].return_value = 50

        gap_start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        gap_end = datetime(2023, 1, 8, 0, 0, 0, tzinfo=UTC)  # 7 days

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep") as mock_sleep:
            result = heal_large_gap("EUR_USD", "M1", gap_start, gap_end)

        # Should have retried
        assert mock_fetch_and_store["fetch"].call_count == 2
        assert result == 50
        # Should have called sleep for backoff
        assert mock_sleep.call_count >= 1

    def test_heal_large_gap_retries_on_429_error(self, mock_fetch_and_store):
        """Test that 429 rate limit errors trigger retry."""
        from agentfx.adapters.oanda import OandaError

        # First call fails with 429, second succeeds
        mock_fetch_and_store["fetch"].side_effect = [
            OandaError("429 Too Many Requests"),
            [{"time": "2023-01-01T12:00:00Z"}],
        ]
        mock_fetch_and_store["store"].return_value = 50

        gap_start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        gap_end = datetime(2023, 1, 8, 0, 0, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = heal_large_gap("EUR_USD", "M1", gap_start, gap_end)

        assert mock_fetch_and_store["fetch"].call_count == 2
        assert result == 50


@pytest.mark.unit
class TestFetchCandlesRangeRetry:
    """Test cases for fetch_candles_range retry logic."""

    @pytest.fixture
    def mock_oanda_client(self):
        """Create a mock OANDA client."""
        with patch("agentfx.adapters.oanda.OandaClient") as mock:
            yield mock

    def test_fetch_retries_on_504_error(self, mock_oanda_client):
        """Test that 504 errors trigger retry."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda_client.return_value = mock_client

        # Generator that yields: error, success, then empty (to end pagination)
        def candle_generator():
            yield OandaError("504 Gateway Timeout")  # First call fails
            yield [
                {
                    "time": "2023-01-01T12:04:00Z",
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 100,
                }
            ]
            while True:
                yield []  # Pagination ends

        gen = candle_generator()

        def side_effect_fn(*args, **kwargs):
            result = next(gen)
            if isinstance(result, Exception):
                raise result
            return result

        mock_client.get_candles.side_effect = side_effect_fn

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = fetch_candles_range(
                "EUR_USD", "M1", from_time, to_time, max_retries=3
            )

        assert len(result) == 1
        assert (
            mock_client.get_candles.call_count >= 2
        )  # At least 2 calls (error + success)

    def test_fetch_returns_partial_on_max_retries(self, mock_oanda_client):
        """Test that max retries exceeded returns partial results."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda_client.return_value = mock_client

        # All calls fail with 504
        mock_client.get_candles.side_effect = OandaError("504 Gateway Timeout")

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = fetch_candles_range(
                "EUR_USD", "M1", from_time, to_time, max_retries=2
            )

        # Should return empty after all retries failed
        assert result == []
        assert mock_client.get_candles.call_count == 2

    def test_fetch_no_retry_on_non_retryable_error(self, mock_oanda_client):
        """Test that non-retryable errors don't trigger retry."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda_client.return_value = mock_client

        # Error that shouldn't trigger retry
        mock_client.get_candles.side_effect = OandaError("400 Bad Request")

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        result = fetch_candles_range("EUR_USD", "M1", from_time, to_time, max_retries=3)

        # Should not retry for 400 errors
        assert result == []
        assert mock_client.get_candles.call_count == 1


@pytest.mark.unit
class TestGapDetectionMultiplier:
    """Test cases for the gap detection sensitivity constant."""

    def test_gap_detection_multiplier_value(self):
        """Test that the multiplier is 5."""
        assert GAP_DETECTION_MULTIPLIER == 5

    def test_m1_threshold_is_5_minutes(self):
        """Test that M1 gap threshold is 300 seconds (5 min), not 7200."""
        threshold = GRANULARITY_SECONDS["M1"] * GAP_DETECTION_MULTIPLIER
        assert threshold == 300

    def test_m5_threshold_is_25_minutes(self):
        """Test that M5 gap threshold is 1500 seconds (25 min)."""
        threshold = GRANULARITY_SECONDS["M5"] * GAP_DETECTION_MULTIPLIER
        assert threshold == 1500

    def test_h1_threshold_is_5_hours(self):
        """Test that H1 gap threshold is 18000 seconds (5 hours)."""
        threshold = GRANULARITY_SECONDS["H1"] * GAP_DETECTION_MULTIPLIER
        assert threshold == 18000

    def test_d_threshold_is_5_days(self):
        """Test that D gap threshold is 432000 seconds (5 days)."""
        threshold = GRANULARITY_SECONDS["D"] * GAP_DETECTION_MULTIPLIER
        assert threshold == 432000


@pytest.mark.unit
class TestMergeNearbyGaps:
    """Test cases for _merge_nearby_gaps function."""

    def test_empty_gaps_returns_empty(self):
        """Test that empty input returns empty output."""
        assert _merge_nearby_gaps([], 60) == []

    def test_single_gap_unchanged(self):
        """Test that a single gap is returned unchanged."""
        gap = {
            "from": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            "duration_seconds": 1800,
            "duration_hours": 0.5,
        }
        result = _merge_nearby_gaps([gap], 60)
        assert len(result) == 1
        assert result[0]["from"] == gap["from"]
        assert result[0]["to"] == gap["to"]

    def test_adjacent_gaps_merged(self):
        """Test that gaps separated by less than merge_threshold are merged."""
        gap1 = {
            "from": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 5, tzinfo=UTC),
            "duration_seconds": 300,
            "duration_hours": 300 / 3600,
        }
        gap2 = {
            "from": datetime(2024, 1, 15, 10, 8, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
            "duration_seconds": 420,
            "duration_hours": 420 / 3600,
        }
        # 3 minutes apart, merge_threshold = 10 * 60 = 600s
        result = _merge_nearby_gaps([gap1, gap2], 60)
        assert len(result) == 1
        assert result[0]["from"] == gap1["from"]
        assert result[0]["to"] == gap2["to"]

    def test_distant_gaps_not_merged(self):
        """Test that gaps separated by more than merge_threshold stay separate."""
        gap1 = {
            "from": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 5, tzinfo=UTC),
            "duration_seconds": 300,
            "duration_hours": 300 / 3600,
        }
        gap2 = {
            "from": datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 12, 10, tzinfo=UTC),
            "duration_seconds": 600,
            "duration_hours": 600 / 3600,
        }
        # 2 hours apart, merge_threshold = 600s
        result = _merge_nearby_gaps([gap1, gap2], 60)
        assert len(result) == 2

    def test_three_gaps_merge_into_one(self):
        """Test that three adjacent gaps merge into one."""
        base = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        gaps = [
            {
                "from": base,
                "to": base + timedelta(minutes=3),
                "duration_seconds": 180,
                "duration_hours": 0.05,
            },
            {
                "from": base + timedelta(minutes=5),
                "to": base + timedelta(minutes=8),
                "duration_seconds": 180,
                "duration_hours": 0.05,
            },
            {
                "from": base + timedelta(minutes=9),
                "to": base + timedelta(minutes=12),
                "duration_seconds": 180,
                "duration_hours": 0.05,
            },
        ]
        result = _merge_nearby_gaps(gaps, 60)
        assert len(result) == 1
        assert result[0]["from"] == base
        assert result[0]["to"] == base + timedelta(minutes=12)

    def test_custom_merge_threshold(self):
        """Test that custom merge_threshold_seconds is respected."""
        gap1 = {
            "from": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 5, tzinfo=UTC),
            "duration_seconds": 300,
            "duration_hours": 300 / 3600,
        }
        gap2 = {
            "from": datetime(2024, 1, 15, 10, 8, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
            "duration_seconds": 420,
            "duration_hours": 420 / 3600,
        }
        # 3 minutes apart, but custom threshold is 60s (should NOT merge)
        result = _merge_nearby_gaps([gap1, gap2], 60, merge_threshold_seconds=60)
        assert len(result) == 2

    def test_unsorted_gaps_handled(self):
        """Test that out-of-order gaps are sorted before merging."""
        gap_late = {
            "from": datetime(2024, 1, 15, 10, 8, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
            "duration_seconds": 420,
            "duration_hours": 420 / 3600,
        }
        gap_early = {
            "from": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "to": datetime(2024, 1, 15, 10, 5, tzinfo=UTC),
            "duration_seconds": 300,
            "duration_hours": 300 / 3600,
        }
        result = _merge_nearby_gaps([gap_late, gap_early], 60)
        assert len(result) == 1
        assert result[0]["from"] == gap_early["from"]
        assert result[0]["to"] == gap_late["to"]


@pytest.mark.unit
class TestRepairCandleData:
    """Test cases for repair_candle_data function."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for repair_candle_data."""
        with (
            patch(
                "src.agentfx.api.workers.candle_ingestion.detect_and_heal_gaps"
            ) as mock_heal,
            patch("src.agentfx.api.core.database.SessionLocal") as mock_session,
            patch("src.agentfx.api.workers.candle_ingestion.settings") as mock_settings,
        ):
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            mock_settings.instruments_list = ["EUR_USD", "GBP_USD"]
            mock_settings.granularities_list = ["M1", "H1"]
            yield {
                "heal": mock_heal,
                "session": mock_session,
                "db": mock_db,
                "settings": mock_settings,
            }

    def test_repair_processes_all_combinations(self, mock_dependencies):
        """Test repair processes all instrument/granularity combinations."""
        mock_dependencies["heal"].return_value = (10, False)

        stats = repair_candle_data()

        # 2 instruments * 2 granularities = 4 calls
        assert mock_dependencies["heal"].call_count == 4
        assert stats["total_healed"] == 40

    def test_repair_with_specific_instruments(self, mock_dependencies):
        """Test repair with specific instruments only."""
        mock_dependencies["heal"].return_value = (5, False)

        stats = repair_candle_data(instruments=["EUR_USD"])

        # 1 instrument * 2 granularities = 2 calls
        assert mock_dependencies["heal"].call_count == 2
        assert stats["total_healed"] == 10

    def test_repair_with_specific_granularities(self, mock_dependencies):
        """Test repair with specific granularities only."""
        mock_dependencies["heal"].return_value = (5, False)

        stats = repair_candle_data(granularities=["M1"])

        # 2 instruments * 1 granularity = 2 calls
        assert mock_dependencies["heal"].call_count == 2
        assert stats["total_healed"] == 10

    def test_repair_handles_errors_gracefully(self, mock_dependencies):
        """Test that repair continues when one combination fails."""
        mock_dependencies["heal"].side_effect = [
            Exception("API error"),
            (10, False),
            (5, False),
            (20, False),
        ]

        stats = repair_candle_data()

        assert mock_dependencies["heal"].call_count == 4
        assert stats["total_healed"] == 35
        assert stats["by_instrument"]["EUR_USD"]["M1"] == -1

    def test_repair_returns_per_instrument_stats(self, mock_dependencies):
        """Test that repair returns stats broken down by instrument/granularity."""
        mock_dependencies["heal"].side_effect = [
            (10, False),
            (20, False),
            (30, False),
            (40, False),
        ]

        stats = repair_candle_data()

        assert stats["by_instrument"]["EUR_USD"]["M1"] == 10
        assert stats["by_instrument"]["EUR_USD"]["H1"] == 20
        assert stats["by_instrument"]["GBP_USD"]["M1"] == 30
        assert stats["by_instrument"]["GBP_USD"]["H1"] == 40


@pytest.mark.unit
class TestFetchCandlesRange502Retry:
    """Test that 502 Bad Gateway errors trigger retries."""

    @pytest.fixture
    def mock_oanda_client(self):
        """Create a mock OANDA client."""
        with patch("agentfx.adapters.oanda.OandaClient") as mock:
            yield mock

    def test_fetch_retries_on_502_error(self, mock_oanda_client):
        """Test that 502 errors trigger retry (previously silently failed)."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda_client.return_value = mock_client

        def candle_generator():
            yield OandaError("502 Bad Gateway")  # First call fails
            yield [
                {
                    "time": "2023-01-01T12:04:00Z",
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 100,
                }
            ]
            while True:
                yield []

        gen = candle_generator()

        def side_effect_fn(*args, **kwargs):
            result = next(gen)
            if isinstance(result, Exception):
                raise result
            return result

        mock_client.get_candles.side_effect = side_effect_fn

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = fetch_candles_range(
                "EUR_USD", "M1", from_time, to_time, max_retries=3
            )

        assert len(result) == 1
        assert mock_client.get_candles.call_count >= 2

    def test_fetch_retries_on_connection_error(self, mock_oanda_client):
        """Test that connection_error from OandaClient triggers retry."""
        from agentfx.adapters.oanda import OandaError

        mock_client = MagicMock()
        mock_oanda_client.return_value = mock_client

        def candle_generator():
            yield OandaError("connection_error: Read timed out")
            yield [
                {
                    "time": "2023-01-01T12:04:00Z",
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 100,
                }
            ]
            while True:
                yield []

        gen = candle_generator()

        def side_effect_fn(*args, **kwargs):
            result = next(gen)
            if isinstance(result, Exception):
                raise result
            return result

        mock_client.get_candles.side_effect = side_effect_fn

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=UTC)

        with patch("src.agentfx.api.workers.candle_ingestion.time.sleep"):
            result = fetch_candles_range(
                "EUR_USD", "M1", from_time, to_time, max_retries=3
            )

        assert len(result) == 1
        assert mock_client.get_candles.call_count >= 2


@pytest.mark.unit
class TestGapPrioritization:
    """Test that gaps are processed newest-first with a configurable limit."""

    @pytest.fixture
    def mock_deps(self):
        """Mock all dependencies for detect_and_heal_gaps."""
        with (
            patch("src.agentfx.api.services.market_data.MarketDataService") as mock_mds,
            patch("src.agentfx.api.core.database.SessionLocal") as mock_session,
            patch(
                "src.agentfx.api.workers.candle_ingestion.is_market_closure",
                return_value=(False, ""),
            ),
            patch(
                "src.agentfx.api.workers.candle_ingestion.fetch_candles_range"
            ) as mock_fetch,
            patch(
                "src.agentfx.api.workers.candle_ingestion.store_candles",
                return_value=1,
            ) as mock_store,
            patch(
                "src.agentfx.api.services.verified_gap.VerifiedGapService"
            ) as mock_vgs,
        ):
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            mock_vgs.get_verified_gaps.return_value = set()
            yield {
                "mds": mock_mds,
                "db": mock_db,
                "fetch": mock_fetch,
                "store": mock_store,
                "vgs": mock_vgs,
            }

    def test_gaps_processed_newest_first(self, mock_deps):
        """Test that the most recent gap is healed before older gaps."""
        # Create timestamps with 3 gaps, each separated by >60 candles (>3600s)
        # to prevent _merge_nearby_gaps from merging them (merge threshold = 60 * 60s = 3600s)
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = [
            base,  # 0: 00:00
            base + timedelta(minutes=1),  # 1: 00:01
            # GAP 1: 10 min gap
            base + timedelta(minutes=11),  # 2: 00:11
            # 65 normal candles to separate gaps (> merge threshold of 60 min)
            *[base + timedelta(minutes=11 + i) for i in range(1, 66)],
            # GAP 2: 10 min gap at minute 76
            base + timedelta(minutes=86),  # ~: 01:26
            # 65 normal candles to separate gaps
            *[base + timedelta(minutes=86 + i) for i in range(1, 66)],
            # GAP 3: 10 min gap at minute 151
            base + timedelta(minutes=161),  # ~: 02:41
        ]
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        # Track the order gaps are fetched
        fetch_order = []

        def track_fetch(instrument, granularity, from_time, to_time):
            fetch_order.append(from_time)
            return [
                {
                    "time": "2023-01-01T00:05:00Z",
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 100,
                }
            ]

        mock_deps["fetch"].side_effect = track_fetch

        detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        # Should process 3 gaps, newest first
        assert len(fetch_order) == 3
        # Most recent gap first, then middle, then oldest
        assert fetch_order[0] > fetch_order[1] > fetch_order[2]

    def test_max_gaps_limit_respected(self, mock_deps):
        """Test that processing stops after settings.MAX_GAPS_PER_HEALTH_CHECK gaps."""
        # Create timestamps with 10 gaps, each separated by >60 candles
        # to prevent merging (merge threshold = 60 * 60s = 3600s for M1)
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = []
        offset = 0
        for i in range(10):
            # Add a normal candle
            timestamps.append(base + timedelta(minutes=offset))
            offset += 1
            # Add a 10 min gap (> 5 min threshold)
            offset += 10
            # Add candle after gap
            timestamps.append(base + timedelta(minutes=offset))
            offset += 1
            # Add 65 normal candles to separate from next gap (> merge threshold)
            for j in range(65):
                timestamps.append(base + timedelta(minutes=offset))
                offset += 1

        mock_deps["mds"].get_candle_timestamps.return_value = timestamps
        mock_deps["fetch"].return_value = [
            {
                "time": "2023-01-01T00:05:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
        ]

        # Patch settings to use a small limit for testing
        with patch.object(settings, "MAX_GAPS_PER_HEALTH_CHECK", 5):
            detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        # Should stop at the patched limit
        assert mock_deps["fetch"].call_count == 5

    def test_max_gaps_per_health_check_default(self):
        """Test that MAX_GAPS_PER_HEALTH_CHECK defaults to 500."""
        assert settings.MAX_GAPS_PER_HEALTH_CHECK == 500


# NOTE: Market closure tests have been moved to test_market_calendar.py
# The is_market_closure function is now in src.agentfx.api.workers.market_calendar


class TestNextPollWakeTime:
    """Wilson UAT 2026-08-02: ingestion loop now sleeps until the next
    minute boundary + buffer instead of a fixed 60s. `_next_poll_wake_time`
    is the pure helper that decides when to wake — locked here so we
    don't regress the boundary math."""

    def test_aligns_to_next_minute_boundary_plus_buffer(self):
        # 12:00:15 with 60s boundary + 3s buffer → 12:01:03
        now = datetime(2026, 8, 2, 12, 0, 15, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=3)
        assert wake == datetime(2026, 8, 2, 12, 1, 3, tzinfo=UTC)

    def test_when_before_boundary_buffer_within_same_boundary(self):
        # 12:00:01 with buffer=3 → 12:00:03 (still within the current
        # boundary's buffer window — DON'T advance to next minute).
        now = datetime(2026, 8, 2, 12, 0, 1, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=3)
        assert wake == datetime(2026, 8, 2, 12, 0, 3, tzinfo=UTC)

    def test_when_past_boundary_buffer_jumps_to_next(self):
        # 12:00:05 with buffer=3 → next boundary + buffer = 12:01:03
        now = datetime(2026, 8, 2, 12, 0, 5, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=3)
        assert wake == datetime(2026, 8, 2, 12, 1, 3, tzinfo=UTC)

    def test_exactly_on_boundary_plus_buffer_advances(self):
        # 12:01:03 exactly with buffer=3 → next is 12:02:03. Otherwise
        # we'd bounce immediately with sleep=0 → tight loop.
        now = datetime(2026, 8, 2, 12, 1, 3, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=3)
        assert wake == datetime(2026, 8, 2, 12, 2, 3, tzinfo=UTC)

    def test_sub_second_now_still_aligns_cleanly(self):
        # 12:00:00.500 with buffer=3 → 12:00:03 (buffer still ahead).
        now = datetime(2026, 8, 2, 12, 0, 0, 500_000, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=3)
        assert wake == datetime(2026, 8, 2, 12, 0, 3, tzinfo=UTC)

    def test_zero_buffer_wakes_right_at_boundary(self):
        # No buffer → wake at exact minute boundary.
        now = datetime(2026, 8, 2, 12, 0, 30, tzinfo=UTC)
        wake = _next_poll_wake_time(now, boundary_seconds=60, buffer_seconds=0)
        assert wake == datetime(2026, 8, 2, 12, 1, 0, tzinfo=UTC)

    def test_default_buffer_is_three_seconds(self):
        # Constant hasn't drifted — the paper runner's assumption of
        # "~3s post bar close before ingestion completes" depends on
        # this value.
        assert INGESTION_ALIGNMENT_BUFFER_SECONDS == 3
