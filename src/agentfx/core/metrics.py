from __future__ import annotations

import numpy as np
import pandas as pd


def compute_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1.0 + returns).cumprod()


def sharpe(returns: pd.Series, trading_days_per_year: int = 252) -> float:
    # Resample to daily returns before annualizing to avoid inflating
    # the ratio with high-frequency autocorrelated bar returns.
    eq = (1.0 + returns).cumprod()
    if not isinstance(eq.index, pd.DatetimeIndex):
        # Fallback for non-datetime indices (e.g., unit tests with RangeIndex)
        mu = returns.mean() * trading_days_per_year
        sigma = returns.std(ddof=0) * np.sqrt(trading_days_per_year)
        return float(mu / sigma) if sigma > 0 else 0.0
    daily_returns = eq.resample("1D").last().pct_change().dropna()
    if daily_returns.empty or daily_returns.std(ddof=0) == 0:
        return 0.0
    mu = daily_returns.mean() * trading_days_per_year
    sigma = daily_returns.std(ddof=0) * np.sqrt(trading_days_per_year)
    return float(mu / sigma)


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def hit_rate(trade_returns: pd.Series) -> float:
    if trade_returns.empty:
        return 0.0
    wins = (trade_returns > 0).sum()
    return float(wins / len(trade_returns))


def per_trade_returns(returns: pd.Series, signals: pd.Series) -> pd.Series:
    """Collapse per-bar returns into one cumulative return per closed trade.

    A "trade" is a contiguous run of bars with the same non-zero signal
    (e.g. +1, +1, +1 → one long trade; -1, -1 → one short trade). Bars
    with signal=0 are flat and don't belong to any trade. Returns are
    compounded multiplicatively within each trade so the result matches
    (exit_price / entry_price - 1) modulo sign.

    This is the trade-level definition the rest of the system (Gate B's
    hit-rate threshold, the backtest endpoint's `capture_signals` block,
    trader UX intuition) assumes. The earlier `summarize()` was computing
    hit_rate from bar-level returns sampled at signal-flip points, which
    is a near-meaningless statistic for any opposite-signal-exit
    strategy. See plan addendum "Hit rate inconsistency between validator
    folds and re-backtest" for the root cause analysis.
    """
    if returns.empty or signals.empty:
        return pd.Series(dtype=float)
    s = signals.fillna(0)
    # New "group" starts each time the signal value changes. Consecutive
    # bars with the same signal share a group id.
    group_ids = s.ne(s.shift()).cumsum()
    in_trade = s != 0
    if not in_trade.any():
        return pd.Series(dtype=float)
    trade_rets = (1.0 + returns[in_trade]).groupby(group_ids[in_trade]).prod() - 1.0
    # If the final bar of the series is still in a non-zero position,
    # that group hasn't been closed yet — drop it. Hit rate and trade
    # count should reflect *realised* P&L only; an open position is not
    # a closed trade.
    if s.iloc[-1] != 0:
        last_group = int(group_ids.iloc[-1])
        if last_group in trade_rets.index:
            trade_rets = trade_rets.drop(last_group)
    return trade_rets


def turnover(signals: pd.Series) -> float:
    # Sum of absolute changes in position (per period)
    return float(signals.diff().abs().fillna(0).sum())


def _cagr(eq: pd.Series) -> float:
    if len(eq) <= 1 or eq.iloc[-1] <= 0:
        return 0.0
    if isinstance(eq.index, pd.DatetimeIndex):
        total_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400
        years = total_days / 365.25
    else:
        # Fallback: assume 252 trading days per year
        years = len(eq) / 252
    if years <= 0:
        return 0.0
    return float(eq.iloc[-1] ** (1.0 / years) - 1.0)


def summarize(returns: pd.Series, signals: pd.Series) -> dict[str, float]:
    eq = equity_curve(returns)
    # Build the closed-trade return series ONCE — every trade-level metric
    # below derives from it so they all share the same definition. This
    # was previously two different things: `trade_rets` was per-bar
    # returns sampled at signal-flip points (a near-useless statistic for
    # opposite-signal-exit strategies — typically 6-10% even for healthy
    # mean reverters), while `n_trades` counted position changes (which
    # double-counts: flat→long→flat is 2 changes for 1 trade). The new
    # `per_trade_returns` collapses both into the trader-meaningful
    # "entry-to-exit P&L per closed trade".
    trade_rets = per_trade_returns(returns, signals)
    mdd_value = max_drawdown(eq)
    return {
        "sharpe": sharpe(returns),
        "mdd": mdd_value,
        "max_drawdown": mdd_value,  # Alias for Gate B compatibility
        "hit_rate": hit_rate(trade_rets),
        "turnover": turnover(signals),
        "cagr": _cagr(eq),
        "n_trades": int(len(trade_rets)),  # Count of closed-trade groups
    }
