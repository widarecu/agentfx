import time
import logging
from typing import Dict, Any, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """Single metric value with timestamp."""

    value: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MetricStats:
    """Statistics for a metric over a time window."""

    count: int = 0
    sum: float = 0.0
    min: float = float("inf")
    max: float = float("-inf")
    avg: float = 0.0
    recent_values: deque = field(default_factory=lambda: deque(maxlen=100))


class MarketDataMetrics:
    """
    Metrics collection system for market data operations.

    Tracks performance, usage, and data quality metrics.
    """

    def __init__(self, max_history_hours: int = 24):
        self.max_history_hours = max_history_hours
        self.metrics: dict[str, MetricStats] = defaultdict(MetricStats)
        self.lock = Lock()

        # Performance metrics
        self.request_times: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.error_counts: dict[str, int] = defaultdict(int)
        self.success_counts: dict[str, int] = defaultdict(int)

        # Data quality metrics
        self.data_validation_errors: dict[str, int] = defaultdict(int)
        self.data_gaps: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

        # Usage metrics
        self.endpoint_usage: dict[str, int] = defaultdict(int)
        self.user_usage: dict[str, int] = defaultdict(int)

    def record_request_time(
        self, endpoint: str, duration_ms: float, user_id: str | None = None
    ):
        """Record API request duration."""
        with self.lock:
            self.request_times[endpoint].append(MetricValue(duration_ms))
            self.endpoint_usage[endpoint] += 1

            if user_id:
                self.user_usage[f"user:{user_id}"] += 1

    def record_error(self, endpoint: str, error_type: str = "general"):
        """Record an API error."""
        with self.lock:
            self.error_counts[f"{endpoint}:{error_type}"] += 1

    def record_success(self, endpoint: str):
        """Record a successful API request."""
        with self.lock:
            self.success_counts[endpoint] += 1

    def record_data_validation_error(self, instrument: str, error_type: str):
        """Record data validation issues."""
        with self.lock:
            key = f"{instrument}:{error_type}"
            self.data_validation_errors[key] += 1

    def record_data_gap(
        self, instrument: str, timeframe: str, gap_start: datetime, gap_end: datetime
    ):
        """Record a gap in market data."""
        with self.lock:
            key = f"{instrument}:{timeframe}"
            self.data_gaps[key].append(
                {
                    "start": gap_start,
                    "end": gap_end,
                    "duration_minutes": (gap_end - gap_start).total_seconds() / 60,
                }
            )

    def get_endpoint_stats(self, endpoint: str, hours: int = 1) -> dict[str, Any]:
        """Get performance statistics for an endpoint."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        with self.lock:
            recent_times = [
                mv.value
                for mv in self.request_times[endpoint]
                if mv.timestamp > cutoff_time
            ]

            if not recent_times:
                return {
                    "endpoint": endpoint,
                    "timeframe_hours": hours,
                    "request_count": 0,
                    "avg_response_time_ms": 0,
                    "min_response_time_ms": 0,
                    "max_response_time_ms": 0,
                    "success_rate": 0,
                    "error_rate": 0,
                }

            total_requests = self.success_counts[endpoint] + sum(
                count
                for key, count in self.error_counts.items()
                if key.startswith(f"{endpoint}:")
            )

            return {
                "endpoint": endpoint,
                "timeframe_hours": hours,
                "request_count": len(recent_times),
                "total_requests": total_requests,
                "avg_response_time_ms": sum(recent_times) / len(recent_times),
                "min_response_time_ms": min(recent_times),
                "max_response_time_ms": max(recent_times),
                "success_count": self.success_counts[endpoint],
                "error_count": sum(
                    count
                    for key, count in self.error_counts.items()
                    if key.startswith(f"{endpoint}:")
                ),
                "success_rate": self.success_counts[endpoint]
                / max(total_requests, 1)
                * 100,
                "error_rate": sum(
                    count
                    for key, count in self.error_counts.items()
                    if key.startswith(f"{endpoint}:")
                )
                / max(total_requests, 1)
                * 100,
            }

    def get_data_quality_stats(self, hours: int = 24) -> dict[str, Any]:
        """Get data quality statistics."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        with self.lock:
            total_validation_errors = sum(self.data_validation_errors.values())
            total_data_gaps = sum(len(gaps) for gaps in self.data_gaps.values())

            # Calculate gap statistics
            gap_durations = []
            for gaps in self.data_gaps.values():
                gap_durations.extend(gap["duration_minutes"] for gap in gaps)

            return {
                "timeframe_hours": hours,
                "total_validation_errors": total_validation_errors,
                "total_data_gaps": total_data_gaps,
                "avg_gap_duration_minutes": sum(gap_durations)
                / max(len(gap_durations), 1),
                "max_gap_duration_minutes": max(gap_durations) if gap_durations else 0,
                "validation_errors_by_type": dict(self.data_validation_errors),
                "data_gaps_by_instrument": {
                    key: len(gaps) for key, gaps in self.data_gaps.items()
                },
            }

    def get_usage_stats(self, hours: int = 1) -> dict[str, Any]:
        """Get usage statistics."""
        with self.lock:
            return {
                "timeframe_hours": hours,
                "endpoint_usage": dict(self.endpoint_usage),
                "total_requests": sum(self.endpoint_usage.values()),
                "top_users": sorted(
                    self.user_usage.items(), key=lambda x: x[1], reverse=True
                )[:10],
                "unique_users": len(self.user_usage),
            }

    def cleanup_old_metrics(self):
        """Remove metrics older than max_history_hours."""
        cutoff_time = datetime.utcnow() - timedelta(hours=self.max_history_hours)

        with self.lock:
            # Clean request times
            for endpoint, times in self.request_times.items():
                while times and times[0].timestamp < cutoff_time:
                    times.popleft()

    def get_health_summary(self) -> dict[str, Any]:
        """Get overall system health summary."""
        recent_stats = self.get_endpoint_stats("candles", hours=1)
        quality_stats = self.get_data_quality_stats(hours=24)

        # Determine health status
        health_score = 100
        issues = []

        if recent_stats["error_rate"] > 5:
            health_score -= 20
            issues.append(f"High error rate: {recent_stats['error_rate']:.1f}%")

        if recent_stats["avg_response_time_ms"] > 1000:
            health_score -= 15
            issues.append(
                f"Slow response times: {recent_stats['avg_response_time_ms']:.0f}ms"
            )

        if quality_stats["total_validation_errors"] > 100:
            health_score -= 10
            issues.append(
                f"High data validation errors: {quality_stats['total_validation_errors']}"
            )

        if quality_stats["total_data_gaps"] > 10:
            health_score -= 10
            issues.append(f"Multiple data gaps: {quality_stats['total_data_gaps']}")

        health_status = "healthy"
        if health_score < 70:
            health_status = "unhealthy"
        elif health_score < 85:
            health_status = "degraded"

        return {
            "status": health_status,
            "health_score": max(0, health_score),
            "issues": issues,
            "last_updated": datetime.utcnow().isoformat(),
            "performance": recent_stats,
            "data_quality": quality_stats,
        }


# Global metrics instance
market_metrics = MarketDataMetrics()


def timing_decorator(metric_name: str):
    """Decorator to measure execution time."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                market_metrics.record_success(metric_name)
                return result
            except Exception as e:
                market_metrics.record_error(metric_name, type(e).__name__)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                market_metrics.record_request_time(metric_name, duration_ms)

        return wrapper

    return decorator
