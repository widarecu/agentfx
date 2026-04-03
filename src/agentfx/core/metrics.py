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
    # Approximate trades when signal changes (position change)
    trade_mask = signals.diff().fillna(0).ne(0)
    trade_rets = returns.where(trade_mask).dropna()
    mdd_value = max_drawdown(eq)
    return {
        "sharpe": sharpe(returns),
        "mdd": mdd_value,
        "max_drawdown": mdd_value,  # Alias for Gate B compatibility
        "hit_rate": hit_rate(trade_rets),
        "turnover": turnover(signals),
        "cagr": _cagr(eq),
        "n_trades": int(trade_mask.sum()),  # Count of position changes (trades)
    }
