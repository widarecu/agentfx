import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, Set, Optional, List, Tuple, Any, Union

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from ..core.websocket import manager
from ..core.config import settings
from ..models.market_data import Candle, MarketDataUpdate
from ..schemas.market_data import CandleCreate
from ..core.database import SessionLocal

logger = logging.getLogger(__name__)

class OandaMarketDataStream:
    """Handles WebSocket connection to OANDA's streaming API and manages data distribution."""
    
    def __init__(self):
        # OANDA streaming API requires the account ID in the path and instruments as query parameters
        self.ws_url = f"wss://{settings.OANDA_STREAMING_HOST}/v3/accounts/{settings.OANDA_ACCOUNT_ID}/pricing/stream"
        self.instruments = "EUR_USD,USD_JPY,GBP_USD"  # Default instruments to subscribe to
        self.headers = {
            "Authorization": f"Bearer {settings.OANDA_API_KEY}",
            "Content-Type": "application/json"
        }
        self.reconnect_attempts = 0
        self.active = False
        self.subscribed_instruments: Set[str] = set()
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stop_event = asyncio.Event()
        
    async def _debug_http_request(self, method, url, **kwargs):
        """Helper method to make HTTP requests with detailed logging"""
        logger.debug(f"Making {method} request to {url}")
        logger.debug(f"Headers: {kwargs.get('headers', {})}")
        
        try:
            async with self.session.request(method, url, **kwargs) as resp:
                response_text = await resp.text()
                logger.debug(f"Response status: {resp.status}")
                logger.debug(f"Response headers: {dict(resp.headers)}")
                logger.debug(f"Response body: {response_text}")
                return resp
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise

    async def _get_rate_limit_delay(self, response_headers: dict) -> float:
        """Calculate delay based on rate limit headers"""
        retry_after = response_headers.get('Retry-After')
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
        
        # Default exponential backoff with jitter
        return min(2 ** self.reconnect_attempts + random.uniform(0, 1), 30)

    async def connect(self) -> bool:
        """Establish WebSocket connection to OANDA's streaming API with rate limit handling."""
        if self.active:
            logger.warning("WebSocket connection already active")
            return False

        # Add rate limiting delay if needed
        if self.reconnect_attempts > 0:
            delay = min(2 ** (self.reconnect_attempts - 1) * 0.5, 30)  # Exponential backoff, max 30s
            logger.info(f"Waiting {delay:.2f} seconds before reconnection attempt {self.reconnect_attempts}...")
            await asyncio.sleep(delay)

        try:
            logger.info("Connecting to OANDA WebSocket...")
            
            # Create WebSocket connection with detailed error handling
            if not await self._create_websocket_connection():
                self.reconnect_attempts += 1
                logger.error(f"Failed to create WebSocket connection (attempt {self.reconnect_attempts})")
                return False
                
            # Start listening for messages
            self.active = True
            self.reconnect_attempts = 0  # Reset reconnect attempts on success
            
            # Start the heartbeat task
            self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
            
            # Start listening for messages
            asyncio.create_task(self._listen())
            
            logger.info("Successfully connected to OANDA WebSocket")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to WebSocket: {str(e)}", exc_info=True)
            self.reconnect_attempts += 1
            return False

    async def _verify_account_access(self):
        """Verify account access and handle rate limiting"""
        max_attempts = 3
        
        # Create a new session for this verification if one doesn't exist
        session = self.session or aiohttp.ClientSession()
        
        try:
            for attempt in range(max_attempts):
                try:
                    logger.info(f"Verifying OANDA account access (attempt {attempt + 1}/{max_attempts})...")
                    # Use the configured OANDA API host from settings
                    account_url = f"https://{settings.OANDA_API_HOST}/v3/accounts/{settings.OANDA_ACCOUNT_ID}"
                    
                    # Log the request
                    logger.debug(f"Making GET request to {account_url}")
                    logger.debug(f"Headers: {self.headers}")
                    
                    # Make the request directly without using _debug_http_request
                    async with session.get(account_url, headers=self.headers) as resp:
                        response_text = await resp.text()
                        logger.debug(f"Response status: {resp.status}")
                        logger.debug(f"Response headers: {dict(resp.headers)}")
                        
                        if resp.status == 200:
                            logger.info("Successfully verified OANDA account access")
                            return True
                        elif resp.status == 429:  # Rate limited
                            delay = await self._get_rate_limit_delay(dict(resp.headers))
                            logger.warning(f"Rate limited. Waiting {delay:.2f} seconds before retry...")
                            await asyncio.sleep(delay)
                        else:
                            logger.error(f"Failed to verify account access: {resp.status} - {response_text}")
                            if attempt == max_attempts - 1:  # Last attempt
                                return False
                            await asyncio.sleep(1)  # Short delay before retry
                
                except Exception as e:
                    logger.error(f"Error verifying account access: {str(e)}")
                    if attempt == max_attempts - 1:  # Last attempt
                        return False
                    await asyncio.sleep(1)  # Short delay before retry
            
            return False
            
        finally:
            # Only close the session if we created it here
            if self.session is None and not session.closed:
                await session.close()

    async def _create_websocket_connection(self) -> bool:
        """Helper method to create WebSocket connection with detailed error handling"""
        try:
            # Build the full URL with query parameters
            full_url = f"{self.ws_url}?instruments={self.instruments}&snapshot=true"
            logger.info(f"Attempting WebSocket connection to: {full_url}")
            
            # Create the WebSocket connection with custom headers
            self.ws = await websockets.connect(
                full_url,
                extra_headers={
                    "Authorization": self.headers["Authorization"]
                },
                ping_interval=30,
                ping_timeout=30,
                close_timeout=30,
                max_size=10 * 1024 * 1024,  # 10MB max message size
                compression="deflate"
            )
            
            logger.info("WebSocket connection established successfully")
            return True
            
        except InvalidStatusCode as e:
            logger.error(f"WebSocket connection failed with status code {e.status_code}")
            logger.error(f"Response headers: {e.response_headers}")
            if e.status_code == 401:
                logger.error("Authentication failed. Please check your OANDA API key and account ID.")
            elif e.status_code == 403:
                logger.error("Forbidden. Check if your account has the necessary permissions.")
            elif e.status_code == 404:
                logger.error("Endpoint not found. Please verify the WebSocket URL.")
            return False
            
        except ConnectionRefusedError as e:
            logger.error(f"Connection refused: {str(e)}")
            return False
            
        except asyncio.TimeoutError:
            logger.error("Connection timed out. The server took too long to respond.")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error during WebSocket connection: {str(e)}", exc_info=True)
            return False
            
    async def _handle_websocket_messages(self):
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._process_message(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse WebSocket message: {e}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self.ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.warning("WebSocket connection closed")
                    break
                    
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for WebSocket message")
            if hasattr(self.ws, '_response'):
                logger.error(f"WebSocket response status: {self.ws._response.status}")
                logger.error(f"WebSocket response headers: {dict(self.ws._response.headers)}")
            return False
            
        except aiohttp.ClientResponseError as e:
            if e.status == 429:  # Rate limited
                delay = await self._get_rate_limit_delay(dict(e.headers) if hasattr(e, 'headers') else {})
                logger.warning(f"Rate limited. Waiting {delay:.2f} seconds before retry...")
                await asyncio.sleep(delay)
                return await self.connect()  # Retry the connection
            logger.error(f"HTTP error connecting to WebSocket: {str(e)}")
            raise
            
        except Exception as e:
            logger.error(f"Error connecting to WebSocket: {str(e)}")
            # Don't raise, let the reconnection logic handle it
            return False
            
        # Start heartbeat task if connection was successful
        if self.active:
            self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
            # Start listening for messages
            asyncio.create_task(self._listen())
            return True
            
        # If we get here, connection failed
        logger.error("Failed to connect to OANDA WebSocket")
    async def disconnect(self):
        """Close the WebSocket connection."""
        self._stop_event.set()
        self.active = False
        
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
            self.heartbeat_task = None
        
        if self.ws and not self.ws.closed:
            await self.ws.close()
            logger.info("WebSocket connection closed")
        
        self.ws = None
            
        logger.info("Disconnected from OANDA WebSocket")
    
    async def _reconnect(self, delay: int = 5):
        """Attempt to reconnect with exponential backoff."""
        if self.active:
            return
            
        attempts = 0
        max_attempts = 5
        
        while attempts < max_attempts:
            try:
                logger.info(f"Attempting to reconnect (attempt {attempts + 1}/{max_attempts})...")
                await self.connect()
                if self.active:
                    # Resubscribe to instruments
                    if self.subscribed_instruments:
                        await self.subscribe_to_instruments(list(self.subscribed_instruments))
                    return
            except Exception as e:
                logger.error(f"Reconnection attempt {attempts + 1} failed: {str(e)}")
                
            attempts += 1
            delay = min(delay * 2, 60)  # Exponential backoff, max 60 seconds
            await asyncio.sleep(delay)
            
        logger.error("Max reconnection attempts reached. Giving up.")
    
    async def _send_heartbeat(self):
        """Send periodic heartbeats to keep the connection alive."""
        try:
            while self.active and self.ws and not self.ws.closed:
                await asyncio.sleep(20)  # Send heartbeat every 20 seconds
                if self.active and self.ws and not self.ws.closed:
                    try:
                        await self.ws.ping()
                    except Exception as e:
                        logger.error(f"Error sending heartbeat: {str(e)}")
                        await self._reconnect()
                        break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Heartbeat task error: {str(e)}")
    
    async def _listen(self):
        """Listen for incoming messages from OANDA WebSocket."""
        if not self.ws or self.ws.closed:
            logger.error("WebSocket connection is not active")
            return

        logger.info("Starting to listen for WebSocket messages...")
        
        try:
            while self.active and not self._stop_event.is_set():
                try:
                    message = await self.ws.recv()
                    try:
                        data = json.loads(message)
                        await self._process_message(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode WebSocket message: {message}")
                    except Exception as e:
                        logger.error(f"Error processing WebSocket message: {str(e)}", exc_info=True)
                        
                except ConnectionClosed as e:
                    logger.error(f"WebSocket connection closed: {e}")
                    break
                except asyncio.CancelledError:
                    logger.info("WebSocket listener task was cancelled")
                    break
                        
                except asyncio.TimeoutError:
                    # No message received within timeout, check connection status
                    if self.ws.closed or not self.active:
                        logger.info("WebSocket connection closed during timeout check")
                        break
                    logger.debug("No message received, but connection still active")
                    
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {str(e)}", exc_info=True)
                    if isinstance(e, (aiohttp.ClientError, ConnectionError)):
                        break  # Break the loop on connection errors
                    
        except asyncio.CancelledError:
            logger.info("WebSocket listener task was cancelled")
            raise
            
        except Exception as e:
            logger.error(f"Unexpected error in WebSocket listener: {str(e)}", exc_info=True)
            
        finally:
            # If we get here, the WebSocket connection was closed or errored
            self.active = False
            logger.warning("WebSocket connection lost, attempting to reconnect...")
            try:
                if self.ws and not self.ws.closed:
                    await self.ws.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {str(e)}")
                
            # Only attempt to reconnect if we didn't explicitly close the connection
            if not self.ws.closed:
                await self._reconnect()

    async def _process_message(self, data: Dict[str, Any]):
        """Process incoming WebSocket message from OANDA."""
        try:
            if 'type' not in data:
                return
                
            if data['type'] == 'PRICE':
                await self._handle_price_update(data)
            elif data['type'] == 'HEARTBEAT':
                logger.debug("Received heartbeat from OANDA")
                
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
    
    async def _handle_price_update(self, data: Dict[str, Any]):
        """Handle price update message from OANDA."""
        try:
            instrument = data.get('instrument')
            if not instrument:
                return
                
            # Get bid/ask prices
            bids = data.get('bids', [])
            asks = data.get('asks', [])
            
            if not bids or not asks:
                return
                
            # Use the first price in the book
            bid = float(bids[0].get('price', 0))
            ask = float(asks[0].get('price', 0))
            
            if bid <= 0 or ask <= 0:
                return
                
            # Calculate mid price
            mid_price = (bid + ask) / 2
            
            # Create update message
            update = {
                'type': 'price_update',
                'instrument': instrument,
                'timestamp': data.get('time', datetime.utcnow().isoformat()),
                'bid': bid,
                'ask': ask,
                'mid': mid_price,
                'spread': ask - bid
            }
            
            # Broadcast to WebSocket clients
            await manager.broadcast_to_subscribers(
                instrument=instrument,
                timeframe='S5',  # Default to 5-second candles for real-time updates
                data=update
            )
            
            # Log the update
            logger.debug(f"Price update for {instrument}: {mid_price}")
            
        except Exception as e:
            logger.error(f"Error handling price update: {str(e)}")
    
    async def subscribe_to_instruments(self, instruments: List[str]):
        """Subscribe to price updates for the given instruments."""
        if not self.active or not self.ws or self.ws.closed:
            logger.warning("Cannot subscribe: WebSocket not connected")
            return False
            
        try:
            # Filter out already subscribed instruments
            new_instruments = [i for i in instruments if i not in self.subscribed_instruments]
            
            if not new_instruments:
                return True
                
            # Add to our tracking set
            self.subscribed_instruments.update(new_instruments)
            
            # Send subscription message to OANDA
            subscribe_msg = {
                "type": "SUBSCRIBE",
                "instruments": new_instruments,
                "streaming_type": "PRICE"
            }
            
            await self.ws.send_json(subscribe_msg)
            logger.info(f"Subscribed to instruments: {', '.join(new_instruments)}")
            return True
            
        except Exception as e:
            logger.error(f"Error subscribing to instruments: {str(e)}")
            return False
    
    async def unsubscribe_from_instrument(self, instrument: str):
        """Unsubscribe from price updates for the given instrument."""
        if not self.active or not self.ws or self.ws.closed:
            return False
            
        try:
            if instrument not in self.subscribed_instruments:
                return True
                
            # Remove from our tracking set
            self.subscribed_instruments.discard(instrument)
            
            # Send unsubscribe message to OANDA
            unsubscribe_msg = {
                "type": "UNSUBSCRIBE",
                "instruments": [instrument],
                "streaming_type": "PRICE"
            }
            
            await self.ws.send_json(unsubscribe_msg)
            logger.info(f"Unsubscribed from instrument: {instrument}")
            return True
            
        except Exception as e:
            logger.error(f"Error unsubscribing from instrument {instrument}: {str(e)}")
            return False

# Global instance
market_data_stream = OandaMarketDataStream()

# Initialize the market data stream when the module is imported
async def init_market_data_stream():
    """Initialize the market data stream."""
    if settings.WEBSOCKET_ENABLED:
        await market_data_stream.connect()

# Start the market data stream when the application starts
async def start_market_data_stream():
    """Start the market data stream."""
    if settings.WEBSOCKET_ENABLED and not market_data_stream.active:
        await market_data_stream.connect()

# Stop the market data stream when the application shuts down
async def stop_market_data_stream():
    """Stop the market data stream."""
    if market_data_stream.active:
        await market_data_stream.disconnect()
