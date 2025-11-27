from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


class Strategy:
    """Base strategy interface.

    Implement generate_signals to return a Series of +1 (long), -1 (short), or 0 (flat)
    aligned to the input DataFrame index.
    """

    def generate_signals(
        self, df: pd.DataFrame
    ) -> pd.Series:  # pragma: no cover (interface)
        raise NotImplementedError


@dataclass
class MACrossover(Strategy):
    fast: int = 20
    slow: int = 50

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"].astype(float)
        fast_ma = close.rolling(self.fast, min_periods=1).mean()
        slow_ma = close.rolling(self.slow, min_periods=1).mean()
        sig = (fast_ma > slow_ma).astype(int) - (fast_ma < slow_ma).astype(int)
        # Position is previous signal to avoid lookahead
        return sig.shift(1).fillna(0)


@dataclass
class RSIMeanReversion(Strategy):
    window: int = 14
    lower: float = 30.0
    upper: float = 70.0

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"].astype(float)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(self.window, min_periods=self.window).mean()
        avg_loss = loss.rolling(self.window, min_periods=self.window).mean()
        rs = avg_gain / (avg_loss.replace(0, pd.NA))
        rsi = 100 - (100 / (1 + rs))
        long_sig = (rsi < self.lower).astype(int)
        short_sig = (rsi > self.upper).astype(int)
        sig = long_sig - short_sig
        return sig.shift(1).fillna(0)


@dataclass
class DonchianBreakout(Strategy):
    window: int = 20

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        # Use prior window (exclude current bar) for channel
        upper = high.shift(1).rolling(self.window, min_periods=self.window).max()
        lower = low.shift(1).rolling(self.window, min_periods=self.window).min()
        long_sig = (close > upper).astype(int)
        short_sig = (close < lower).astype(int)
        sig = long_sig - short_sig
        return sig.shift(1).fillna(0)
