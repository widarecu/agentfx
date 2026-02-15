"""Unit tests for OANDA adapter."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from src.agentfx.adapters.oanda import (
    OandaClient,
    OandaError,
    to_oanda_instrument,
)


@pytest.mark.unit
@pytest.mark.market_data
class TestOandaAdapter:
    """Test cases for OANDA adapter functionality."""

    def test_to_oanda_instrument_standard_formats(self):
        """Test instrument symbol conversion for standard formats."""
        # Test standard format
        assert to_oanda_instrument("EUR/USD") == "EUR_USD"
        assert to_oanda_instrument("GBP/USD") == "GBP_USD"
        assert to_oanda_instrument("USD/JPY") == "USD_JPY"

        # Test already correct format
        assert to_oanda_instrument("EUR_USD") == "EUR_USD"
        assert to_oanda_instrument("GBP_USD") == "GBP_USD"

        # Test 6-character format
        assert to_oanda_instrument("EURUSD") == "EUR_USD"
        assert to_oanda_instrument("GBPUSD") == "GBP_USD"

        # Test lowercase and spaces
        assert to_oanda_instrument("eur/usd") == "EUR_USD"
        assert to_oanda_instrument("  eur/usd  ") == "EUR_USD"

    def test_to_oanda_instrument_edge_cases(self):
        """Test instrument symbol conversion for edge cases."""
        # Test crypto pairs
        assert to_oanda_instrument("BTC/USD") == "BTC_USD"
        assert to_oanda_instrument("ETH/USD") == "ETH_USD"

        # Test metals
        assert to_oanda_instrument("XAU/USD") == "XAU_USD"
        assert to_oanda_instrument("XAG/USD") == "XAG_USD"

    @pytest.fixture
    def mock_config(self):
        """Mock configuration values."""
        return {"api_key": "test_api_key_12345", "host": "api-fxpractice.oanda.com"}

    @pytest.fixture
    def mock_session(self):
        """Mock requests session."""
        session = MagicMock(spec=requests.Session)
        session.headers = {}
        return session

    @pytest.fixture
    def oanda_client(self, mock_config, mock_session):
        """Create OANDA client with mocked dependencies."""
        with patch(
            "src.agentfx.adapters.oanda.oanda_api_key",
            return_value=mock_config["api_key"],
        ):
            with patch(
                "src.agentfx.adapters.oanda.oanda_api_host",
                return_value=mock_config["host"],
            ):
                with patch("requests.Session", return_value=mock_session):
                    client = OandaClient()
                    return client

    def test_oanda_client_initialization_success(self, oanda_client, mock_config):
        """Test successful OANDA client initialization."""
        assert oanda_client.api_key == mock_config["api_key"]
        assert oanda_client.host == mock_config["host"]
        assert oanda_client.base_url == f"https://{mock_config['host']}/v3"
        assert oanda_client.timeout == 15
        assert "Authorization" in oanda_client.session.headers
        assert (
            oanda_client.session.headers["Authorization"]
            == f"Bearer {mock_config['api_key']}"
        )

    def test_oanda_client_initialization_no_api_key(self):
        """Test OANDA client initialization fails without API key."""
        with patch("src.agentfx.adapters.oanda.oanda_api_key", return_value=None):
            with pytest.raises(OandaError, match="OANDA_API_KEY is not set"):
                OandaClient()

    def test_oanda_client_custom_initialization(self):
        """Test OANDA client initialization with custom parameters."""
        with patch("requests.Session") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = OandaClient(
                api_key="custom_key", host="custom.host.com", timeout=30
            )

            assert client.api_key == "custom_key"
            assert client.host == "custom.host.com"
            assert client.timeout == 30
            assert client.base_url == "https://custom.host.com/v3"

    def test_request_success(self, oanda_client):
        """Test successful API request."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test_data"}

        oanda_client.session.request = MagicMock(return_value=mock_response)

        result = oanda_client._request("GET", "/test", {"param": "value"})

        assert result == {"data": "test_data"}
        oanda_client.session.request.assert_called_once_with(
            "GET",
            "https://api-fxpractice.oanda.com/v3/test",
            params={"param": "value"},
            timeout=15,
        )

    def test_request_rate_limit_retry(self, oanda_client):
        """Test API request with rate limiting and retry logic."""
        # Mock rate limit response followed by success
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"data": "success"}

        oanda_client.session.request = MagicMock(
            side_effect=[rate_limit_response, success_response]
        )

        with patch("time.sleep") as mock_sleep:
            result = oanda_client._request("GET", "/test", {})

        assert result == {"data": "success"}
        assert oanda_client.session.request.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_request_rate_limit_exhausted(self, oanda_client):
        """Test API request fails after maximum rate limit retries."""
        # Mock continuous rate limit responses
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429

        oanda_client.session.request = MagicMock(return_value=rate_limit_response)

        with patch("time.sleep"):
            with pytest.raises(OandaError, match="Rate limited repeatedly by OANDA"):
                oanda_client._request("GET", "/test", {})

        assert oanda_client.session.request.call_count == 5

    def test_request_http_error(self, oanda_client):
        """Test API request handles HTTP errors properly."""
        # Mock error response
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.text = "Bad Request"

        oanda_client.session.request = MagicMock(return_value=error_response)

        with pytest.raises(OandaError, match="400 Bad Request"):
            oanda_client._request("GET", "/test", {})

    def test_get_candles_basic_success(self, oanda_client):
        """Test successful candle retrieval with basic parameters."""
        # Mock API response
        mock_response = {
            "candles": [
                {
                    "time": "2023-01-01T12:00:00.000000000Z",
                    "mid": {"o": "1.1000", "h": "1.1005", "l": "1.0995", "c": "1.1002"},
                    "volume": 100,
                    "complete": True,
                }
            ]
        }

        oanda_client._request = MagicMock(return_value=mock_response)

        result = oanda_client.get_candles("EUR_USD")

        assert len(result) == 1
        candle = result[0]
        assert candle["time"] == "2023-01-01T12:00:00.000000000Z"
        assert candle["open"] == 1.1000
        assert candle["high"] == 1.1005
        assert candle["low"] == 1.0995
        assert candle["close"] == 1.1002
        assert candle["volume"] == 100
        assert candle["complete"] is True

        # Verify request parameters
        oanda_client._request.assert_called_once_with(
            "GET",
            "/instruments/EUR_USD/candles",
            {
                "granularity": "M1",
                "price": "M",
            },
        )

    def test_get_candles_with_bid_ask_prices(self, oanda_client):
        """Test candle retrieval with bid/ask price conversion."""
        mock_response = {
            "candles": [
                {
                    "time": "2023-01-01T12:00:00.000000000Z",
                    "bid": {"o": "1.0998", "h": "1.1003", "l": "1.0993", "c": "1.1000"},
                    "ask": {"o": "1.1002", "h": "1.1007", "l": "1.0997", "c": "1.1004"},
                    "volume": 150,
                    "complete": False,
                }
            ]
        }

        oanda_client._request = MagicMock(return_value=mock_response)

        result = oanda_client.get_candles("EUR_USD", price="B")

        assert len(result) == 1
        candle = result[0]
        # Should calculate mid prices from bid/ask
        assert candle["open"] == (1.0998 + 1.1002) / 2
        assert candle["high"] == (1.1003 + 1.1007) / 2
        assert candle["low"] == (1.0993 + 1.0997) / 2
        assert candle["close"] == (1.1000 + 1.1004) / 2

    def test_get_candles_with_all_parameters(self, oanda_client):
        """Test candle retrieval with all optional parameters."""
        from_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        to_time = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)

        mock_response = {"candles": []}
        oanda_client._request = MagicMock(return_value=mock_response)

        oanda_client.get_candles(
            instrument="EUR_USD",
            granularity="M5",
            price="A",
            from_ts=from_time,
            to_ts=to_time,
            count=100,
            include_first=True,
        )

        # Verify all parameters were passed correctly
        expected_params = {
            "granularity": "M5",
            "price": "A",
            "from": "2023-01-01T12:00:00Z",
            "to": "2023-01-01T13:00:00Z",
            "count": "100",
            "includeFirst": "true",
        }

        oanda_client._request.assert_called_once_with(
            "GET", "/instruments/EUR_USD/candles", expected_params
        )

    def test_get_candles_skips_malformed_data(self, oanda_client):
        """Test that malformed candles are skipped."""
        mock_response = {
            "candles": [
                {
                    "time": "2023-01-01T12:00:00.000000000Z",
                    "mid": {"o": "1.1000", "h": "1.1005", "l": "1.0995", "c": "1.1002"},
                    "volume": 100,
                    "complete": True,
                },
                {
                    "time": "2023-01-01T12:01:00.000000000Z",
                    # Missing mid/bid/ask data - should be skipped
                    "volume": 100,
                    "complete": True,
                },
                {
                    "time": "2023-01-01T12:02:00.000000000Z",
                    "mid": {"o": "1.1003", "h": "1.1008", "l": "1.0998", "c": "1.1005"},
                    "volume": 120,
                    "complete": True,
                },
            ]
        }

        oanda_client._request = MagicMock(return_value=mock_response)

        result = oanda_client.get_candles("EUR_USD")

        # Should only return valid candles (skip malformed one)
        assert len(result) == 2
        assert result[0]["open"] == 1.1000
        assert result[1]["open"] == 1.1003

    def test_get_candles_handles_volume_and_complete_defaults(self, oanda_client):
        """Test candle retrieval handles missing volume and complete fields."""
        mock_response = {
            "candles": [
                {
                    "time": "2023-01-01T12:00:00.000000000Z",
                    "mid": {"o": "1.1000", "h": "1.1005", "l": "1.0995", "c": "1.1002"},
                    # Missing volume and complete fields
                }
            ]
        }

        oanda_client._request = MagicMock(return_value=mock_response)

        result = oanda_client.get_candles("EUR_USD")

        assert len(result) == 1
        candle = result[0]
        assert candle["volume"] == 0  # Default value
        assert candle["complete"] is False  # Default value

    def test_get_candles_instrument_conversion(self, oanda_client):
        """Test that instrument symbols are properly converted."""
        mock_response = {"candles": []}
        oanda_client._request = MagicMock(return_value=mock_response)

        # Test various input formats
        test_cases = [
            ("EUR/USD", "EUR_USD"),
            ("eur/usd", "EUR_USD"),
            ("EURUSD", "EUR_USD"),
            ("  eur/usd  ", "EUR_USD"),
        ]

        for input_symbol, expected_symbol in test_cases:
            oanda_client.get_candles(input_symbol)

            # Verify the converted symbol was used in the request
            call_args = oanda_client._request.call_args
            actual_path = call_args[0][1]
            assert expected_symbol in actual_path

    @pytest.mark.parametrize(
        "granularity", ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    )
    def test_get_candles_different_granularities(self, oanda_client, granularity):
        """Test candle retrieval with different timeframes."""
        mock_response = {"candles": []}
        oanda_client._request = MagicMock(return_value=mock_response)

        oanda_client.get_candles("EUR_USD", granularity=granularity)

        # Verify granularity was passed correctly
        call_args = oanda_client._request.call_args
        params = call_args[0][2]
        assert params["granularity"] == granularity

    def test_error_handling_invalid_response_format(self, oanda_client):
        """Test error handling for invalid response format."""
        # Mock response missing candles key
        mock_response = {"invalid": "response"}
        oanda_client._request = MagicMock(return_value=mock_response)

        result = oanda_client.get_candles("EUR_USD")

        # Should return empty list for invalid response
        assert result == []

    def test_timeout_handling(self, oanda_client):
        """Test timeout is caught and converted to OandaError after retries."""
        oanda_client.session.request = MagicMock(
            side_effect=requests.exceptions.Timeout("Read timed out")
        )

        with patch("time.sleep"):
            with pytest.raises(OandaError, match="connection_error"):
                oanda_client._request("GET", "/test", {})

        # Should retry 5 times before raising
        assert oanda_client.session.request.call_count == 5

    def test_connection_error_handling(self, oanda_client):
        """Test connection error is caught and converted to OandaError after retries."""
        oanda_client.session.request = MagicMock(
            side_effect=requests.exceptions.ConnectionError("Connection refused")
        )

        with patch("time.sleep"):
            with pytest.raises(OandaError, match="connection_error"):
                oanda_client._request("GET", "/test", {})

        # Should retry 5 times before raising
        assert oanda_client.session.request.call_count == 5

    def test_timeout_retries_then_succeeds(self, oanda_client):
        """Test timeout retries and succeeds on later attempt."""
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"data": "success"}

        oanda_client.session.request = MagicMock(
            side_effect=[
                requests.exceptions.Timeout("Read timed out"),
                requests.exceptions.Timeout("Read timed out"),
                success_response,
            ]
        )

        with patch("time.sleep"):
            result = oanda_client._request("GET", "/test", {})

        assert result == {"data": "success"}
        assert oanda_client.session.request.call_count == 3
