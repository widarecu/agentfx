"""Pure indicator functions for the v1 DSL.

Each function takes a DataFrame with OHLCV columns and returns a pd.Series
aligned to the DataFrame's index. Functions are deterministic, side-effect
free, and operate on float-cast inputs.

Bounds on arguments are enforced by the grammar; this module trusts its
inputs. Any compile-time bounds checking lives in compiler.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _close(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float)


def close(df: pd.DataFrame) -> pd.Series:
    """The close price series itself.

    Provided as a pseudo-indicator so DSL specs can reference current close
    without needing `sma(close, 1)` workarounds. Zero args.
    """
    return _close(df)


def sma(df: pd.DataFrame, n: int) -> pd.Series:
    """Simple moving average of close. Matches the existing MACrossover
    semantics (min_periods=1 — value available from bar 1)."""
    n = int(n)
    return _close(df).rolling(n, min_periods=1).mean()


def ema(df: pd.DataFrame, n: int) -> pd.Series:
    """Exponential moving average of close (span=n, adjust=False)."""
    n = int(n)
    return _close(df).ewm(span=n, adjust=False, min_periods=1).mean()


def rsi(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder-style RSI of close. Matches the existing RSIMeanReversion
    semantics: min_periods=n, divides by zero handled via NaN replacement.
    """
    n = int(n)
    close = _close(df)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(n, min_periods=n).mean()
    avg_loss = loss.rolling(n, min_periods=n).mean()
    # Use np.nan (not pd.NA) so downstream float arithmetic and astype(float)
    # work cleanly. Result: rsi == NaN where avg_loss == 0 (no down moves yet).
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """Average true range over n bars."""
    n = int(n)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = _close(df)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def _bollinger_mid_std(df: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    close = _close(df)
    mid = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, std


def bb_upper(df: pd.DataFrame, n: int, k: float) -> pd.Series:
    """Bollinger upper band: SMA(n) + k * std(n)."""
    n = int(n)
    k = float(k)
    mid, std = _bollinger_mid_std(df, n)
    return mid + k * std


def bb_lower(df: pd.DataFrame, n: int, k: float) -> pd.Series:
    """Bollinger lower band: SMA(n) − k * std(n)."""
    n = int(n)
    k = float(k)
    mid, std = _bollinger_mid_std(df, n)
    return mid - k * std


def donchian_high(df: pd.DataFrame, n: int) -> pd.Series:
    """Running max of prior n bars' highs (excludes current bar).

    Matches the existing DonchianBreakout channel: high.shift(1).rolling(n).max()
    """
    n = int(n)
    return df["high"].astype(float).shift(1).rolling(n, min_periods=n).max()


def donchian_low(df: pd.DataFrame, n: int) -> pd.Series:
    """Running min of prior n bars' lows (excludes current bar).

    Matches the existing DonchianBreakout channel: low.shift(1).rolling(n).min()
    """
    n = int(n)
    return df["low"].astype(float).shift(1).rolling(n, min_periods=n).min()


# Dispatch table for the compiler. Keys are the IndicatorName literals.
INDICATOR_FUNCTIONS = {
    "close": close,
    "sma": sma,
    "ema": ema,
    "rsi": rsi,
    "atr": atr,
    "bb_upper": bb_upper,
    "bb_lower": bb_lower,
    "donchian_high": donchian_high,
    "donchian_low": donchian_low,
}

# Argument-arity (excluding df) and bounds per indicator.
# Used by the compiler for sanity checking. close has no args.
INDICATOR_ARG_BOUNDS: dict[str, list[tuple[float, float]]] = {
    "close": [],
    "sma": [(2, 400)],
    "ema": [(2, 400)],
    "rsi": [(2, 100)],
    "atr": [(2, 100)],
    "bb_upper": [(2, 200), (0.5, 4.0)],
    "bb_lower": [(2, 200), (0.5, 4.0)],
    "donchian_high": [(2, 400)],
    "donchian_low": [(2, 400)],
}


def evaluate_indicator(df: pd.DataFrame, name: str, args: list[float]) -> pd.Series:
    """Look up an indicator by name and call it with positional args."""
    fn = INDICATOR_FUNCTIONS[name]
    return fn(df, *args)


def is_oscillator(name: str) -> bool:
    """RSI is the only oscillator in v1 (returns values in [0, 100] not price units)."""
    return name == "rsi"


__all__ = [
    "INDICATOR_ARG_BOUNDS",
    "INDICATOR_FUNCTIONS",
    "atr",
    "bb_lower",
    "bb_upper",
    "donchian_high",
    "donchian_low",
    "ema",
    "evaluate_indicator",
    "is_oscillator",
    "rsi",
    "sma",
]


# Keep numpy import used implicitly by pandas operations
_ = np
