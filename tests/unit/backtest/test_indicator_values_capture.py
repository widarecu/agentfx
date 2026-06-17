"""Trade-signal indicator-value capture for DSL strategies.

When the backtest engine runs with `capture_signals=True`, each trade
signal carries an `indicator_values` dict — the values of the
indicators referenced by the spec, sampled at the SIGNAL bar (one bar
before the entry bar; matches the no-lookahead shift in
`src/agentfx/core/dsl/compiler.py:317`).

Before 2026-06-17 `_get_indicator_values()` returned None for any DSL
strategy because it only knew the legacy MA/RSI/Donchian strategy
names — so every DSL trade signal carried an empty
`indicator_values`, and the chart's trade markers could not display
what triggered them. This suite asserts that's fixed for every
v0.4.0 indicator family.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl import StrategySpec, compile_spec
from agentfx.core.dsl.seeds import (
    ADX_THRESHOLD_SEED,
    BB_MEAN_REVERSION_SEED,
    DONCHIAN_BREAKOUT_SEED,
    ICHIMOKU_CROSS_SEED,
    MACD_CROSSOVER_SEED,
    MA_CROSSOVER_SEED,
    OBV_DIRECTIONAL_SEED,
    RSI_MEAN_REVERSION_SEED,
    STOCH_MEAN_REVERSION_SEED,
)
from agentfx.services.backtest import _get_indicator_values


def _synthetic_df(n: int = 200, seed: int = 31) -> pd.DataFrame:
    """OHLCV with non-degenerate volume, deterministic via fixed seed."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC")
    returns = rng.normal(0, 0.0006, n)
    close = 1.10 * np.exp(np.cumsum(returns))
    spread = np.abs(rng.normal(0, 0.0004, n))
    high = close + spread
    low = close - spread
    open_ = close.copy()
    volume = rng.integers(500, 1500, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _capture(spec: StrategySpec) -> pd.DataFrame | None:
    """Run the same _get_indicator_values path the backtest engine uses."""
    strat = compile_spec(spec)
    df = _synthetic_df()
    return _get_indicator_values(strat, df, "dsl", {"spec": spec.model_dump()})


@pytest.mark.unit
class TestDSLIndicatorCapture:
    def test_ma_seed_captures_both_smas(self) -> None:
        out = _capture(MA_CROSSOVER_SEED)
        assert out is not None
        assert set(out.columns) == {"sma(20)", "sma(50)"}
        # Final-bar values should be sensible price-scale numbers.
        assert 1.0 < float(out["sma(20)"].iloc[-1]) < 1.2

    def test_macd_seed_captures_line_and_signal(self) -> None:
        out = _capture(MACD_CROSSOVER_SEED)
        assert out is not None
        assert set(out.columns) == {"macd_line(12, 26)", "macd_signal(12, 26, 9)"}
        # MACD around 0 on price-delta scale.
        assert abs(float(out["macd_line(12, 26)"].iloc[-1])) < 1.0

    def test_rsi_seed_captures_rsi_only(self) -> None:
        out = _capture(RSI_MEAN_REVERSION_SEED)
        assert out is not None
        assert set(out.columns) == {"rsi(14)"}
        # Final RSI must be bounded [0, 100].
        v = float(out["rsi(14)"].iloc[-1])
        assert 0 <= v <= 100

    def test_stoch_seed_captures_stoch(self) -> None:
        out = _capture(STOCH_MEAN_REVERSION_SEED)
        assert out is not None
        assert set(out.columns) == {"stoch(14, 3)"}
        v = float(out["stoch(14, 3)"].iloc[-1])
        assert 0 <= v <= 100

    def test_bb_seed_captures_both_bands_no_close(self) -> None:
        out = _capture(BB_MEAN_REVERSION_SEED)
        assert out is not None
        # `close` is excluded from indicator_values intentionally — it's
        # just price, already on the chart axis, no diagnostic value.
        # Both bands DO need to appear so the marker can show "L 1.07"
        # for whichever band triggered.
        assert "bb_lower(20, 2)" in out.columns
        assert "bb_upper(20, 2)" in out.columns

    def test_donchian_seed_captures_both_channels(self) -> None:
        out = _capture(DONCHIAN_BREAKOUT_SEED)
        assert out is not None
        assert "donchian_high(20)" in out.columns
        assert "donchian_low(20)" in out.columns

    def test_ichimoku_seed_captures_both_lines(self) -> None:
        out = _capture(ICHIMOKU_CROSS_SEED)
        assert out is not None
        assert "ichimoku_tenkan(9)" in out.columns
        assert "ichimoku_kijun(26)" in out.columns

    def test_adx_seed_captures_adx(self) -> None:
        out = _capture(ADX_THRESHOLD_SEED)
        assert out is not None
        assert set(out.columns) == {"adx(14)"}
        v = float(out["adx(14)"].iloc[-1])
        assert 0 <= v <= 100

    def test_obv_seed_captures_obv_with_natural_label(self) -> None:
        # This is the seed that surfaced the bug originally — UAT
        # 2026-06-17, "Goes short when obv falls below 0" but no way
        # to verify the trade against the indicator line.
        out = _capture(OBV_DIRECTIONAL_SEED)
        assert out is not None
        # OBV is zero-arg so its label is the bare indicator name
        # — matches the JS labelFor() in dslIndicators.ts.
        assert set(out.columns) == {"obv"}
        # OBV is unbounded cumulative volume.
        assert isinstance(float(out["obv"].iloc[-1]), float)

    def test_returns_none_when_spec_missing(self) -> None:
        # Defensive: a "dsl" strategy_name without a `spec` param
        # shouldn't crash the backtest; it returns None and trade
        # signals just carry an empty indicator_values dict (the
        # pre-fix behaviour, retained for missing-spec edge cases).
        df = _synthetic_df()
        out = _get_indicator_values(None, df, "dsl", {})
        assert out is None

    def test_returns_none_when_spec_malformed(self) -> None:
        # A malformed spec dict shouldn't bring the whole backtest
        # down — the indicator capture is best-effort visualization.
        df = _synthetic_df()
        out = _get_indicator_values(None, df, "dsl", {"spec": {"version": "bogus"}})
        assert out is None

    def test_column_labels_match_js_labelFor(self) -> None:
        """The frontend's marker-text lookup matches by indicator-name
        prefix (case-insensitive .startsWith), so column labels must be
        `name(arg, arg)` with whole-number args rendered as ints — the
        same convention `dslIndicators.ts::labelFor()` uses. Otherwise
        the chart's marker can't find the value to display.
        """
        out = _capture(MACD_CROSSOVER_SEED)
        assert out is not None
        # 12 and 26 are stored as floats in the Pydantic model
        # (`args: list[float]`); the label must render them as "12"
        # and "26", not "12.0" and "26.0", to match JS toString.
        assert "macd_line(12, 26)" in out.columns
        assert "macd_line(12.0, 26.0)" not in out.columns
