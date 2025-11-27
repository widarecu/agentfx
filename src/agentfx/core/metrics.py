from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict


def compute_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1.0 + returns).cumprod()


def sharpe(returns: pd.Series, periods_per_year: int = 252*24*60) -> float:
    # For 1m bars, periods per (trading) year is high; this is a rough MVP scaler
    mu = returns.mean() * periods_per_year
    sigma = returns.std(ddof=0) * np.sqrt(periods_per_year)
    return float(mu / sigma) if sigma > 0 else 0.0


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


def summarize(returns: pd.Series, signals: pd.Series) -> Dict[str, float]:
    eq = equity_curve(returns)
    # Approximate trades when signal changes (position change)
    trade_mask = signals.diff().fillna(0).ne(0)
    trade_rets = returns.where(trade_mask).dropna()
    return {
        "sharpe": sharpe(returns),
        "mdd": max_drawdown(eq),
        "hit_rate": hit_rate(trade_rets),
        "turnover": turnover(signals),
        "cagr": float(eq.iloc[-1] ** (252*24*60 / max(1, len(eq))) - 1.0) if len(eq) > 1 else 0.0,
    }
