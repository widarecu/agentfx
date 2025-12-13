"""Unit tests for the candle ingestion worker."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from src.agentfx.api.workers.candle_ingestion import (
    INSTRUMENTS,
    GRANULARITIES,
    INITIAL_BACKFILL_DAYS,
    MAX_CANDLES_PER_REQUEST,
    _get_backfill_start_time,
    fetch_candles_range,
    store_candles,
    backfill_instrument,
    fetch_and_store_recent,
)


@pytest.mark.unit
class TestIngestionConfiguration:
    """Test cases for ingestion worker configuration."""

    def test_supported_instruments_count(self):
        """Test that we have exactly 6 supported instruments."""
        assert len(INSTRUMENTS) == 6

    def test_supported_instruments_list(self):
        """Test that all expected instruments are configured."""
        expected = {"EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "USD_CAD"}
        assert set(INSTRUMENTS) == expected

    def test_granularities_configured(self):
        """Test that multiple granularities are configured."""
        assert len(GRANULARITIES) >= 4
        assert "M1" in GRANULARITIES
        assert "H1" in GRANULARITIES

    def test_initial_backfill_days(self):
        """Test that initial backfill is 7 days."""
        assert INITIAL_BACKFILL_DAYS == 7

    def test_max_candles_per_request(self):
        """Test OANDA API pagination limit."""
        assert MAX_CANDLES_PER_REQUEST == 500


@pytest.mark.unit
class TestGetBackfillStartTime:
    """Test cases for _get_backfill_start_time helper."""

    def test_default_backfill_days(self):
        """Test default 7-day backfill calculation."""
        before = datetime.now(timezone.utc)
        result = _get_backfill_start_time()
        after = datetime.now(timezone.utc)

        expected_start = before - timedelta(days=INITIAL_BACKFILL_DAYS)
        expected_end = after - timedelta(days=INITIAL_BACKFILL_DAYS)

        assert expected_start <= result <= expected_end

    def test_custom_backfill_days(self):
        """Test custom backfill days calculation."""
        before = datetime.now(timezone.utc)
        result = _get_backfill_start_time(days=30)
        after = datetime.now(timezone.utc)

        expected_start = before - timedelta(days=30)
        expected_end = after - timedelta(days=30)

        assert expected_start <= result <= expected_end

    def test_backfill_returns_utc_datetime(self):
        """Test that backfill time has UTC timezone."""
        result = _get_backfill_start_time()
        assert result.tzinfo == timezone.utc


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

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2023, 1, 1, 12, 5, 0, tzinfo=timezone.utc)

        result = fetch_candles_range("EUR_USD", "M1", from_time, to_time)

        assert len(result) == 2
        assert result[0]["open"] == 1.1000
        mock_client_instance.get_candles.assert_called()

    def test_fetch_candles_empty_response(self, mock_oanda_client):
        """Test handling empty response from OANDA."""
        mock_client_instance = MagicMock()
        mock_oanda_client.return_value = mock_client_instance
        mock_client_instance.get_candles.return_value = []

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        first_batch = [
            {
                "time": f"2023-01-01T{i:02d}:00:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
            for i in range(MAX_CANDLES_PER_REQUEST)
        ]
        first_batch[-1]["time"] = "2023-01-01T23:59:00Z"

        second_batch = [
            {
                "time": "2023-01-02T00:00:00Z",
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

        from_time = datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2023, 1, 2, 12, 0, 0, tzinfo=timezone.utc)

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

        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        with patch(
            "src.agentfx.api.services.market_data.MarketDataService"
        ) as mock_service, patch(
            "src.agentfx.api.core.database.SessionLocal"
        ) as mock_session, patch(
            "src.agentfx.api.workers.candle_ingestion.fetch_candles_range"
        ) as mock_fetch, patch(
            "src.agentfx.api.workers.candle_ingestion.store_candles"
        ) as mock_store:
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
        """Test backfill when no existing data (7-day backfill)."""
        mock_dependencies["service"].get_latest_candle.return_value = None
        mock_dependencies["fetch"].return_value = [{"time": "2023-01-01T12:00:00Z"}]
        mock_dependencies["store"].return_value = 100

        result = backfill_instrument("EUR_USD", "M1")

        assert result == 100
        mock_dependencies["service"].get_latest_candle.assert_called_once()
        mock_dependencies["fetch"].assert_called_once()
        mock_dependencies["store"].assert_called_once()

    def test_backfill_with_existing_data_gap(self, mock_dependencies):
        """Test backfill when there's a gap (data exists but outdated)."""
        # Latest candle is 2 hours ago
        latest_candle = MagicMock()
        latest_candle.timestamp = datetime.now(timezone.utc) - timedelta(hours=2)

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
        latest_candle.timestamp = datetime.now(timezone.utc) - timedelta(seconds=30)

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
            yield mock

    async def test_startup_backfill_runs_for_all_instruments(self, mock_settings):
        """Test that startup_backfill processes all instruments."""
        with patch(
            "src.agentfx.api.workers.candle_ingestion.backfill_instrument"
        ) as mock_backfill, patch(
            "src.agentfx.api.workers.candle_ingestion._shutdown_event", None
        ):
            mock_backfill.return_value = 10

            from src.agentfx.api.workers.candle_ingestion import startup_backfill

            result = await startup_backfill()

            # Should call backfill for each instrument/granularity combo
            expected_calls = len(INSTRUMENTS) * len(GRANULARITIES)
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
