import os
import time
import asyncio
from typing import Dict
from collections import defaultdict, deque
from fastapi import HTTPException, Request, status
from functools import wraps
import logging

logger = logging.getLogger(__name__)

# Check if rate limiting should be disabled (test environment)
RATE_LIMITING_ENABLED = os.environ.get("ENVIRONMENT", "development") != "test"


class RateLimiter:
    """
    Simple in-memory rate limiter for API endpoints.

    Uses sliding window algorithm to track requests per client.
    """

    def __init__(self):
        # Store client requests: {client_id: deque of timestamps}
        self.requests: dict[str, deque] = defaultdict(deque)
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    def _cleanup_old_requests(self, client_id: str, window_seconds: int):
        """Remove requests older than the time window."""
        now = time.time()
        while (
            self.requests[client_id]
            and now - self.requests[client_id][0] > window_seconds
        ):
            self.requests[client_id].popleft()

    async def is_allowed(self, client_id: str, limit: int, window_seconds: int) -> bool:
        """
        Check if client is allowed to make a request.

        Args:
            client_id: Unique identifier for the client
            limit: Maximum number of requests allowed
            window_seconds: Time window in seconds

        Returns:
            True if request is allowed, False otherwise
        """
        async with self._lock:
            now = time.time()

            # Clean old requests
            self._cleanup_old_requests(client_id, window_seconds)

            # Check if under limit
            if len(self.requests[client_id]) >= limit:
                return False

            # Add current request
            self.requests[client_id].append(now)
            return True

    def get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        # Check for forwarded headers first
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        return request.client.host if request.client else "unknown"


# Global rate limiter instance
rate_limiter = RateLimiter()


def rate_limit(
    requests_per_minute: int = 60, requests_per_hour: int = 1000, per_user: bool = False
):
    """
    Decorator to apply rate limiting to API endpoints.

    Args:
        requests_per_minute: Max requests per minute
        requests_per_hour: Max requests per hour
        per_user: If True, rate limit per user ID instead of IP
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Skip rate limiting in test environment
            if not RATE_LIMITING_ENABLED:
                return await func(*args, **kwargs)

            # Extract request from kwargs (FastAPI dependency injection)
            request = None
            for key, value in kwargs.items():
                if isinstance(value, Request):
                    request = value
                    break

            if not request:
                # If no request found, skip rate limiting
                return await func(*args, **kwargs)

            # Determine client identifier
            if per_user:
                # Try to get user from kwargs (FastAPI dependency injection)
                current_user = None
                for key, value in kwargs.items():
                    if hasattr(value, "id") and hasattr(
                        value, "email"
                    ):  # Likely a User object
                        current_user = value
                        break

                if current_user:
                    client_id = f"user:{current_user.id}"
                else:
                    # Fallback to IP if user not found
                    client_id = f"ip:{rate_limiter.get_client_ip(request)}"
            else:
                client_id = f"ip:{rate_limiter.get_client_ip(request)}"

            # Check minute limit
            if not await rate_limiter.is_allowed(client_id, requests_per_minute, 60):
                logger.warning(f"Rate limit exceeded for {client_id} (minute limit)")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please try again later.",
                    headers={"Retry-After": "60"},
                )

            # Check hour limit
            if not await rate_limiter.is_allowed(client_id, requests_per_hour, 3600):
                logger.warning(f"Rate limit exceeded for {client_id} (hour limit)")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please try again later.",
                    headers={"Retry-After": "3600"},
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# Predefined rate limits for different endpoint types
MARKET_DATA_LIMITS = {
    "candles": {
        "requests_per_minute": 120,
        "requests_per_hour": 2000,
    },  # Higher for data fetching
    "instruments": {
        "requests_per_minute": 30,
        "requests_per_hour": 500,
    },  # Lower for static data
    "status": {
        "requests_per_minute": 60,
        "requests_per_hour": 1000,
    },  # Medium for monitoring
    "default": {"requests_per_minute": 60, "requests_per_hour": 1000},
}


def market_data_rate_limit(endpoint_type: str = "default", per_user: bool = True):
    """
    Rate limiting specifically for market data endpoints.

    Args:
        endpoint_type: Type of market data endpoint
        per_user: Apply rate limits per authenticated user
    """
    limits = MARKET_DATA_LIMITS.get(endpoint_type, MARKET_DATA_LIMITS["default"])
    return rate_limit(
        requests_per_minute=limits["requests_per_minute"],
        requests_per_hour=limits["requests_per_hour"],
        per_user=per_user,
    )
