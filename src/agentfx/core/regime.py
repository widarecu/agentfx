"""Regime tagging for time slices of price data.

A "regime" is a short, human-readable label summarising how a market behaved
during a time slice. The v0.4.0 validator uses these labels in two ways:

1. **UI**: each walk-forward fold (training or OOS) shows its regime
   alongside Sharpe/hit-rate/etc so a reviewer can see at-a-glance whether
   the training and OOS slices were the same kind of market.
2. **Early-stop check**: if a run's training folds are one regime and its
   OOS folds are a different regime, the strategy that wins the GA will
   almost certainly lose on OOS (today's pattern: 8 of 9 recent runs).
   When the operator opts in via the form checkbox, the orchestrator
   aborts before generation 0 if train/OOS regimes disagree past a
   threshold, saving 30 generations of doomed compute.

This module is statistics-only. No machine learning, no LLM. Three
measurements combine into a label:

- **Volatility quintile**: standard deviation of bar-level returns, bucketed
  against absolute thresholds calibrated for FX majors. ``low`` (annualised
  vol < ~8%), ``med`` (8-15%), ``high`` (> 15%).
- **Trend strength**: ratio of net move to total path length. If the price
  ended far from where it started relative to its zigzag, that's trending;
  if it zigzagged a lot but ended near where it started, that's choppy.
- **Direction**: sign of net move as a fraction of starting price. Adds
  directional context to the trend coordinate — a "choppy" slice can still
  drift up or down by 1-2% over a 22-day window, which is enough to flip
  the verdict on a directionally-biased strategy. Added in the v0.4.0
  diagnostic pass that traced a catastrophic OOS fold to a +146-pip
  directional reversal between adjacent windows the prior tag couldn't
  distinguish.

The thresholds are hard-coded for EUR_USD-shaped pairs at minute-to-hour
granularities. They are NOT empirically calibrated against years of OANDA
data — they are sensible first cuts the team can refine once we have
production observations across the regime labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

VolatilityLabel = Literal["low", "med", "high"]
TrendLabel = Literal["trending", "choppy", "mixed"]
DirectionLabel = Literal["up", "down", "flat"]

# Bars per trading day per granularity. Used to annualise the bar-level
# standard deviation of returns into a comparable cross-timeframe number.
# 252 trading days per year × bars per day.
_BARS_PER_DAY: dict[str, int] = {
    "M1": 1440,
    "M5": 288,
    "M10": 144,
    "M15": 96,
    "M30": 48,
    "H1": 24,
    "H4": 6,
    "D": 1,
}

# Annualised-volatility thresholds (decimal, e.g. 0.08 = 8%) for the
# vol bucket. Calibrated against the rough EUR_USD norm of ~7-12%
# annualised; tune if production observations consistently bucket
# things differently.
_VOL_LOW = 0.08
_VOL_HIGH = 0.15

# Trend-strength thresholds. The ratio is (|end - start|) / (sum of
# absolute bar-to-bar moves). 0 = pure noise, 1 = monotone straight line.
# Real FX clusters around 0.05-0.30; "trending" markets sit at the high end.
_TREND_STRONG = 0.30
_TREND_WEAK = 0.15

# Direction thresholds. ``(close[-1] - close[0]) / close[0]`` as a percent.
# A 22-day EUR_USD window typically drifts 0.3-1.5% over its span; ``flat``
# captures the range where directional bias isn't strong enough to label.
# The diagnostic on 2026-06-10 showed that adjacent low-vol-choppy folds
# can swing from -1.13% to +1.36% net move — a 2.5pp directional
# reversal that the previous tagger collapsed into the same label.
_DIRECTION_FLAT = 0.005  # |net move| < 0.5% → flat


@dataclass(frozen=True)
class RegimeTag:
    """Three coordinate labels + a combined string suitable for badges."""

    volatility: VolatilityLabel
    trend: TrendLabel
    direction: DirectionLabel = "flat"

    @property
    def label(self) -> str:
        """Combined ``vol-trend-direction`` form, e.g.
        ``"med-vol-trending-up"``. The direction suffix is always
        present so consumers (UI badges, log messages) can pattern-match
        on a stable shape.
        """
        return f"{self.volatility}-vol-{self.trend}-{self.direction}"


def _annualised_vol(returns: pd.Series, granularity: str) -> float:
    """Annualised standard deviation of bar-level returns.

    ``pd.Series.std()`` × √(bars_per_year). Bars-per-year = 252 × bars-per-day
    for the given granularity. Falls back to 252 if granularity is unknown
    (treats unknown as daily).
    """
    if len(returns) < 2:
        return 0.0
    bars_per_day = _BARS_PER_DAY.get(granularity.upper(), 1)
    bars_per_year = 252 * bars_per_day
    return float(returns.std() * (bars_per_year**0.5))


def _trend_strength(closes: pd.Series) -> float:
    """Net move divided by total path length. Returns a ratio in [0, 1].

    Zero = pure noise (price ended where it started after meandering).
    One = monotone straight line (never reversed). Real FX clusters around
    0.05–0.30; values above 0.30 are uncommon and indicate a strong trend.

    If the closes series is empty or has zero total path length (a flat
    line), returns 0.0 — there is no trend to speak of.
    """
    if len(closes) < 2:
        return 0.0
    net_move = abs(float(closes.iloc[-1]) - float(closes.iloc[0]))
    total_path = float(closes.diff().abs().sum())
    if total_path == 0.0:
        return 0.0
    return net_move / total_path


def _volatility_label(annualised_vol: float) -> VolatilityLabel:
    if annualised_vol < _VOL_LOW:
        return "low"
    if annualised_vol < _VOL_HIGH:
        return "med"
    return "high"


def _trend_label(strength: float) -> TrendLabel:
    if strength >= _TREND_STRONG:
        return "trending"
    if strength < _TREND_WEAK:
        return "choppy"
    return "mixed"


def _net_move_pct(closes: pd.Series) -> float:
    """Signed net move as a fraction of the starting price.

    Positive = price ended higher than it started; negative = ended lower.
    Returns 0.0 if the series is empty or the starting price is zero
    (defensive — shouldn't happen on real candle data).
    """
    if len(closes) < 2:
        return 0.0
    start = float(closes.iloc[0])
    if start == 0.0:
        return 0.0
    return (float(closes.iloc[-1]) - start) / start


def _direction_label(net_move_pct: float) -> DirectionLabel:
    if net_move_pct > _DIRECTION_FLAT:
        return "up"
    if net_move_pct < -_DIRECTION_FLAT:
        return "down"
    return "flat"


def compute_regime(
    df: pd.DataFrame,
    granularity: str = "M1",
) -> RegimeTag:
    """Compute the regime tag for a slice of OHLC data.

    Args:
        df: A DataFrame with at least a ``close`` column. Typically the
            window the validator is about to backtest on. Must have at least
            2 rows; with fewer than 2 rows the result is meaningless and the
            function returns ``("low", "choppy")`` as a defensive default.
        granularity: Candle code (``"M1"``, ``"M10"``, ``"H1"``, ...). Used
            to annualise volatility correctly. Defaults to ``"M1"``.

    Returns:
        ``RegimeTag(volatility=..., trend=...)``.

    Raises:
        KeyError: if ``df`` does not contain a ``close`` column.
    """
    if "close" not in df.columns:
        raise KeyError("compute_regime requires a 'close' column on df")
    if len(df) < 2:
        return RegimeTag(volatility="low", trend="choppy", direction="flat")

    closes = df["close"]
    returns = closes.pct_change().dropna()

    vol = _annualised_vol(returns, granularity)
    trend = _trend_strength(closes)
    net_move = _net_move_pct(closes)

    return RegimeTag(
        volatility=_volatility_label(vol),
        trend=_trend_label(trend),
        direction=_direction_label(net_move),
    )


def regimes_match(a: RegimeTag, b: RegimeTag) -> bool:
    """Two tags match iff all three coordinates agree exactly.

    Used by the orchestrator's early-stop check to decide whether a
    training fold's regime is "the same kind of market" as an OOS fold's.
    Direction (added in v0.4.0 follow-up) is part of the match — a
    low-vol-choppy-up fold is genuinely a different market than a
    low-vol-choppy-down fold, even though the older two-coordinate tag
    collapsed them. "mixed" trend is NOT treated as a wildcard — it must
    equal "mixed" on both sides. "flat" direction likewise is its own
    bucket, not a wildcard. Strict equality is the conservative choice;
    we can relax it (e.g. treat "flat" as compatible with up OR down)
    once production observations show how often it fires.
    """
    return (
        a.volatility == b.volatility
        and a.trend == b.trend
        and a.direction == b.direction
    )


__all__ = [
    "DirectionLabel",
    "RegimeTag",
    "TrendLabel",
    "VolatilityLabel",
    "compute_regime",
    "regimes_match",
]
