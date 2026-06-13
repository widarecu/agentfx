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
        # Public format used in the UI badges — kebab-case parts.
        # Direction is always present in the suffix.
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.label == f"{tag.volatility}-vol-{tag.trend}-{tag.direction}"

    @pytest.mark.parametrize("granularity", ["M1", "M10", "H1", "H4", "D"])
    def test_works_across_granularities(self, granularity):
        # Same shape of data, different granularities — should produce a
        # valid tag every time even when the annualisation factor differs
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity=granularity)
        assert isinstance(tag, RegimeTag)


class TestDirectionLabel:
    def test_strong_uptrend_data_labels_direction_up(self):
        # +200 bars × 1bps each = +2% net move. Above the 0.5% flat
        # threshold and positive, so direction must be "up".
        closes = [1.10 + 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.direction == "up"

    def test_strong_downtrend_data_labels_direction_down(self):
        closes = [1.20 - 0.0001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.direction == "down"

    def test_pure_oscillation_labels_direction_flat(self):
        # Sine wave around constant — net move ~ 0, well under the flat
        # threshold even at the noisy ends of the series
        closes = [1.10 + 0.001 * np.sin(i / 5.0) for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.direction == "flat"

    def test_tiny_drift_within_flat_band(self):
        # ~0.2% net move (below the 0.5% flat threshold). Should NOT be
        # labelled "up" — protects against false positives on barely-
        # moving choppy markets that just happened to close slightly up.
        closes = [1.10 + 0.00001 * i for i in range(200)]
        tag = compute_regime(_df_from_closes(closes), granularity="M1")
        assert tag.direction == "flat"


class TestRegimesMatch:
    def test_identical_tags_match(self):
        a = RegimeTag(volatility="med", trend="trending", direction="up")
        b = RegimeTag(volatility="med", trend="trending", direction="up")
        assert regimes_match(a, b) is True

    def test_different_vol_does_not_match(self):
        a = RegimeTag(volatility="low", trend="trending", direction="up")
        b = RegimeTag(volatility="high", trend="trending", direction="up")
        assert regimes_match(a, b) is False

    def test_different_trend_does_not_match(self):
        a = RegimeTag(volatility="med", trend="trending", direction="up")
        b = RegimeTag(volatility="med", trend="choppy", direction="up")
        assert regimes_match(a, b) is False

    def test_different_direction_does_not_match(self):
        # The whole point of adding direction — same vol+trend but
        # opposite drift should NOT count as the same regime. This is
        # exactly the OOS-2 vs OOS-3 pattern from the 2026-06-10
        # diagnostic that the prior tag missed.
        a = RegimeTag(volatility="low", trend="choppy", direction="down")
        b = RegimeTag(volatility="low", trend="choppy", direction="up")
        assert regimes_match(a, b) is False

    def test_mixed_trend_is_not_a_wildcard(self):
        # Conservative choice — see docstring on regimes_match
        a = RegimeTag(volatility="med", trend="mixed", direction="up")
        b = RegimeTag(volatility="med", trend="trending", direction="up")
        assert regimes_match(a, b) is False

    def test_flat_direction_is_not_a_wildcard(self):
        # "flat" must equal "flat" on both sides — it is not a
        # compatible-with-anything bucket
        a = RegimeTag(volatility="low", trend="choppy", direction="flat")
        b = RegimeTag(volatility="low", trend="choppy", direction="up")
        assert regimes_match(a, b) is False
