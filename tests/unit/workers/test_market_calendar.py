"""Unit tests for the forex market calendar module."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from src.agentfx.api.workers.market_calendar import (
    _is_low_liquidity_period,
    _spans_extended_closure,
    _spans_weekend,
    is_early_close,
    is_holiday,
    is_market_closure,
    load_calendar,
    reset_calendar,
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
        # low_liquidity_hours_utc is optional (can be disabled in config)

    def test_calendar_has_holidays_for_years(self):
        """Test that calendar has holiday data for expected years."""
        cal = load_calendar()
        holidays = cal["holidays"]

        # Should have data for 2020-2026
        for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
            assert year in holidays, f"Missing holidays for year {year}"
            assert len(holidays[year]) >= 3, (
                f"Year {year} should have at least 3 holidays"
            )

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
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)  # Friday
        sunday = datetime(2024, 1, 14, 22, 0, tzinfo=UTC)  # Sunday
        assert _spans_weekend(friday, sunday) is True

    def test_friday_to_monday_is_weekend(self):
        """Test Friday to Monday gap is detected as weekend."""
        friday = datetime(2024, 1, 12, 21, 0, tzinfo=UTC)  # Friday
        monday = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)  # Monday
        assert _spans_weekend(friday, monday) is True

    def test_friday_to_tuesday_is_weekend(self):
        """Test Friday to Tuesday gap (72+ hours) is detected as weekend."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)  # Friday
        tuesday = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)  # Tuesday 00:00 UTC
        # ~74 hours, ends on Tuesday (weekday 1) within reopen window
        assert _spans_weekend(friday, tuesday) is True

    def test_saturday_to_monday_is_weekend(self):
        """Test Saturday to Monday gap is detected as weekend."""
        saturday = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)  # Saturday
        monday = datetime(2026, 1, 12, 22, 0, tzinfo=UTC)  # Monday 22:00 UTC
        assert _spans_weekend(saturday, monday) is True

    def test_daily_candle_weekend_gap(self):
        """Test daily candle weekend gap (~72 hours) is detected."""
        friday = datetime(2024, 1, 12, 21, 0, tzinfo=UTC)  # Friday
        monday = datetime(2024, 1, 15, 21, 0, tzinfo=UTC)  # Monday
        assert _spans_weekend(friday, monday) is True

    def test_weekday_gap_not_weekend(self):
        """Test that a weekday gap is not detected as weekend."""
        monday = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)  # Monday
        wednesday = datetime(2024, 1, 17, 10, 0, tzinfo=UTC)  # Wednesday
        assert _spans_weekend(monday, wednesday) is False

    def test_short_gap_not_weekend(self):
        """Test that gaps shorter than 44 hours are not weekends."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)
        saturday = datetime(2024, 1, 13, 22, 0, tzinfo=UTC)  # 24 hours
        assert _spans_weekend(friday, saturday) is False

    def test_handles_naive_datetime(self):
        """Test that naive datetimes are handled correctly."""
        friday = datetime(2024, 1, 12, 22, 0)  # Naive datetime
        sunday = datetime(2024, 1, 14, 22, 0)  # Naive datetime
        assert _spans_weekend(friday, sunday) is True

    def test_dst_transition_weekend(self):
        """Test weekend gap during DST transition."""
        friday = datetime(2024, 3, 8, 22, 0, tzinfo=UTC)  # Friday
        sunday = datetime(2024, 3, 10, 21, 0, tzinfo=UTC)  # Sunday (47 hours)
        assert _spans_weekend(friday, sunday) is True

    def test_timezone_edge_case(self):
        """Test weekend with +11 timezone (Friday local = Thursday UTC)."""
        tz_plus11 = timezone(timedelta(hours=11))
        # Friday 8:00 +11:00 = Thursday 21:00 UTC
        friday_local = datetime(2021, 11, 5, 8, 0, tzinfo=tz_plus11)
        # Monday 9:00 +11:00 = Sunday 22:00 UTC
        monday_local = datetime(2021, 11, 8, 9, 0, tzinfo=tz_plus11)
        assert _spans_weekend(friday_local, monday_local) is True

    def test_weekend_rejected_when_gap_end_too_late(self):
        """Gap from Friday to Monday 04:00 UTC (54h) is NOT a weekend —
        it masks 6 hours of missing Monday trading data after the ~22:00 UTC reopen."""
        friday = datetime(2026, 2, 13, 22, 0, tzinfo=UTC)  # Friday
        monday = datetime(2026, 2, 16, 4, 0, tzinfo=UTC)  # Monday 04:00 UTC
        # 54 hours, within 44-80 range, but end hour 4 is outside 19-01 window
        assert _spans_weekend(friday, monday) is False

    def test_weekend_accepted_at_reopen_boundary(self):
        """Gap ending at Monday 01:00 UTC is still accepted (market may open late)."""
        friday = datetime(2026, 2, 13, 22, 0, tzinfo=UTC)  # Friday
        monday = datetime(2026, 2, 16, 1, 0, tzinfo=UTC)  # Monday 01:00 UTC
        assert _spans_weekend(friday, monday) is True


@pytest.mark.unit
class TestIsLowLiquidityPeriod:
    """Test cases for _is_low_liquidity_period function."""

    def test_gap_during_overnight_hours(self):
        """Test overnight gap behavior depends on config.

        When low_liquidity_hours_utc is disabled in config, returns False.
        When enabled, gaps during 22:00-07:00 UTC are detected as low-liquidity.
        """
        cal = load_calendar()
        start = datetime(2024, 1, 15, 23, 0, tzinfo=UTC)  # 11 PM UTC
        end = datetime(2024, 1, 16, 4, 0, tzinfo=UTC)  # 4 AM UTC
        if cal.get("low_liquidity_hours_utc"):
            assert _is_low_liquidity_period(start, end) is True
        else:
            assert _is_low_liquidity_period(start, end) is False

    def test_gap_during_trading_hours_not_low_liquidity(self):
        """Test that gap during active trading hours is not low-liquidity."""
        start = datetime(2024, 1, 15, 14, 0, tzinfo=UTC)  # 2 PM UTC
        end = datetime(2024, 1, 15, 17, 0, tzinfo=UTC)  # 5 PM UTC
        assert _is_low_liquidity_period(start, end) is False

    def test_long_gap_not_low_liquidity(self):
        """Test that gaps longer than max hours are not low-liquidity."""
        # Gap > 12 hours should not be attributed to low liquidity
        start = datetime(2024, 1, 15, 22, 0, tzinfo=UTC)
        end = datetime(2024, 1, 16, 14, 0, tzinfo=UTC)  # 16 hours
        assert _is_low_liquidity_period(start, end) is False


@pytest.mark.unit
class TestIsMarketClosure:
    """Test cases for the main is_market_closure function."""

    def test_weekend_returns_weekend_reason(self):
        """Test weekend gap returns 'weekend' reason."""
        friday = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)
        sunday = datetime(2024, 1, 14, 22, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(friday, sunday)
        assert is_closure is True
        assert reason == "weekend"

    def test_holiday_returns_holiday_reason(self):
        """Test Christmas 2024 gap returns closure reason.

        The extended closure check (12-24 to 12-27) matches before the
        single-day holiday check since the gap spans the multi-day period.
        """
        dec_24 = datetime(2024, 12, 24, 17, 0, tzinfo=UTC)
        dec_26 = datetime(2024, 12, 26, 9, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(dec_24, dec_26)
        assert is_closure is True
        assert "extended_closure:2024-12-24/2024-12-27" == reason

    def test_early_close_returns_early_close_reason(self):
        """Test gap on early close day returns 'early_close' reason."""
        # Gap on Christmas Eve that doesn't contain Christmas
        dec_24_morning = datetime(2024, 12, 24, 10, 0, tzinfo=UTC)
        dec_24_evening = datetime(2024, 12, 24, 20, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(dec_24_morning, dec_24_evening)
        assert is_closure is True
        assert "early_close" in reason

    def test_low_liquidity_returns_low_liquidity_reason(self):
        """Test overnight gap behavior depends on low-liquidity config.

        When low_liquidity_hours_utc is disabled in config, the gap is not
        classified as a closure and should be healed.
        """
        cal = load_calendar()
        start = datetime(2024, 1, 15, 23, 0, tzinfo=UTC)
        end = datetime(2024, 1, 16, 4, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(start, end)
        if cal.get("low_liquidity_hours_utc"):
            assert is_closure is True
            assert reason == "low_liquidity"
        else:
            assert is_closure is False
            assert reason == ""

    def test_normal_gap_returns_false(self):
        """Test that normal trading gap returns (False, '')."""
        # Gap during active trading hours on a normal day
        start = datetime(2024, 1, 15, 14, 0, tzinfo=UTC)  # Monday
        end = datetime(2024, 1, 15, 17, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(start, end)
        assert is_closure is False
        assert reason == ""

    def test_new_year_closure_detected(self):
        """Test that New Year closure is detected.

        The extended closure check (12-31 to 01-02) matches before the
        single-day holiday check since the gap spans the multi-day period.
        """
        dec_31 = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
        jan_2 = datetime(2025, 1, 2, 9, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(dec_31, jan_2)
        assert is_closure is True
        assert "extended_closure:2024-12-31/2025-01-02" == reason

    def test_good_friday_plus_weekend_not_single_closure(self):
        """Test that a 96h gap spanning Good Friday + weekend is NOT a single closure.

        The gap from Thursday to Monday spans both Good Friday and the weekend.
        Neither the weekend check (>80h) nor the holiday boundary check
        (end=Apr 1 != Mar 29/30) matches. The healer should attempt to fill it;
        OANDA returns no data for closed days.
        """
        thursday = datetime(2024, 3, 28, 17, 0, tzinfo=UTC)
        monday = datetime(2024, 4, 1, 9, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(thursday, monday)
        assert is_closure is False
        assert reason == ""

    def test_multi_year_gap_not_classified_as_holiday(self):
        """Test that a 3-year gap containing holidays is NOT classified as a closure.

        Regression test: is_market_closure previously iterated all days in a gap
        and returned True if ANY day was a holiday, causing multi-year gaps
        (e.g. AUD_USD M10 from 2022-12-24 to 2026-02-05) to be skipped as
        'holiday:2022-12-26'.
        """
        gap_start = datetime(2022, 12, 24, 22, 0, tzinfo=UTC)
        gap_end = datetime(2026, 2, 5, 0, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(gap_start, gap_end)
        assert is_closure is False
        assert reason == ""

    def test_multi_month_gap_not_classified_as_holiday(self):
        """Test that a 2-month gap containing Christmas is NOT a closure."""
        gap_start = datetime(2024, 12, 20, 22, 0, tzinfo=UTC)
        gap_end = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(gap_start, gap_end)
        assert is_closure is False
        assert reason == ""

    def test_early_close_not_matched_for_long_gap(self):
        """Test that a 2-week gap containing early close days is NOT a closure."""
        gap_start = datetime(2024, 12, 23, 10, 0, tzinfo=UTC)
        gap_end = datetime(2025, 1, 10, 10, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(gap_start, gap_end)
        assert is_closure is False
        assert reason == ""

    def test_holiday_boundary_mismatch_not_closure(self):
        """Test that a gap starting far before a holiday is NOT a closure.

        Gap from Dec 20 to Dec 28 contains Christmas Dec 25, but
        gap_start (Dec 20) is not adjacent to the holiday (needs Dec 24 or 25).
        """
        gap_start = datetime(2024, 12, 20, 22, 0, tzinfo=UTC)
        gap_end = datetime(2024, 12, 28, 10, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(gap_start, gap_end)
        assert is_closure is False
        assert reason == ""

    def test_early_close_boundary_mismatch_not_closure(self):
        """Test that a gap starting the day before an early close is NOT a closure.

        Dec 24 is an early close day, but the gap starts on Dec 23 —
        the early close boundary check requires gap_start on the early close day itself.
        """
        gap_start = datetime(2024, 12, 23, 18, 0, tzinfo=UTC)
        gap_end = datetime(2024, 12, 25, 10, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(gap_start, gap_end)
        # This may match as holiday (Dec 25 is a holiday, start=Dec 23 is not adjacent)
        # Dec 25 holiday: prev_day=Dec 24, start=Dec 23 != Dec 24/25 => no match
        assert is_closure is False
        assert reason == ""

    def test_good_friday_standalone_closure_detected(self):
        """Test that a standalone Good Friday gap (not spanning weekend) is detected."""
        # Gap from Thursday evening to Saturday morning (Good Friday only)
        thursday = datetime(2024, 3, 28, 22, 0, tzinfo=UTC)
        saturday = datetime(2024, 3, 30, 2, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(thursday, saturday)
        assert is_closure is True
        assert reason == "holiday:2024-03-29"


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
                f"Holiday {holiday} missing for year {year}. Actual: {actual_holidays}"
            )


@pytest.mark.unit
class TestSpansExtendedClosure:
    """Test cases for _spans_extended_closure function."""

    def test_christmas_week_2024_detected(self):
        """Test that Christmas week 2024 gap is detected as extended closure."""
        start = datetime(2024, 12, 24, 20, 0, tzinfo=UTC)
        end = datetime(2024, 12, 27, 8, 0, tzinfo=UTC)
        is_closure, reason = _spans_extended_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason

    def test_new_year_week_2024_2025_detected(self):
        """Test that New Year week 2024-2025 gap is detected (year crossing)."""
        start = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
        end = datetime(2025, 1, 2, 8, 0, tzinfo=UTC)
        is_closure, reason = _spans_extended_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason

    def test_christmas_week_2023_detected(self):
        """Test that Christmas week 2023 gap is detected."""
        start = datetime(2023, 12, 22, 20, 0, tzinfo=UTC)
        end = datetime(2023, 12, 26, 8, 0, tzinfo=UTC)
        is_closure, reason = _spans_extended_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason

    def test_short_gap_not_extended_closure(self):
        """Test that a short gap (< 24 hours) is not matched."""
        start = datetime(2024, 12, 24, 15, 0, tzinfo=UTC)
        end = datetime(2024, 12, 25, 10, 0, tzinfo=UTC)
        is_closure, _ = _spans_extended_closure(start, end)
        assert is_closure is False

    def test_very_long_gap_not_extended_closure(self):
        """Test that a very long gap (> 240 hours) is not matched."""
        start = datetime(2024, 12, 15, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 10, 0, 0, tzinfo=UTC)
        is_closure, _ = _spans_extended_closure(start, end)
        assert is_closure is False

    def test_random_week_not_extended_closure(self):
        """Test that a random mid-year week gap is not matched."""
        start = datetime(2024, 6, 10, 20, 0, tzinfo=UTC)
        end = datetime(2024, 6, 14, 8, 0, tzinfo=UTC)
        is_closure, _ = _spans_extended_closure(start, end)
        assert is_closure is False

    def test_naive_datetimes_handled(self):
        """Test that naive datetimes are handled correctly."""
        start = datetime(2024, 12, 24, 20, 0)
        end = datetime(2024, 12, 27, 8, 0)
        is_closure, reason = _spans_extended_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason


@pytest.mark.unit
class TestExtendedClosureIntegration:
    """Test that extended closures work via the main is_market_closure entry point."""

    def test_christmas_week_via_is_market_closure(self):
        """Test Christmas week detected by main function."""
        start = datetime(2024, 12, 24, 20, 0, tzinfo=UTC)
        end = datetime(2024, 12, 27, 8, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason

    def test_new_year_crossing_via_is_market_closure(self):
        """Test New Year year-crossing gap detected by main function."""
        start = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
        end = datetime(2025, 1, 2, 8, 0, tzinfo=UTC)
        is_closure, reason = is_market_closure(start, end)
        assert is_closure is True
        assert "extended_closure" in reason


@pytest.mark.unit
class TestCalendarHasExtendedClosures:
    """Test that extended closure data is present in calendar."""

    def test_calendar_has_extended_closures_section(self):
        """Test that calendar has extended_closures for recent years."""
        cal = load_calendar()
        extended = cal.get("extended_closures", {})
        assert len(extended) > 0, "Calendar should have extended_closures section"

    def test_extended_closures_cover_recent_years(self):
        """Test that extended closures cover 2020-2026."""
        cal = load_calendar()
        extended = cal.get("extended_closures", {})
        for year in [2020, 2021, 2022, 2023, 2024, 2025, 2026]:
            assert year in extended, f"Missing extended closures for {year}"

    def test_extended_closures_have_start_and_end(self):
        """Test that each extended closure has start and end dates."""
        cal = load_calendar()
        extended = cal.get("extended_closures", {})
        for year, closures in extended.items():
            for closure in closures:
                assert "start" in closure, f"Missing 'start' in {year} closure"
                assert "end" in closure, f"Missing 'end' in {year} closure"
