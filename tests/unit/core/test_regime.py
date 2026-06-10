"""Unit tests for the regime-tagging module.

The tagger should be:
- Deterministic: same input → same label.
- Statistically reasonable: hand-constructed "clearly trending" data should
  read as trending; hand-constructed "clearly choppy" data should read as
  choppy. We do not over-specify the exact threshold values here — tests
  exercise the *direction* of the classification, not the exact cutoffs,
  so the implementation can be tuned without breaking the suite.
- Defensive: empty / single-row frames must not crash.

The compute_regime() output is consumed by the orchestrator (early-stop
check) and by the validator (per-fold tagging surfaced in the UI). Any
change to the schema of RegimeTag is a breaking change for both consumers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agentfx.core.regime import (
    RegimeTag,
    compute_regime,
    regimes_match,
)


def _df_from_closes(closes: list[float]) -> pd.DataFrame:
    """Build a minimal DataFrame the tagger can read."""
    return pd.DataFrame({"close": closes})


class TestComputeRegime:
    def test_strong_uptrend_is_trending(self):
        # Monotone upward — net move ≈ total path, ratio ≈ 1
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.trend == "trending"

    def test_strong_downtrend_is_trending(self):
        # Monotone downward — same shape, opposite sign
        closes = [1.20 - 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.trend == "trending"

    def test_pure_oscillation_is_choppy(self):
        # Sine wave around a constant — net move is tiny vs total path
        closes = [1.10 + 0.001 * np.sin(i / 5.0) for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.trend == "choppy"

    def test_low_vol_data_labels_low(self):
        # Tiny returns: 0.5 bps per bar
        closes = [1.10 + 0.00001 * i for i in range(500)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.volatility == "low"

    def test_high_vol_data_labels_high(self):
        # Large alternating moves — sigma of returns should annualise high
        rng = np.random.default_rng(seed=42)
        closes_arr = 1.10 + np.cumsum(rng.normal(0, 0.005, 500))
        tag = compute_regime(
            _df_from_closes(closes_arr.tolist()),
            granularity="M1",
        )
        assert tag.volatility == "high"

    def test_empty_dataframe_returns_default_without_crash(self):
        # Defensive: avoid divide-by-zero / index error on empty slices
        tag = compute_regime(_df_from_closes([]), granularity="M1")
        assert isinstance(tag, RegimeTag)

    def test_single_row_returns_default_without_crash(self):
        tag = compute_regime(_df_from_closes([1.10]), granularity="M1")
        assert isinstance(tag, RegimeTag)

    def test_missing_close_column_raises_keyerror(self):
        # Explicit failure mode: surfaces config errors fast rather than
        # silently labelling everything as low-vol-choppy
        df = pd.DataFrame({"open": [1.10, 1.11]})
        with pytest.raises(KeyError):
            compute_regime(df, granularity="M1")

    def test_label_is_dash_joined(self):
        # Public format used in the UI badges — kebab-case parts
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.label == f"{tag.volatility}-vol-{tag.trend}"

    @pytest.mark.parametrize("granularity", ["M1", "M10", "H1", "H4", "D"])
    def test_works_across_granularities(self, granularity):
        # Same shape of data, different granularities — should produce a
        # valid tag every time even when the annualisation factor differs
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity=granularity)
        assert isinstance(tag, RegimeTag)


class TestRegimesMatch:
    def test_identical_tags_match(self):
        a = RegimeTag(volatility="med", trend="trending")
        b = RegimeTag(volatility="med", trend="trending")
        assert regimes_match(a, b) is True

    def test_different_vol_does_not_match(self):
        a = RegimeTag(volatility="low", trend="trending")
        b = RegimeTag(volatility="high", trend="trending")
        assert regimes_match(a, b) is False

    def test_different_trend_does_not_match(self):
        a = RegimeTag(volatility="med", trend="trending")
        b = RegimeTag(volatility="med", trend="choppy")
        assert regimes_match(a, b) is False

    def test_mixed_trend_is_not_a_wildcard(self):
        # Conservative choice — see docstring on regimes_match
        a = RegimeTag(volatility="med", trend="mixed")
        b = RegimeTag(volatility="med", trend="trending")
        assert regimes_match(a, b) is False
