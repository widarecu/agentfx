from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from agentfx.core.strategy import MACrossover


def make_df(values):
    # Build a small OHLCV frame using provided close values
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(
        minutes=len(values)
    )
    idx = pd.date_range(start=start, periods=len(values), freq="T", tz="UTC")
    df = pd.DataFrame(
        {
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "volume": 1,
        },
        index=idx,
    )
    df = df.reset_index().rename(columns={"index": "time"})
    df = df.set_index("time")
    return df


def test_ma_crossover_basic_no_lookahead():
    # Construct a series that rises then falls then rises to induce crossovers
    closes = [1, 2, 3, 4, 3, 2, 1, 2, 3]
    df = make_df(closes)
    strat = MACrossover(fast=2, slow=3)

    sig = strat.generate_signals(df)

    # Verify length and index alignment
    assert len(sig) == len(df)
    assert sig.index.equals(df.index)

    # Recompute expected signals per spec (shifted by 1 to avoid lookahead)
    close = pd.Series(closes, index=df.index, dtype=float)
    fast_ma = close.rolling(2, min_periods=1).mean()
    slow_ma = close.rolling(3, min_periods=1).mean()
    raw = (fast_ma > slow_ma).astype(int) - (fast_ma < slow_ma).astype(int)
    expected = raw.shift(1).fillna(0)

    pd.testing.assert_series_equal(
        sig.astype(float), expected.astype(float), check_names=False
    )

    # No lookahead: current signal equals previous bar's raw crossover
    # i.e., sig[t] == raw[t-1]
    assert (sig.iloc[1:] == raw.shift(1).iloc[1:].fillna(0)).all()

    # First signal is zero due to shift
    assert sig.iloc[0] == 0


def test_ma_crossover_params_effect():
    closes = [1, 2, 3, 4, 5, 4, 3, 2, 1]
    df = make_df(closes)

    strat_fast = MACrossover(fast=2, slow=3)
    strat_slow = MACrossover(fast=3, slow=5)

    sig_fast = strat_fast.generate_signals(df)
    sig_slow = strat_slow.generate_signals(df)

    # Different parameters should produce different signals at some point
    assert (sig_fast != sig_slow).any()


def test_ma_crossover_handles_short_series():
    # Fewer rows than window sizes
    closes = [1]
    df = make_df(closes)
    strat = MACrossover(fast=5, slow=10)

    sig = strat.generate_signals(df)
    # Should still return a single 0 signal without error
    assert len(sig) == 1
    assert sig.iloc[0] == 0
