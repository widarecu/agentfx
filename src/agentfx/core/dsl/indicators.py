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


# ─────────────────────────────────────────────────────────────────────
# v0.4.0 indicator expansion (2026-06-16)
# ─────────────────────────────────────────────────────────────────────
#
# Five new indicator families added so the GA + LLM have richer
# vocabulary than the original 9. Each family ships as one or two
# grammar entries depending on whether it produces a single line or a
# pair you'd typically cross.


def macd_line(df: pd.DataFrame, n_fast: int, n_slow: int) -> pd.Series:
    """MACD line: EMA(close, n_fast) - EMA(close, n_slow).

    Standard parameters are 12 / 26 but the GA can pick any combination
    inside the bounds. Above zero = fast EMA above slow EMA (bullish
    momentum); below zero = bearish. Most commonly compared against the
    `macd_signal` line (Cross of the two = classic MACD signal).
    """
    n_fast = int(n_fast)
    n_slow = int(n_slow)
    close = _close(df)
    return (
        close.ewm(span=n_fast, adjust=False, min_periods=1).mean()
        - close.ewm(span=n_slow, adjust=False, min_periods=1).mean()
    )


def macd_signal(df: pd.DataFrame, n_fast: int, n_slow: int, n_signal: int) -> pd.Series:
    """MACD signal line: EMA of macd_line with span=n_signal.

    Crossover of macd_line above macd_signal is the canonical "go long"
    trigger; crossover below = "go short." Default n_signal=9.
    """
    n_signal = int(n_signal)
    line = macd_line(df, n_fast, n_slow)
    return line.ewm(span=n_signal, adjust=False, min_periods=1).mean()


def stoch(df: pd.DataFrame, n: int, k_smooth: int) -> pd.Series:
    """Stochastic oscillator %K, smoothed.

    %K = 100 * (close - low_n) / (high_n - low_n), then SMA-smoothed
    over k_smooth bars. Oscillator in [0, 100]: above 80 = overbought,
    below 20 = oversold. Bounded like RSI — `is_oscillator()` returns
    True for it.
    """
    n = int(n)
    k_smooth = int(k_smooth)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = _close(df)
    lowest_low = low.rolling(n, min_periods=n).min()
    highest_high = high.rolling(n, min_periods=n).max()
    range_ = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100.0 * (close - lowest_low) / range_
    return raw_k.rolling(k_smooth, min_periods=1).mean()


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    """Average Directional Index — trend STRENGTH, not direction.

    Wilder's classic computation. Returns a value in [0, 100]:
      > 25 → strong trend (any direction)
      < 20 → weak/no trend
    Most commonly used as a FILTER: only trade trend-following
    strategies when adx > some_threshold. Always non-negative —
    `is_oscillator()` returns True for the regime-family check.
    """
    n = int(n)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = _close(df)
    prev_close = close.shift(1)

    # True range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional movement
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # Wilder smoothing via EWM with alpha=1/n
    alpha = 1.0 / n
    tr_smooth = tr.ewm(alpha=alpha, adjust=False, min_periods=1).mean()
    plus_di = (
        100.0
        * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=1).mean()
        / tr_smooth.replace(0, np.nan)
    )
    minus_di = (
        100.0
        * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=1).mean()
        / tr_smooth.replace(0, np.nan)
    )

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, adjust=False, min_periods=1).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — cumulative direction-weighted volume.

    On bars where close > prev_close, add this bar's volume; where
    close < prev_close, subtract it; equal → unchanged. The level itself
    has no absolute meaning; STRATEGIES compare its current value
    against a moving average of it, or look at OBV trend vs price
    trend (divergence).

    **Important data caveat:** the platform's `volume` field is OANDA's
    **tick volume** — a count of price updates within the bar, not true
    traded volume (which the FX market does not centrally publish).
    Tick volume is a reasonable activity proxy but is less precise than
    the equities-market sense of "volume." OBV remains useful as a
    momentum/exhaustion confirmation signal but should not be treated
    as if it were institutional flow.
    """
    if "volume" not in df.columns:
        # Defensive: candle-loader always provides volume, but degraded
        # data sources might not. Return zeros so backtests don't crash.
        return pd.Series(0.0, index=df.index)
    close = _close(df)
    volume = df["volume"].astype(float)
    direction = close.diff().apply(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0))
    return (direction * volume).cumsum()


def ichimoku_tenkan(df: pd.DataFrame, n: int) -> pd.Series:
    """Ichimoku Tenkan-sen (Conversion Line): midpoint of last n bars'
    range. (highest_high(n) + lowest_low(n)) / 2.

    Default n=9 in the classic Ichimoku setup. Faster of the two
    primary Ichimoku lines.
    """
    n = int(n)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    highest = high.rolling(n, min_periods=n).max()
    lowest = low.rolling(n, min_periods=n).min()
    return (highest + lowest) / 2.0


def ichimoku_kijun(df: pd.DataFrame, n: int) -> pd.Series:
    """Ichimoku Kijun-sen (Base Line): midpoint of last n bars' range.

    Default n=26. Slower of the two primary Ichimoku lines. Crossover
    of tenkan above kijun is the classic Ichimoku "Tenkan/Kijun cross"
    bullish signal.
    """
    n = int(n)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    highest = high.rolling(n, min_periods=n).max()
    lowest = low.rolling(n, min_periods=n).min()
    return (highest + lowest) / 2.0


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
    # v0.4.0 expansion
    "macd_line": macd_line,
    "macd_signal": macd_signal,
    "stoch": stoch,
    "adx": adx,
    "obv": obv,
    "ichimoku_tenkan": ichimoku_tenkan,
    "ichimoku_kijun": ichimoku_kijun,
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
    # v0.4.0 expansion. Bounds chosen to keep results meaningful:
    # MACD periods clamped to the usual 5..50 (fast) and 20..200 (slow)
    # with the GA constrained to fast < slow via the grammar-level
    # `_at_least_one_side_is_indicator` validator chain — the compiler
    # rejects fast >= slow at compile time as well.
    "macd_line": [(5, 50), (10, 200)],
    "macd_signal": [(5, 50), (10, 200), (3, 50)],
    # Stochastic: lookback 5..100 bars, smoothing 1..20.
    "stoch": [(5, 100), (1, 20)],
    # ADX: classic 14, allow 2..100.
    "adx": [(2, 100)],
    # OBV: no arg.
    "obv": [],
    # Ichimoku tenkan typically 9, kijun typically 26. We allow wide
    # ranges so the GA can find pair-specific tunings.
    "ichimoku_tenkan": [(2, 100)],
    "ichimoku_kijun": [(2, 400)],
}


def evaluate_indicator(df: pd.DataFrame, name: str, args: list[float]) -> pd.Series:
    """Look up an indicator by name and call it with positional args."""
    fn = INDICATOR_FUNCTIONS[name]
    return fn(df, *args)


def is_oscillator(name: str) -> bool:
    """True for indicators returning bounded values (not price units).

    Used by the compiler / mutator to refuse comparisons between
    incompatible scales (e.g. ``sma > rsi`` is nonsensical). Anything
    returning a value in [0, 100] qualifies: RSI, stochastic, ADX. The
    full original set ([0, 100] sentiment oscillators) is now larger
    after the v0.4.0 indicator expansion.
    """
    return name in {"rsi", "stoch", "adx"}


__all__ = [
    "INDICATOR_ARG_BOUNDS",
    "INDICATOR_FUNCTIONS",
    "adx",
    "atr",
    "bb_lower",
    "bb_upper",
    "donchian_high",
    "donchian_low",
    "ema",
    "evaluate_indicator",
    "ichimoku_kijun",
    "ichimoku_tenkan",
    "is_oscillator",
    "macd_line",
    "macd_signal",
    "obv",
    "rsi",
    "sma",
    "stoch",
]


# Keep numpy import used implicitly by pandas operations
_ = np
