from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime


class WebSocketMessage(BaseModel):
    """Base WebSocket message schema."""

    type: str
    message: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WebSocketCommand(BaseModel):
    """Base command schema for WebSocket messages."""

    command: str


class SubscribeCommand(WebSocketCommand):
    """Subscribe to market data updates."""

    command: Literal["subscribe"] = "subscribe"
    instrument: str
    timeframe: str


class UnsubscribeCommand(WebSocketCommand):
    """Unsubscribe from market data updates."""

    command: Literal["unsubscribe"] = "unsubscribe"
    instrument: str
    timeframe: str


class WebSocketSubscription(BaseModel):
    """Active subscription information."""

    instrument: str
    timeframe: str


class CandleUpdateMessage(WebSocketMessage):
    """Candle update message schema."""

    type: Literal["candle_update"] = "candle_update"
    instrument: str
    timeframe: str
    data: dict[str, Any]


class SubscriptionUpdateMessage(WebSocketMessage):
    """Subscription update message schema."""

    type: Literal["subscription_update"] = "subscription_update"
    status: Literal["subscribed", "unsubscribed"]
    instrument: str
    timeframe: str


class ErrorMessage(WebSocketMessage):
    """Error message schema."""

    type: Literal["error"] = "error"
    error: str | None = None


class ConnectionEstablishedMessage(WebSocketMessage):
    """Connection established message schema."""

    type: Literal["connection_established"] = "connection_established"
    user_id: str
