"""Dump deterministic Python indicator values for JS parity testing.

This script generates a synthetic OHLCV series, runs every DSL indicator
with a small set of representative argument combinations, and writes the
expected per-bar values to
`portal/web/tests/fixtures/indicator-values.json`.

The JS parity test in `portal/web/tests/unit/dslIndicators.test.ts` loads
this JSON, runs each JS indicator on the same series, and asserts every
non-NaN bar matches within a float tolerance.

Re-run this script whenever any indicator's Python semantics change.
The fixture is checked in so the JS test does not need Python at run time.

Run from repo root:
    PYTHONPATH=src python scripts/dump_indicator_fixtures.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from agentfx.core.dsl import indicators as ind


def synthesize_candles(n_bars: int = 240) -> pd.DataFrame:
    """Build a deterministic OHLCV series.

    The shape is chosen to exercise every indicator's main paths:
      - a slow drift so MA/EMA/MACD produce non-trivial slopes
      - injected noise so Bollinger bands have non-zero width
      - two regime breaks (~1/3 and ~2/3) so ADX/Stoch swing across
        their range and Donchian/Ichimoku channels actually rotate
      - non-degenerate volume so OBV cumulates a varied signal

    Deterministic (no RNG): the synthetic series is fully derived from
    a fixed-seed sine + linear-ramp construction. Both Python and JS
    use the same constructed series via the fixture.
    """
    rng = np.random.default_rng(42)
    t = np.arange(n_bars, dtype=float)
    drift = 1.0850 + 0.0005 * np.sin(t / 17) + 0.0002 * (t / n_bars)
    regime_shift = np.where(t > n_bars * 0.35, 0.0008, 0.0)
    regime_shift = np.where(t > n_bars * 0.70, -0.0006, regime_shift)
    mid = drift + regime_shift + rng.normal(0, 0.00015, size=n_bars)

    spread = 0.00010 + 0.00005 * np.abs(np.sin(t / 5))
    high = mid + spread
    low = mid - spread
    open_ = np.roll(mid, 1)
    open_[0] = mid[0]
    close = mid + rng.normal(0, 0.00005, size=n_bars)

    volume = 800 + 300 * np.abs(np.sin(t / 11)) + rng.integers(0, 200, size=n_bars)

    # ISO-8601 minute-resolution timestamps — what the chart consumes.
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=n_bars, freq="1min")

    df = pd.DataFrame(
        {
            "timestamp": [t.isoformat().replace("+00:00", "Z") for t in timestamps],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume.astype(float),
        }
    )
    # Quantize every OHLCV column to absorb last-bit ULP drift across
    # platforms (macOS Accelerate vs Linux OpenBLAS — observed ~1e-13
    # diffs from np.sin and rng.normal on different blas builds).
    # Rounding HERE (in the synthesizer) rather than at serialize time
    # means both the Python indicator computation and the JS indicator
    # computation operate on identical rounded inputs — implementation
    # drift between them is then the only source of disagreement, and
    # that's bounded well under the parity test's tolerance.
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].round(_FIXTURE_DECIMAL_PRECISION)
    return df


# Each tuple: (indicator name, positional args, label used in the JS test)
# Cover small windows (boundary behaviour kicks in early) and a typical
# trading window so we exercise both the initialisation tail and the
# steady-state output of every indicator.
INDICATOR_CASES: list[tuple[str, list[float], str]] = [
    # Original 8 — regression coverage so future Python changes can't
    # silently desync without the JS parity test catching it.
    ("sma", [10], "sma(10)"),
    ("sma", [50], "sma(50)"),
    ("ema", [12], "ema(12)"),
    ("ema", [26], "ema(26)"),
    ("rsi", [14], "rsi(14)"),
    ("atr", [14], "atr(14)"),
    ("bb_upper", [20, 2.0], "bb_upper(20, 2)"),
    ("bb_lower", [20, 2.0], "bb_lower(20, 2)"),
    ("donchian_high", [20], "donchian_high(20)"),
    ("donchian_low", [20], "donchian_low(20)"),
    # v0.4.0 expansion — the indicators this fixture exists to verify.
    ("macd_line", [12, 26], "macd_line(12, 26)"),
    ("macd_line", [5, 20], "macd_line(5, 20)"),
    ("macd_signal", [12, 26, 9], "macd_signal(12, 26, 9)"),
    ("stoch", [14, 3], "stoch(14, 3)"),
    ("stoch", [5, 1], "stoch(5, 1)"),
    ("adx", [14], "adx(14)"),
    ("adx", [7], "adx(7)"),
    ("obv", [], "obv"),
    ("ichimoku_tenkan", [9], "ichimoku_tenkan(9)"),
    ("ichimoku_kijun", [26], "ichimoku_kijun(26)"),
]


# Round serialised values to this many decimal digits before writing the
# fixture JSON. Reason: the staleness guard in CI (`make verify-fixtures`
# = re-dump + `git diff --exit-code`) treats the JSON file as exact, but
# the underlying numpy/pandas float math has last-bit ULP differences
# across platforms (e.g. macOS Accelerate vs Linux OpenBLAS, or numpy
# 1.26 vs 1.27). Without rounding, a fixture generated on macOS and
# committed produces 17th-decimal-place diffs when regenerated on the
# Ubuntu CI runner — see PR #10 first CI failure. Rounding to 10 decimals
# (1e-10) is one order of magnitude tighter than the JS parity test's
# default 1e-9 tolerance, and the candle synthesizer applies the same
# rounding to the OHLCV columns BEFORE indicator computation so Python
# and JS both work on identical rounded inputs. Implementation drift
# between Python and JS indicator code is then the only source of
# disagreement, and that's bounded well under 1e-9 for every indicator
# we ship today.
_FIXTURE_DECIMAL_PRECISION = 10


def _quantize(v: float) -> float:
    """Strip platform-level ULP drift from a serialised float."""
    return round(float(v), _FIXTURE_DECIMAL_PRECISION)


def serialize_series(timestamps: list[str], series: pd.Series) -> list[dict]:
    """Convert (ts, series) pairs into [{ts, value}] dropping NaN.

    Drops NaN bars so the JS-side test can compare 1:1 against the JS
    implementation, which itself skips NaN positions. The JS series and
    the Python series, after this transform, must have identical length
    and ordering.
    """
    out: list[dict] = []
    for ts, v in zip(timestamps, series.tolist()):
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        out.append({"ts": ts, "value": _quantize(v)})
    return out


def main() -> None:
    df = synthesize_candles(n_bars=240)
    timestamps = df["timestamp"].tolist()

    candles = [
        {
            "timestamp": row.timestamp,
            "open": _quantize(row.open),
            "high": _quantize(row.high),
            "low": _quantize(row.low),
            "close": _quantize(row.close),
            "volume": _quantize(row.volume),
        }
        for row in df.itertuples(index=False)
    ]

    expected: dict[str, list[dict]] = {}
    for name, args, label in INDICATOR_CASES:
        fn = ind.INDICATOR_FUNCTIONS[name]
        series = fn(df, *args) if args else fn(df)
        expected[label] = serialize_series(timestamps, series)

    fixture = {
        "candles": candles,
        "indicators": expected,
        "_meta": {
            "n_bars": len(df),
            "n_indicators": len(INDICATOR_CASES),
            "seed": 42,
            "purpose": (
                "Per-bar parity reference between Python "
                "src/agentfx/core/dsl/indicators.py and JS "
                "portal/web/src/utils/dslIndicators.ts. "
                "Re-generate via scripts/dump_indicator_fixtures.py "
                "whenever Python indicator semantics change."
            ),
        },
    }

    out_path = (
        Path(__file__).resolve().parent.parent
        / "portal"
        / "web"
        / "tests"
        / "fixtures"
        / "indicator-values.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, indent=2))
    print(f"Wrote {len(INDICATOR_CASES)} indicators × {len(df)} bars to {out_path}")


if __name__ == "__main__":
    main()
