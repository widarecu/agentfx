"""Unit tests for the forex market calendar module."""

import pytest
from datetime import datetime, date, timezone, timedelta

from src.agentfx.api.workers.market_calendar import (
    load_calendar,
    reset_calendar,
    is_holiday,
    is_early_close,
    is_market_closure,
    _spans_weekend,
    _is_low_liquidity_period,
)


@pytest.fixture(autouse=True)
def reset_calendar_cache():
    """Reset calendar cache before each test."""
    reset_calendar()
    yield
    reset_calendar()


@pytest.mark.unit
class TestLoadCalendar:
    """Test cases for calendar loading functionality."""

    def test_load_calendar_returns_dict(self):
        """Test that load_calendar returns a dictionary."""
        cal = load_calendar()
        assert isinstance(cal, dict)

    def test_calendar_has_required_sections(self):
        """Test that calendar has all required sections."""
        cal = load_calendar()
        assert "weekly" in cal
        assert "holidays" in cal
        assert "early_closes" in cal
        assert "low_liquidity_hours_utc" in cal

    def test_calendar_has_holidays_for_years(self):
        """Test that calendar has holiday data for expected years."""
        cal = load_calendar()
        holidays = cal["holidays"]

        # Should have data for 2020-2026
        for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
            assert year in holidays, f"Missing holidays for year {year}"
            assert (
                len(holidays[year]) >= 3
            ), f"Year {year} should have at least 3 holidays"

    def test_calendar_caches_on_subsequent_calls(self):
        """Test that calendar is cached after first load."""
        cal1 = load_calendar()
        cal2 = load_calendar()
        assert cal1 is cal2  # Same object reference

    def test_reset_calendar_clears_cache(self):
        """Test that reset_calendar clears the cache."""
        cal1 = load_calendar()
        reset_calendar()
        cal2 = load_calendar()
        # After reset, should reload (same content but different object)
        assert cal1 is not cal2


@pytest.mark.unit
class TestIsHoliday:
    """Test cases for is_holiday function."""

    # Christmas tests (varies by year for observed dates)
    def test_christmas_2024_is_holiday(self):
        """Test Christmas 2024 (Dec 25) is a holiday."""
        assert is_holiday(date(2024, 12, 25)) is True

    def test_christmas_2021_observed_friday(self):
        """Test Christmas 2021 observed on Dec 24 (Saturday Dec 25)."""
        assert is_holiday(date(2021, 12, 24)) is True
        assert is_holiday(date(2021, 12, 25)) is False  # Saturday, not observed

    def test_christmas_2022_observed_monday(self):
        """Test Christmas 2022 observed on Dec 26 (Sunday Dec 25)."""
        assert is_holiday(date(2022, 12, 26)) is True
        assert is_holiday(date(2022, 12, 25)) is False  # Sunday, not observed

    # New Year tests
    def test_new_year_2024_is_holiday(self):
        """Test New Year's Day 2024 is a holiday."""
        assert is_holiday(date(2024, 1, 1)) is True

    def test_new_year_2022_observed_monday(self):
        """Test New Year 2022 observed on Jan 3 (Saturday Jan 1)."""
        assert is_holiday(date(2022, 1, 3)) is True

    def test_new_year_2023_observed_monday(self):
        """Test New Year 2023 observed on Jan 2 (Sunday Jan 1)."""
        assert is_holiday(date(2023, 1, 2)) is True

    # Good Friday tests
    def test_good_friday_2024_is_holiday(self):
        """Test Good Friday 2024 (Mar 29) is a holiday."""
        assert is_holiday(date(2024, 3, 29)) is True

    def test_good_friday_2025_is_holiday(self):
        """Test Good Friday 2025 (Apr 18) is a holiday."""
        assert is_holiday(date(2025, 4, 18)) is True

    def test_good_friday_2026_is_holiday(self):
        """Test Good Friday 2026 (Apr 3) is a holiday."""
        assert is_holiday(date(2026, 4, 3)) is True

    # Non-holiday tests
    def test_regular_weekday_not_holiday(self):
        """Test that a regular weekday is not a holiday."""
        assert is_holiday(date(2024, 6, 15)) is False  # Random Wednesday

    def test_boxing_day_2024_not_holiday(self):
        """Test that Boxing Day 2024 is NOT a forex holiday."""
        assert is_holiday(date(2024, 12, 26)) is False


@pytest.mark.unit
class TestIsEarlyClose:
    """Test cases for is_early_close function."""

    def test_christmas_eve_2024_is_early_close(self):
        """Test Christmas Eve 2024 is an early close day."""
        assert is_early_close(date(2024, 12, 24)) is True

    def test_new_years_eve_2024_is_early_close(self):
        """Test New Year's Eve 2024 is an early close day."""
        assert is_early_close(date(2024, 12, 31)) is True

    def test_day_before_good_friday_2025_is_early_close(self):
        """Test day before Good Friday 2025 is an early close day."""
        assert is_early_close(date(2025, 4, 17)) is True

    def test_regular_day_not_early_close(self):
        """Test that a regular day is not an early close."""
        assert is_early_close(date(2024, 6, 15)) is False


@pytest.mark.unit
class TestSpansWeekend:
    """Test cases for _spans_weekend function."""

    def test_friday_to_sunday_is_weekend(self):
        """Test Friday 5PM to Sunday 5PM gap is detected as weekend."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=timezone.utc)  # Friday
        sunday = datetime(2024, 1, 14, 22, 0, tzinfo=timezone.utc)  # Sunday
        assert _spans_weekend(friday, sunday) is True

    def test_friday_to_monday_is_weekend(self):
        """Test Friday to Monday gap is detected as weekend."""
        friday = datetime(2024, 1, 12, 21, 0, tzinfo=timezone.utc)  # Friday
        monday = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)  # Monday
        assert _spans_weekend(friday, monday) is True

    def test_friday_to_tuesday_is_weekend(self):
        """Test Friday to Tuesday gap (72+ hours) is detected as weekend."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=timezone.utc)  # Friday
        tuesday = datetime(2024, 1, 16, 2, 0, tzinfo=timezone.utc)  # Tuesday
        # ~76 hours, ends on Tuesday (weekday 1)
        assert _spans_weekend(friday, tuesday) is True

    def test_saturday_to_tuesday_is_weekend(self):
        """Test Saturday to Tuesday gap is detected as weekend."""
        # This was the bug - Saturday (5) to Tuesday (1) wasn't caught
        saturday = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)  # Saturday
        tuesday = datetime(2026, 1, 13, 2, 0, tzinfo=timezone.utc)  # Tuesday
        assert _spans_weekend(saturday, tuesday) is True

    def test_daily_candle_weekend_gap(self):
        """Test daily candle weekend gap (~72 hours) is detected."""
        friday = datetime(2024, 1, 12, 21, 0, tzinfo=timezone.utc)  # Friday
        monday = datetime(2024, 1, 15, 21, 0, tzinfo=timezone.utc)  # Monday
        assert _spans_weekend(friday, monday) is True

    def test_weekday_gap_not_weekend(self):
        """Test that a weekday gap is not detected as weekend."""
        monday = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)  # Monday
        wednesday = datetime(2024, 1, 17, 10, 0, tzinfo=timezone.utc)  # Wednesday
        assert _spans_weekend(monday, wednesday) is False

    def test_short_gap_not_weekend(self):
        """Test that gaps shorter than 44 hours are not weekends."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=timezone.utc)
        saturday = datetime(2024, 1, 13, 22, 0, tzinfo=timezone.utc)  # 24 hours
        assert _spans_weekend(friday, saturday) is False

    def test_handles_naive_datetime(self):
        """Test that naive datetimes are handled correctly."""
        friday = datetime(2024, 1, 12, 22, 0)  # Naive datetime
        sunday = datetime(2024, 1, 14, 22, 0)  # Naive datetime
        assert _spans_weekend(friday, sunday) is True

    def test_dst_transition_weekend(self):
        """Test weekend gap during DST transition."""
        friday = datetime(2024, 3, 8, 22, 0, tzinfo=timezone.utc)  # Friday
        sunday = datetime(2024, 3, 10, 21, 0, tzinfo=timezone.utc)  # Sunday (47 hours)
        assert _spans_weekend(friday, sunday) is True

    def test_timezone_edge_case(self):
        """Test weekend with +11 timezone (Friday local = Thursday UTC)."""
        tz_plus11 = timezone(timedelta(hours=11))
        # Friday 8:00 +11:00 = Thursday 21:00 UTC
        friday_local = datetime(2021, 11, 5, 8, 0, tzinfo=tz_plus11)
        # Monday 9:00 +11:00 = Sunday 22:00 UTC
        monday_local = datetime(2021, 11, 8, 9, 0, tzinfo=tz_plus11)
        assert _spans_weekend(friday_local, monday_local) is True


@pytest.mark.unit
class TestIsLowLiquidityPeriod:
    """Test cases for _is_low_liquidity_period function."""

    def test_gap_during_overnight_hours(self):
        """Test that gap during 22:00-07:00 UTC is low-liquidity."""
        start = datetime(2024, 1, 15, 23, 0, tzinfo=timezone.utc)  # 11 PM UTC
        end = datetime(2024, 1, 16, 4, 0, tzinfo=timezone.utc)  # 4 AM UTC
        assert _is_low_liquidity_period(start, end) is True

    def test_gap_during_trading_hours_not_low_liquidity(self):
        """Test that gap during active trading hours is not low-liquidity."""
        start = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)  # 2 PM UTC
        end = datetime(2024, 1, 15, 17, 0, tzinfo=timezone.utc)  # 5 PM UTC
        assert _is_low_liquidity_period(start, end) is False

    def test_long_gap_not_low_liquidity(self):
        """Test that gaps longer than max hours are not low-liquidity."""
        # Gap > 12 hours should not be attributed to low liquidity
        start = datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 16, 14, 0, tzinfo=timezone.utc)  # 16 hours
        assert _is_low_liquidity_period(start, end) is False


@pytest.mark.unit
class TestIsMarketClosure:
    """Test cases for the main is_market_closure function."""

    def test_weekend_returns_weekend_reason(self):
        """Test weekend gap returns 'weekend' reason."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=timezone.utc)
        sunday = datetime(2024, 1, 14, 22, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(friday, sunday)
        assert is_closure is True
        assert reason == "weekend"

    def test_holiday_returns_holiday_reason(self):
        """Test holiday gap returns 'holiday:YYYY-MM-DD' reason."""
        dec_24 = datetime(2024, 12, 24, 17, 0, tzinfo=timezone.utc)
        dec_26 = datetime(2024, 12, 26, 9, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(dec_24, dec_26)
        assert is_closure is True
        assert reason == "holiday:2024-12-25"

    def test_early_close_returns_early_close_reason(self):
        """Test gap on early close day returns 'early_close' reason."""
        # Gap on Christmas Eve that doesn't contain Christmas
        dec_24_morning = datetime(2024, 12, 24, 10, 0, tzinfo=timezone.utc)
        dec_24_evening = datetime(2024, 12, 24, 20, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(dec_24_morning, dec_24_evening)
        assert is_closure is True
        assert "early_close" in reason

    def test_low_liquidity_returns_low_liquidity_reason(self):
        """Test overnight gap returns 'low_liquidity' reason."""
        start = datetime(2024, 1, 15, 23, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 16, 4, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(start, end)
        assert is_closure is True
        assert reason == "low_liquidity"

    def test_normal_gap_returns_false(self):
        """Test that normal trading gap returns (False, '')."""
        # Gap during active trading hours on a normal day
        start = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)  # Monday
        end = datetime(2024, 1, 15, 17, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(start, end)
        assert is_closure is False
        assert reason == ""

    def test_new_year_closure_detected(self):
        """Test that New Year closure is detected."""
        dec_31 = datetime(2024, 12, 31, 17, 0, tzinfo=timezone.utc)
        jan_2 = datetime(2025, 1, 2, 9, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(dec_31, jan_2)
        assert is_closure is True
        assert "holiday:2025-01-01" in reason

    def test_good_friday_closure_detected(self):
        """Test that Good Friday closure is detected."""
        thursday = datetime(2024, 3, 28, 17, 0, tzinfo=timezone.utc)
        monday = datetime(2024, 4, 1, 9, 0, tzinfo=timezone.utc)
        is_closure, reason = is_market_closure(thursday, monday)
        # Weekend and Good Friday
        assert is_closure is True


@pytest.mark.unit
class TestCompleteHolidayCalendar:
    """Test that all SIFMA holidays are correctly defined."""

    @pytest.mark.parametrize(
        "year,expected_holidays",
        [
            (2020, ["01-01", "04-10", "12-25"]),
            (2021, ["01-01", "04-02", "12-24"]),
            (2022, ["01-03", "04-15", "12-26"]),
            (2023, ["01-02", "04-07", "12-25"]),
            (2024, ["01-01", "03-29", "12-25"]),
            (2025, ["01-01", "04-18", "12-25"]),
            (2026, ["01-01", "04-03", "12-25"]),
        ],
    )
    def test_holidays_match_sifma_calendar(self, year, expected_holidays):
        """Test that holidays match SIFMA calendar for each year."""
        cal = load_calendar()
        actual_holidays = cal["holidays"].get(year, [])

        for holiday in expected_holidays:
            assert holiday in actual_holidays, (
                f"Holiday {holiday} missing for year {year}. "
                f"Actual: {actual_holidays}"
            )
