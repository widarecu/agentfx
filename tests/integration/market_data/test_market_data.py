"""Integration tests for the market data stream."""

import asyncio
import pytest
from unittest.mock import patch

from src.agentfx.api.services.market_data_stream import OandaMarketDataStream
from src.agentfx.api.core.config import settings

# Skip these tests if we don't have OANDA credentials
pytestmark = pytest.mark.skipif(
    not all(
        [
            settings.OANDA_API_KEY,
            settings.OANDA_ACCOUNT_ID,
            settings.OANDA_STREAMING_HOST,
        ]
    ),
    reason="OANDA credentials not configured",
)


class TestMarketDataIntegration:
    """Integration tests for the market data stream."""

    @pytest.fixture
    async def market_data_stream(self):
        """Create a market data stream instance for testing."""
        stream = OandaMarketDataStream()
        yield stream
        await stream.disconnect()

    @pytest.mark.asyncio
    async def test_connect_to_oanda(self, market_data_stream):
        """Test connecting to the OANDA streaming API."""
        # This is a live test that will connect to OANDA's practice server
        connected = await market_data_stream.connect()
        assert connected is True
        assert market_data_stream.active is True

        # Clean up
        await market_data_stream.disconnect()

    @pytest.mark.asyncio
    async def test_subscribe_to_instruments(self, market_data_stream):
        """Test subscribing to instrument updates."""
        # Connect first
        connected = await market_data_stream.connect()
        assert connected is True

        # Subscribe to instruments
        instruments = ["EUR_USD", "USD_JPY"]
        await market_data_stream.subscribe(instruments)

        # Check that the instruments were added to the subscription list
        for instrument in instruments:
            assert instrument in market_data_stream.subscribed_instruments

        # Clean up
        await market_data_stream.disconnect()

    @pytest.mark.asyncio
    async def test_receive_market_data(self, market_data_stream):
        """Test receiving market data updates."""
        # This test will wait for a short time to receive market data
        connected = await market_data_stream.connect()
        assert connected is True

        # Subscribe to an instrument
        await market_data_stream.subscribe(["EUR_USD"])

        # Start listening for messages (with a timeout)
        try:
            # Wait for a short time to receive messages
            await asyncio.wait_for(market_data_stream._listen(), timeout=5.0)
        except TimeoutError:
            # Expected - we just want to receive some messages
            pass

        # Clean up
        await market_data_stream.disconnect()

    @pytest.mark.asyncio
    async def test_reconnect_logic(self, market_data_stream):
        """Test the reconnection logic."""
        # Mock the connect method to fail first, then succeed
        original_connect = market_data_stream.connect
        connect_calls = 0

        async def mock_connect():
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls == 1:
                raise ConnectionError("Simulated connection error")
            return await original_connect()

        market_data_stream.connect = mock_connect

        # Try to connect (should trigger a reconnection)
        with patch("asyncio.sleep", return_value=None):  # Skip sleep during test
            connected = await market_data_stream._reconnect()

        assert connected is True
        assert connect_calls == 2  # Should have retried once

        # Clean up
        await market_data_stream.disconnect()
