"""
Forex market calendar for closure detection.

Loads market closure configuration from YAML and provides functions to check
if a time period falls within a known market closure (weekend, holiday, or
low-liquidity period).

Configuration file: config/forex_calendar.yaml
Source: SIFMA Holiday Schedule (https://www.sifma.org/resources/guides-playbooks/holiday-schedule)
"""

import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Singleton calendar cache
_calendar: dict[str, Any] | None = None


def load_calendar(path: Path | None = None) -> dict[str, Any]:
    """
    Load forex calendar from YAML file.

    Uses singleton pattern - loads once and caches for performance.

    Args:
        path: Optional custom path to calendar YAML file.
              Defaults to config/forex_calendar.yaml at project root.

    Returns:
        Dictionary with calendar configuration.

    Raises:
        FileNotFoundError: If calendar file doesn't exist.
    """
    global _calendar

    if _calendar is not None:
        return _calendar

    if path is None:
        # Navigate from this file to project root/config/forex_calendar.yaml
        # This file is at: src/agentfx/api/workers/market_calendar.py
        # Config is at: config/forex_calendar.yaml
        this_dir = Path(__file__).parent
        path = this_dir.parent.parent.parent.parent / "config" / "forex_calendar.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Forex calendar not found at {path}")

    with open(path) as f:
        _calendar = yaml.safe_load(f)

    logger.debug(f"Loaded forex calendar from {path}")
    return _calendar


def reset_calendar() -> None:
    """Reset the calendar cache. Useful for testing."""
    global _calendar
    _calendar = None


def is_holiday(d: date) -> bool:
    """
    Check if a date is a forex market holiday (full closure).

    Args:
        d: Date to check.

    Returns:
        True if the date is a market holiday.
    """
    cal = load_calendar()
    holidays = cal.get("holidays", {})
    year_holidays = holidays.get(d.year, [])
    date_str = d.strftime("%m-%d")
    return date_str in year_holidays


def is_early_close(d: date) -> bool:
    """
    Check if a date is an early close day (reduced liquidity expected).

    Args:
        d: Date to check.

    Returns:
        True if the date is an early close day.
    """
    cal = load_calendar()
    early_closes = cal.get("early_closes", {})
    year_early_closes = early_closes.get(d.year, [])
    date_str = d.strftime("%m-%d")
    return date_str in year_early_closes


def _spans_weekend(gap_start: datetime, gap_end: datetime) -> bool:
    """
    Check if a gap spans the forex market weekend closure.

    Forex markets close Friday around 5 PM ET and reopen Sunday around 5 PM ET.
    This is approximately 48 hours for intraday candles, or 72 hours for daily.

    Args:
        gap_start: Start of the gap (last candle before gap).
        gap_end: End of the gap (first candle after gap).

    Returns:
        True if the gap spans a weekend closure.
    """
    # Ensure timezone awareness
    if gap_start.tzinfo is None:
        gap_start = gap_start.replace(tzinfo=timezone.utc)
    if gap_end.tzinfo is None:
        gap_end = gap_end.replace(tzinfo=timezone.utc)

    gap_hours = (gap_end - gap_start).total_seconds() / 3600

    # Weekend gaps are typically 44-80 hours (48 hours nominal, with tolerance)
    # Daily candles can have 72-hour gaps (Friday to Monday)
    if not (44 <= gap_hours <= 80):
        return False

    start_weekday = gap_start.weekday()
    end_weekday = gap_end.weekday()

    # Gap should start around Friday (4) or Saturday (5)
    # Include Thursday (3) for timezone edge cases (+11 hours Friday = Thursday UTC)
    # Gap should end around Sunday (6), Monday (0), or Tuesday (1)
    # Include Tuesday for gaps that end later due to timezone
    return start_weekday in (3, 4, 5) and end_weekday in (6, 0, 1)


def _is_low_liquidity_period(gap_start: datetime, gap_end: datetime) -> bool:
    """
    Check if a gap falls within known low-liquidity hours.

    OANDA data shows 10-14% of M1 bars missing during 22:00-07:00 UTC.
    This is expected behavior, not a bug.

    Args:
        gap_start: Start of the gap.
        gap_end: End of the gap.

    Returns:
        True if the gap falls within low-liquidity hours.
    """
    cal = load_calendar()
    low_liq = cal.get("low_liquidity_hours_utc", {})

    if not low_liq:
        return False

    start_hour = low_liq.get("start", 22)
    end_hour = low_liq.get("end", 7)
    max_gap_hours = cal.get("low_liquidity_max_gap_hours", 12)

    # Ensure timezone awareness (convert to UTC for comparison)
    if gap_start.tzinfo is None:
        gap_start = gap_start.replace(tzinfo=timezone.utc)
    if gap_end.tzinfo is None:
        gap_end = gap_end.replace(tzinfo=timezone.utc)

    gap_start_utc = gap_start.astimezone(timezone.utc)
    gap_end_utc = gap_end.astimezone(timezone.utc)

    gap_hours = (gap_end_utc - gap_start_utc).total_seconds() / 3600

    # Only consider short gaps (< max hours) as low-liquidity
    if gap_hours > max_gap_hours:
        return False

    # Check if the gap falls within low-liquidity window
    # Low-liquidity window spans midnight: 22:00 - 07:00 UTC
    start_hour_utc = gap_start_utc.hour
    end_hour_utc = gap_end_utc.hour

    # Check if gap start is in low-liquidity window
    if start_hour <= end_hour:
        # Window doesn't span midnight (unlikely for our case)
        start_in_window = start_hour <= start_hour_utc < end_hour
    else:
        # Window spans midnight (22:00 - 07:00)
        start_in_window = start_hour_utc >= start_hour or start_hour_utc < end_hour

    # Check if gap end is in low-liquidity window
    if start_hour <= end_hour:
        end_in_window = start_hour <= end_hour_utc < end_hour
    else:
        end_in_window = end_hour_utc >= start_hour or end_hour_utc < end_hour

    return start_in_window or end_in_window


def is_market_closure(gap_start: datetime, gap_end: datetime) -> tuple[bool, str]:
    """
    Check if a gap spans a known market closure.

    This is the main entry point for gap detection. Checks in order:
    1. Weekend closure (Friday PM to Sunday PM)
    2. Holiday closure (Christmas, New Year, Good Friday)
    3. Low-liquidity period (overnight hours with sparse data)

    Args:
        gap_start: Timestamp of the candle before the gap.
        gap_end: Timestamp of the candle after the gap.

    Returns:
        Tuple of (is_closure, reason):
        - (True, "weekend") - Gap spans weekend
        - (True, "holiday:2024-12-25") - Gap contains holiday
        - (True, "early_close:2024-12-24") - Gap contains early close day
        - (True, "low_liquidity") - Gap is during low-liquidity hours
        - (False, "") - Gap is not a known closure
    """
    # 1. Weekend check
    if _spans_weekend(gap_start, gap_end):
        return True, "weekend"

    # 2. Holiday check (iterate days in gap)
    current = gap_start.date()
    end_date = gap_end.date()
    while current <= end_date:
        if is_holiday(current):
            return True, f"holiday:{current.isoformat()}"
        current += timedelta(days=1)

    # 3. Early close check (reduced liquidity expected)
    current = gap_start.date()
    while current <= end_date:
        if is_early_close(current):
            return True, f"early_close:{current.isoformat()}"
        current += timedelta(days=1)

    # 4. Low-liquidity period check
    if _is_low_liquidity_period(gap_start, gap_end):
        return True, "low_liquidity"

    return False, ""
