from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import logging
import json

from ..core.websocket import manager
from ..services.market_data_stream import market_data_stream
from ..core.security import get_current_user_ws

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/market-data")
async def market_data_websocket(
    websocket: WebSocket, token: str | None = None, instruments: str | None = None
):
    """
    WebSocket endpoint for real-time market data streaming.

    Query Parameters:
    - token: JWT token for authentication
    - instruments: Comma-separated list of instruments to subscribe to initially (e.g., "EUR_USD,GBP_USD")
    """
    # Authenticate user
    user = await get_current_user_ws(token)
    if not user:
        await websocket.close(code=4001)
        return

    # Accept the connection
    await manager.connect(websocket, "market-data")
    logger.info(f"New WebSocket connection from user {user.email}")

    # Initial subscriptions from query params
    initial_instruments = []
    if instruments:
        initial_instruments = [i.strip() for i in instruments.split(",") if i.strip()]

        # Subscribe to initial instruments
        for instrument in initial_instruments:
            await manager.subscribe(
                websocket, instrument, "S5"
            )  # Default to 5s candles

    try:
        # Send connection established message
        await websocket.send_json(
            {
                "type": "connection_established",
                "message": "Connected to market data feed",
                "user_id": str(user.id),
                "initial_instruments": initial_instruments,
            }
        )

        # Start the market data stream if not already running
        if not market_data_stream.active:
            await market_data_stream.connect()

            # Subscribe to initial instruments
            if initial_instruments:
                await market_data_stream.subscribe_to_instruments(initial_instruments)

        # Main message loop
        while True:
            try:
                data = await websocket.receive_text()
                await handle_websocket_message(websocket, data, user)

            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for user {user.email}")
                break

            except json.JSONDecodeError:
                await send_error(websocket, "Invalid JSON format")

            except Exception as e:
                logger.error(f"WebSocket error for user {user.email}: {str(e)}")
                await send_error(websocket, "Internal server error")
                break

    except Exception as e:
        logger.error(f"WebSocket connection error: {str(e)}")
    finally:
        # Clean up on disconnect
        manager.disconnect(websocket)
        logger.info(f"WebSocket connection closed for user {user.email}")


async def handle_websocket_message(websocket: WebSocket, message: str, user: any):
    """Handle incoming WebSocket messages."""
    try:
        data = json.loads(message)
        command = data.get("command")

        if command == "subscribe":
            await handle_subscribe(websocket, data, user)

        elif command == "unsubscribe":
            await handle_unsubscribe(websocket, data, user)

        elif command == "list_subscriptions":
            await handle_list_subscriptions(websocket, user)

        else:
            await send_error(websocket, f"Unknown command: {command}")

    except json.JSONDecodeError:
        await send_error(websocket, "Invalid JSON format")
    except Exception as e:
        logger.error(f"Error handling WebSocket message: {str(e)}")
        await send_error(websocket, "Internal server error")


async def handle_subscribe(websocket: WebSocket, data: dict, user: any):
    """Handle subscribe command."""
    instrument = data.get("instrument")
    timeframe = data.get("timeframe", "S5")  # Default to 5-second candles

    if not instrument:
        await send_error(websocket, "Missing instrument parameter")
        return

    # Add subscription
    await manager.subscribe(websocket, instrument, timeframe)

    # Subscribe to OANDA stream if needed
    if market_data_stream.active:
        await market_data_stream.subscribe_to_instruments([instrument])

    # Send confirmation
    await websocket.send_json(
        {
            "type": "subscription_update",
            "status": "subscribed",
            "instrument": instrument,
            "timeframe": timeframe,
        }
    )

    logger.info(f"User {user.email} subscribed to {instrument} {timeframe}")


async def handle_unsubscribe(websocket: WebSocket, data: dict, user: any):
    """Handle unsubscribe command."""
    instrument = data.get("instrument")
    timeframe = data.get("timeframe", "S5")

    if not instrument:
        await send_error(websocket, "Missing instrument parameter")
        return

    # Remove subscription
    await manager.unsubscribe(websocket, instrument, timeframe)

    # Check if we can unsubscribe from OANDA stream
    # (only if no other clients are subscribed to this instrument)
    if market_data_stream.active:
        # Check if any other clients are subscribed to this instrument
        other_subscribers = False
        for client_subs in manager.subscriptions.values():
            if client_subs != websocket and (instrument, timeframe) in client_subs:
                other_subscribers = True
                break

        if not other_subscribers:
            await market_data_stream.unsubscribe_from_instrument(instrument)

    # Send confirmation
    await websocket.send_json(
        {
            "type": "subscription_update",
            "status": "unsubscribed",
            "instrument": instrument,
            "timeframe": timeframe,
        }
    )

    logger.info(f"User {user.email} unsubscribed from {instrument} {timeframe}")


async def handle_list_subscriptions(websocket: WebSocket, user: any):
    """Handle list_subscriptions command."""
    if websocket in manager.subscriptions:
        subscriptions = [
            {"instrument": instr, "timeframe": tf}
            for instr, tf in manager.subscriptions[websocket]
        ]
    else:
        subscriptions = []

    await websocket.send_json(
        {"type": "subscription_list", "subscriptions": subscriptions}
    )


async def send_error(websocket: WebSocket, message: str):
    """Send an error message to the client."""
    await websocket.send_json(
        {
            "type": "error",
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
