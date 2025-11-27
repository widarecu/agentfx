from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
from collections import deque


@dataclass
class PolicyConfig:
    # Maximum absolute exposure in units of signal magnitude (for now signals are -1..1)
    max_abs_position: int = 1
    # Maximum number of trades (position changes) per hour
    max_trades_per_hour: int = 60
    # Daily max drawdown threshold as fractional loss (e.g., 0.02 for -2%) to trigger halt to flat for the rest of day
    daily_loss_threshold: float = 0.02


@dataclass
class PolicyResult:
    adjusted_signals: pd.Series
    # Simple counters/flags for observability
    trades_blocked_rate: int
    halted_days: int


def apply_policies(
    signals: pd.Series,
    prices: pd.Series,
    cfg: PolicyConfig | None = None,
) -> PolicyResult:
    """Apply simple policy rules to an input signal series.

    Rules:
    - Enforce max_abs_position by clipping signals to [-max_abs_position, max_abs_position].
    - Enforce max_trades_per_hour by throttling position changes if more than N in a rolling 60m window.
    - Enforce daily_loss_threshold: compute intraday equity relative to open; if loss exceeds threshold, flat for rest of day.
    """
    cfg = cfg or PolicyConfig()
    s = signals.copy().fillna(0).astype(float)
    s = s.clip(lower=-cfg.max_abs_position, upper=cfg.max_abs_position)

    # Throttle trades per hour using iterative limiter over rolling 60 minutes
    # Accept at most cfg.max_trades_per_hour changes in any 60-minute window
    accepted = []
    last_pos = 0.0
    accepted_trade_times: deque[pd.Timestamp] = deque()
    trades_blocked = 0
    for ts, val in s.items():
        desired = float(val)
        is_change = desired != last_pos
        if is_change:
            # drop trades older than 60 minutes from current ts
            cutoff = ts - pd.Timedelta(minutes=60)
            while accepted_trade_times and accepted_trade_times[0] <= cutoff:
                accepted_trade_times.popleft()
            if len(accepted_trade_times) >= cfg.max_trades_per_hour:
                # block this change
                accepted.append(last_pos)
                trades_blocked += 1
                continue
            else:
                # accept this change
                accepted_trade_times.append(ts)
                last_pos = desired
                accepted.append(last_pos)
        else:
            accepted.append(last_pos)
    s_blocked = pd.Series(accepted, index=s.index, dtype=float)

    # Daily loss circuit breaker
    # Compute daily equity starting at 1.0 and track drawdown within each day using prices and current position
    pos = s_blocked.shift(0).fillna(0)
    returns = prices.pct_change().fillna(0.0) * pos
    halted_days = 0
    daily_flag = pd.Series(False, index=s.index)
    for day, grp in returns.groupby(returns.index.date):
        eq = (1.0 + grp).cumprod()
        peak = eq.cummax()
        dd = 1.0 - (eq / peak)
        if (dd.max() >= cfg.daily_loss_threshold) or (
            (1.0 - eq.iloc[-1]) >= cfg.daily_loss_threshold
        ):
            # Halt from the first time threshold exceeded until end of day
            exceed_idx = dd[dd >= cfg.daily_loss_threshold].index
            if len(exceed_idx) == 0:
                # fallback: end-of-day breach
                exceed_point = grp.index[-1]
            else:
                exceed_point = exceed_idx[0]
            daily_flag.loc[exceed_point : grp.index[-1]] = True
            halted_days += 1
    # Flatten on flagged segments
    s_final = s_blocked.where(~daily_flag, other=0.0)

    return PolicyResult(
        adjusted_signals=s_final,
        trades_blocked_rate=trades_blocked,
        halted_days=halted_days,
    )
