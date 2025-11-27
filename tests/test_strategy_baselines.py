from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta, timezone

from agentfx.core.strategy import RSIMeanReversion, DonchianBreakout


def make_df_from_close(values):
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=len(values))
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


def test_rsi_mean_reversion_basic_behavior_no_lookahead():
    # Create sequence that dips below lower then above upper
    closes = [50, 49, 48, 47, 46, 47, 48, 49, 50, 51, 52]
    df = make_df_from_close(closes)
    strat = RSIMeanReversion(window=3, lower=40, upper=60)
    sig = strat.generate_signals(df)

    # Alignment and shift
    assert len(sig) == len(df)
    assert sig.index.equals(df.index)
    assert sig.iloc[0] == 0

    # When RSI low, expect long later (after shift), and vice versa for high
    # We cannot easily compute RSI here; minimally ensure some non-zero signals exist
    assert (sig != 0).any()


def test_rsi_params_influence_signals():
    closes = [50, 49, 48, 47, 46, 47, 48, 49, 50, 51, 52]
    df = make_df_from_close(closes)
    a = RSIMeanReversion(window=3, lower=40, upper=60).generate_signals(df)
    b = RSIMeanReversion(window=5, lower=30, upper=70).generate_signals(df)
    assert (a != b).any()


def test_donchian_breakout_signals_on_channel_break():
    # Construct highs/lows to form a channel then break
    closes = [1, 1, 1, 1, 1, 1.1, 1.2, 1.3, 1.4]
    df = make_df_from_close(closes)
    strat = DonchianBreakout(window=3)
    sig = strat.generate_signals(df)
    # After enough bars, a breakout should trigger at least once
    assert (sig != 0).any()
    # First signal should be 0 due to shift
    assert sig.iloc[0] == 0


def test_donchian_window_affects_trigger():
    # Use a short history where the smaller window can trigger earlier than the larger window
    closes = [1.00, 1.00, 1.05, 1.06]
    df = make_df_from_close(closes)
    s_small = DonchianBreakout(window=2).generate_signals(df)
    s_large = DonchianBreakout(window=5).generate_signals(df)
    # Expect at least one difference due to insufficient prior window for the larger period early on
    assert (s_small != s_large).any()
