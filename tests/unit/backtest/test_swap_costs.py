"""Unit tests for OANDA financing/swap cost calculation in the backtest engine.

These exercise the pure helpers (`_count_rollovers`, `compute_swap_returns`)
without touching the database. Integration with the actual backtest is
covered separately.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agentfx.services.backtest import (
    _count_rollovers,
    compute_swap_returns,
)


# -- _count_rollovers -------------------------------------------------------


def _ts(iso: str) -> pd.Timestamp:
    """Build a UTC pd.Timestamp from an ISO-8601 string."""
    return pd.Timestamp(iso, tz="UTC")


def test_count_rollovers_no_crossing_within_one_hour():
    # 18:00–19:00 UTC on a Monday — well before the 21:00 UTC rollover.
    assert (
        _count_rollovers(_ts("2026-04-13T18:00:00Z"), _ts("2026-04-13T19:00:00Z")) == 0
    )


def test_count_rollovers_monday_to_tuesday_one_charge():
    # Bar interval contains Mon 21:00 UTC. 2026-04-13 is a Monday.
    multiplier = _count_rollovers(
        _ts("2026-04-13T20:00:00Z"),  # Mon 20:00 UTC
        _ts("2026-04-13T22:00:00Z"),  # Mon 22:00 UTC
    )
    assert multiplier == 1


def test_count_rollovers_wednesday_single_charge():
    # 2026-04-15 is a Wednesday. Bar spans Wed 21:00 UTC.
    # OANDA AU does NOT triple-charge Wednesdays — verified against
    # the user's OANDA platform transaction history. Every weekday
    # rollover is 1×.
    multiplier = _count_rollovers(
        _ts("2026-04-15T20:00:00Z"),  # Wed 20:00 UTC
        _ts("2026-04-15T22:00:00Z"),  # Wed 22:00 UTC
    )
    assert multiplier == 1


def test_count_rollovers_friday_charges_one():
    # 2026-04-17 is a Friday. Friday rollover is 1×.
    multiplier = _count_rollovers(
        _ts("2026-04-17T20:00:00Z"),  # Fri 20:00 UTC
        _ts("2026-04-17T22:00:00Z"),  # Fri 22:00 UTC
    )
    assert multiplier == 1


def test_count_rollovers_saturday_skipped():
    # 2026-04-18 is a Saturday — skipped (corresponds to Sun morning AEST).
    multiplier = _count_rollovers(
        _ts("2026-04-18T20:00:00Z"),
        _ts("2026-04-18T22:00:00Z"),
    )
    assert multiplier == 0


def test_count_rollovers_sunday_skipped():
    # 2026-04-19 is a Sunday — skipped (corresponds to Mon morning AEST).
    multiplier = _count_rollovers(
        _ts("2026-04-19T20:00:00Z"),
        _ts("2026-04-19T22:00:00Z"),
    )
    assert multiplier == 0


def test_count_rollovers_full_week_sums_to_five():
    # Mon 20:00 UTC → following Mon 20:00 UTC = 7 calendar days.
    # Schedule: Mon 1, Tue 1, Wed 1, Thu 1, Fri 1, Sat 0, Sun 0 = 5
    multiplier = _count_rollovers(
        _ts("2026-04-13T20:00:00Z"),  # Mon 20:00 UTC
        _ts("2026-04-20T20:00:00Z"),  # next Mon 20:00 UTC
    )
    assert multiplier == 5


def test_count_rollovers_wednesday_to_thursday_one_charge():
    # Position open Wed 14:00 UTC to Thu 14:00 UTC — only Wed 21:00 UTC inside.
    # 1× under the AU schedule.
    multiplier = _count_rollovers(
        _ts("2026-04-15T14:00:00Z"),  # Wed 14:00 UTC
        _ts("2026-04-16T14:00:00Z"),  # Thu 14:00 UTC
    )
    assert multiplier == 1


# -- compute_swap_returns ---------------------------------------------------


def _df_hourly(start_iso: str, hours: int) -> pd.DataFrame:
    idx = pd.date_range(start=start_iso, periods=hours, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [1.10] * hours,
            "high": [1.10] * hours,
            "low": [1.10] * hours,
            "close": [1.10] * hours,
            "volume": [1] * hours,
        },
        index=idx,
    )


def test_compute_swap_returns_zero_when_rates_none():
    df = _df_hourly("2026-04-13T00:00:00Z", 48)
    signals = pd.Series(1.0, index=df.index)
    result = compute_swap_returns(df, signals, None, 0.02)
    assert (result == 0.0).all()


def test_compute_swap_returns_zero_when_position_flat():
    df = _df_hourly("2026-04-13T00:00:00Z", 48)
    signals = pd.Series(0.0, index=df.index)
    result = compute_swap_returns(df, signals, -0.02, 0.01)
    assert (result == 0.0).all()


def test_compute_swap_returns_long_one_night_uses_long_rate():
    # Mon 13 Apr 2026 — 24 hourly bars covering one rollover at Mon 21:00 UTC.
    df = _df_hourly("2026-04-13T00:00:00Z", 24)
    signals = pd.Series(1.0, index=df.index)  # long throughout
    long_rate = 0.0365  # 3.65%/year → 0.01% per night
    short_rate = -0.05
    result = compute_swap_returns(df, signals, long_rate, short_rate)
    expected = long_rate / 365.0
    # Exactly one bar in the 24-bar window contains Mon 21:00 UTC.
    nonzero = result[result != 0.0]
    assert len(nonzero) == 1
    assert nonzero.iloc[0] == pytest.approx(expected)


def test_compute_swap_returns_short_uses_short_rate():
    df = _df_hourly("2026-04-13T00:00:00Z", 24)
    signals = pd.Series(-1.0, index=df.index)  # short throughout
    long_rate = 0.0365
    short_rate = -0.0182  # client pays
    result = compute_swap_returns(df, signals, long_rate, short_rate)
    expected = short_rate / 365.0
    nonzero = result[result != 0.0]
    assert len(nonzero) == 1
    assert nonzero.iloc[0] == pytest.approx(expected)


def test_compute_swap_returns_wednesday_single_charge():
    # OANDA AU schedule: Wednesday rollover is 1×, NOT 3×. The historical
    # NY-based "triple Wed" model does not match AU transaction behavior
    # (user verified against multi-day open trade in the platform).
    df = _df_hourly("2026-04-15T00:00:00Z", 24)  # Wednesday
    signals = pd.Series(1.0, index=df.index)
    long_rate = 0.0365
    result = compute_swap_returns(df, signals, long_rate, -0.05)
    nonzero = result[result != 0.0]
    assert len(nonzero) == 1
    expected = long_rate / 365.0  # 1× — no Wednesday multiplier
    assert nonzero.iloc[0] == pytest.approx(expected)


def test_compute_swap_returns_no_rollover_within_short_intraday_window():
    # 4 hourly bars Mon 09:00–13:00 UTC. No 21:00 UTC rollover inside.
    df = _df_hourly("2026-04-13T09:00:00Z", 4)
    signals = pd.Series(1.0, index=df.index)
    result = compute_swap_returns(df, signals, 0.0365, -0.05)
    assert (result == 0.0).all()


def test_compute_swap_returns_sign_convention_matches_oanda():
    # Per OANDA: positive rate ⇒ position receives credit; negative ⇒ pays.
    # A long with negative long_rate should produce a NEGATIVE return.
    df = _df_hourly("2026-04-13T00:00:00Z", 24)
    signals = pd.Series(1.0, index=df.index)
    long_rate = -0.02
    result = compute_swap_returns(df, signals, long_rate, 0.01)
    nonzero = result[result != 0.0]
    assert len(nonzero) == 1
    assert nonzero.iloc[0] < 0
    assert nonzero.iloc[0] == pytest.approx(long_rate / 365.0)
