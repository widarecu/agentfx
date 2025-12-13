"""Real OANDA REST API integration tests - requires practice environment.

These tests connect to the OANDA practice environment using credentials from .env.test.
Tests require:
- OANDA_API_KEY: Practice API key
- OANDA_ACCOUNT_ID: Practice account ID
- OANDA_API_HOST: api-fxpractice.oanda.com
"""

import pytest
import time
from datetime import datetime, timedelta, timezone

from src.agentfx.adapters.oanda import OandaClient, OandaError, to_oanda_instrument
from src.agentfx.api.core.config import settings


# Skip if OANDA credentials are not configured
pytestmark = pytest.mark.skipif(
    not settings.OANDA_API_KEY or settings.OANDA_API_KEY == "your-api-key-here",
    reason="OANDA credentials not configured in environment",
)


@pytest.mark.integration
@pytest.mark.oanda_real
@pytest.mark.slow
class TestOandaRealApiIntegration:
    """Real OANDA REST API integration tests using practice environment.

    These tests validate the OandaClient adapter against the real OANDA practice API.
    The adapter currently only implements get_candles().
    """

    @pytest.fixture
    def real_oanda_client(self):
        """Create real OANDA client."""
        return OandaClient()

    def test_real_client_initialization(self, real_oanda_client):
        """Test OANDA client initialization with real credentials."""
        # Verify client is properly initialized
        assert real_oanda_client.api_key, "API key not set"
        assert real_oanda_client.host, "Host not set"
        assert real_oanda_client.base_url, "Base URL not set"
        assert real_oanda_client.timeout > 0, "Timeout not set"

        # Verify session headers
        assert "Authorization" in real_oanda_client.session.headers
        assert "Content-Type" in real_oanda_client.session.headers
        assert (
            real_oanda_client.session.headers["Authorization"]
            == f"Bearer {real_oanda_client.api_key}"
        )

    def test_real_candle_retrieval(self, real_oanda_client):
        """Test retrieving real candle data from OANDA."""
        # Get recent candles for EUR_USD
        candles = real_oanda_client.get_candles(
            instrument="EUR_USD", count=10, granularity="M1"
        )

        assert isinstance(candles, list), "Candles should be a list"
        assert len(candles) > 0, "No candles returned"

        # Verify candle structure (OandaClient returns list[dict])
        for candle in candles:
            assert "open" in candle, "Candle missing open price"
            assert "high" in candle, "Candle missing high price"
            assert "low" in candle, "Candle missing low price"
            assert "close" in candle, "Candle missing close price"
            assert "volume" in candle, "Candle missing volume"
            assert "time" in candle, "Candle missing timestamp"

            # Verify price relationships
            assert candle["high"] >= candle["low"], "High price should be >= low price"
            assert candle["high"] >= max(
                candle["open"], candle["close"]
            ), "High should be >= open and close"
            assert candle["low"] <= min(
                candle["open"], candle["close"]
            ), "Low should be <= open and close"
            assert candle["volume"] >= 0, "Volume should be non-negative"

    def test_real_candle_with_different_granularities(self, real_oanda_client):
        """Test retrieving candles with different timeframes."""
        granularities = ["S5", "M1", "M5", "M15", "H1", "D"]

        for granularity in granularities:
            candles = real_oanda_client.get_candles(
                instrument="EUR_USD", count=5, granularity=granularity
            )
            assert isinstance(
                candles, list
            ), f"Candles for {granularity} should be a list"
            assert len(candles) > 0, f"No candles returned for {granularity}"

    def test_real_candle_with_different_instruments(self, real_oanda_client):
        """Test retrieving candles for different currency pairs."""
        instruments = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"]

        for instrument in instruments:
            candles = real_oanda_client.get_candles(
                instrument=instrument, count=5, granularity="M1"
            )
            assert isinstance(
                candles, list
            ), f"Candles for {instrument} should be a list"
            assert len(candles) > 0, f"No candles returned for {instrument}"

    def test_real_rate_limiting_behavior(self, real_oanda_client):
        """Test actual OANDA API rate limiting behavior."""
        # Make rapid requests to test rate limiting
        start_time = time.time()

        for i in range(5):
            real_oanda_client.get_candles(
                instrument="EUR_USD", count=1, granularity="M1"
            )

        end_time = time.time()
        duration = end_time - start_time

        # Should take some time due to rate limiting (OANDA limits are around 100 requests per second)
        # For 5 requests, it should still be fast but not instantaneous
        assert duration < 10.0, f"Requests took too long: {duration}s"

    def test_real_error_handling_invalid_instrument(self, real_oanda_client):
        """Test error handling with invalid instrument name."""
        with pytest.raises(OandaError):
            real_oanda_client.get_candles(
                instrument="INVALID_INSTRUMENT", count=10, granularity="M1"
            )

    def test_real_error_handling_invalid_timeframe(self, real_oanda_client):
        """Test error handling with invalid timeframe."""
        with pytest.raises(OandaError):
            real_oanda_client.get_candles(
                instrument="EUR_USD", count=10, granularity="INVALID"
            )

    def test_real_authentication_failure(self):
        """Test authentication failure with invalid credentials."""
        # Create client with invalid credentials and make a request
        invalid_client = OandaClient(
            api_key="invalid_key", host=settings.OANDA_API_HOST
        )
        with pytest.raises(OandaError, match="401"):
            invalid_client.get_candles(instrument="EUR_USD", count=1, granularity="M1")

    def test_real_network_timeout_handling(self):
        """Test network timeout handling."""
        # Create client with very short timeout
        short_timeout_client = OandaClient(timeout=0.001)  # 1ms timeout

        # This should fail due to timeout
        with pytest.raises(Exception):
            short_timeout_client.get_candles(
                instrument="EUR_USD", count=10, granularity="M1"
            )

    def test_instrument_format_conversion(self):
        """Test instrument format conversion utility."""
        # Test various input formats
        test_cases = [
            ("EURUSD", "EUR_USD"),
            ("eurusd", "EUR_USD"),
            ("EUR/USD", "EUR_USD"),
            ("eur/usd", "EUR_USD"),
            ("EUR_USD", "EUR_USD"),
            ("USD_JPY", "USD_JPY"),
            ("GBP/JPY", "GBP_JPY"),
        ]

        for input_format, expected_output in test_cases:
            result = to_oanda_instrument(input_format)
            assert (
                result == expected_output
            ), f"Conversion failed: {input_format} -> {result}, expected {expected_output}"

    def test_real_historical_data_retrieval(self, real_oanda_client):
        """Test retrieving historical data with date range."""
        # Get candles from a specific date range
        from_date = datetime.now(timezone.utc) - timedelta(days=1)
        to_date = datetime.now(timezone.utc)

        candles = real_oanda_client.get_candles(
            instrument="EUR_USD",
            from_ts=from_date,
            to_ts=to_date,
            granularity="H1",  # Hourly candles
        )

        assert isinstance(candles, list), "Historical candles should be a list"
        # Note: May return empty list on weekends when markets are closed

        # Verify candle time format if candles exist
        if candles:
            for candle in candles:
                assert "time" in candle, "Candle should have time"
                # Time should be ISO format string
                assert isinstance(candle["time"], str), "Time should be string"

    def test_real_api_response_time(self, real_oanda_client):
        """Test API response time performance."""
        # Measure response times for multiple requests
        response_times = []

        for _ in range(5):
            start_time = time.time()
            real_oanda_client.get_candles(
                instrument="EUR_USD", count=1, granularity="M1"
            )
            end_time = time.time()
            response_times.append(end_time - start_time)

        avg_response_time = sum(response_times) / len(response_times)
        max_response_time = max(response_times)

        # Response times should be reasonable
        assert (
            avg_response_time < 2.0
        ), f"Average response time too slow: {avg_response_time}s"
        assert (
            max_response_time < 5.0
        ), f"Max response time too slow: {max_response_time}s"

    def test_candle_count_parameter(self, real_oanda_client):
        """Test that count parameter limits results correctly."""
        for count in [1, 5, 10, 50]:
            candles = real_oanda_client.get_candles(
                instrument="EUR_USD", count=count, granularity="M1"
            )
            assert len(candles) <= count, f"Should return at most {count} candles"
            assert len(candles) > 0, "Should return at least 1 candle"

    def test_candle_price_values_are_realistic(self, real_oanda_client):
        """Test that candle prices are realistic for EUR/USD."""
        candles = real_oanda_client.get_candles(
            instrument="EUR_USD", count=5, granularity="M1"
        )

        for candle in candles:
            # EUR/USD should be between 0.5 and 2.0 (realistic range)
            assert (
                0.5 < candle["open"] < 2.0
            ), f"Open price {candle['open']} unrealistic"
            assert (
                0.5 < candle["high"] < 2.0
            ), f"High price {candle['high']} unrealistic"
            assert 0.5 < candle["low"] < 2.0, f"Low price {candle['low']} unrealistic"
            assert (
                0.5 < candle["close"] < 2.0
            ), f"Close price {candle['close']} unrealistic"
