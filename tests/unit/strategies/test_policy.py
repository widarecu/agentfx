from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta, timezone

from agentfx.core.policy import PolicyConfig, apply_policies


def make_index(n=120, start_time=None):
    if start_time is None:
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(
            minutes=n
        )
    else:
        start = start_time
    return pd.date_range(start=start, periods=n, freq="min", tz="UTC")


def test_order_rate_throttle_blocks_excess_trades():
    idx = make_index(120)
    # Alternate position every minute -> 119 trades
    sig = pd.Series([(-1) ** i for i in range(len(idx))], index=idx, dtype=float)
    prices = pd.Series(1.0, index=idx)

    cfg = PolicyConfig(
        max_abs_position=1, max_trades_per_hour=10, daily_loss_threshold=1.0
    )
    res = apply_policies(sig, prices, cfg)

    # With cap of 10 per 60m, we should block many changes
    assert res.trades_blocked_rate >= 1
    # After throttling, changes count should be <= cap in any 60m window
    changes = res.adjusted_signals.diff().fillna(0).abs()
    counts = changes.gt(0).rolling("60min").sum().max()
    assert counts <= 10


def test_daily_loss_halts_until_eod():
    # Use a fixed date at 10 AM UTC to avoid crossing midnight
    start_time = datetime(2023, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    idx = make_index(300, start_time=start_time)  # 5 hours: 10 AM to 3 PM
    # Flat signals
    sig = pd.Series(1.0, index=idx)
    # Create a drop within day exceeding threshold
    prices = pd.Series(1.0, index=idx)
    # simulate steady price then abrupt 3% drop
    drop_point = idx[120]
    prices.loc[:drop_point] = 1.0
    prices.loc[drop_point:] = 0.97

    cfg = PolicyConfig(
        max_abs_position=1, max_trades_per_hour=1000, daily_loss_threshold=0.02
    )
    res = apply_policies(sig, prices, cfg)

    # Should have at least one halted day
    assert res.halted_days >= 1
    # After drop point, signals should be flattened to 0
    assert (res.adjusted_signals.loc[drop_point:] == 0.0).all()
