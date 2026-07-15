"""Unit tests for the currency-aware position sizing helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.services.paper_sizing import (
    _pip_size_for_pair,
    compute_position_units,
)

pytestmark = pytest.mark.unit


class TestPipSize:
    def test_major_returns_0_0001(self):
        assert _pip_size_for_pair("EUR_USD") == 0.0001

    def test_jpy_pair_returns_0_01(self):
        assert _pip_size_for_pair("USD_JPY") == 0.01
        assert _pip_size_for_pair("EUR_JPY") == 0.01


class TestComputePositionUnits:
    """The formula must match backtest.py::_dsl_position_size so paper
    P&L is comparable to a strategy's evolution-time backtest metrics."""

    def test_usd_account_eur_usd_no_conversion(self):
        """Same currency as quote → conversion rate 1.0, no OANDA call."""
        client = MagicMock()
        result = compute_position_units(
            instrument="EUR_USD",
            balance=10_000.0,
            account_currency="USD",
            risk_pct=0.005,
            stop_pips=20,
            safety_cap_units=None,
            client=client,
        )
        # $10k × 0.005 / (20 × 0.0001) = 25_000
        assert result.capped_units == 25_000
        assert result.desired_units == 25_000
        assert result.quote_to_account_rate == 1.0
        assert result.notes == "ok"
        client.get_pricing.assert_not_called()

    def test_aud_account_eur_usd_converts_via_aud_usd(self):
        """AUD account trading EUR/USD → need USD→AUD rate. OANDA
        lists AUD_USD (base=AUD, quote=USD); helper inverts to get
        USD→AUD. Wilson's staging Practice account is AUD, so this
        path is load-bearing."""
        client = MagicMock()
        # Primary lookup USD_AUD would fail (not tradable on OANDA);
        # fall through to AUD_USD.
        client.get_pricing.side_effect = [
            Exception("USD_AUD not available"),
            [{"bid": 0.66, "ask": 0.66}],
        ]
        result = compute_position_units(
            instrument="EUR_USD",
            balance=100_000.0,
            account_currency="AUD",
            risk_pct=0.005,
            stop_pips=22,
            safety_cap_units=None,
            client=client,
        )
        # A$100k × 0.005 = A$500 risk. USD→AUD = 1/0.66 ≈ 1.515.
        # pip_value_aud = 0.0001 × 1.515 ≈ 0.0001515 AUD/unit.
        # desired = 500 / (22 × 0.0001515) ≈ 150_015.
        assert 149_000 <= result.capped_units <= 151_000
        assert result.quote_to_account_rate == pytest.approx(1.0 / 0.66, rel=0.001)
        assert result.notes == "ok"

    def test_safety_cap_clamps_desired(self):
        """When desired exceeds the cap, cap wins and note flips."""
        client = MagicMock()
        result = compute_position_units(
            instrument="EUR_USD",
            balance=1_000_000.0,  # would want 2.5M units
            account_currency="USD",
            risk_pct=0.005,
            stop_pips=20,
            safety_cap_units=500_000,
            client=client,
        )
        assert result.desired_units == 2_500_000
        assert result.capped_units == 500_000
        assert "capped_at_safety_limit" in result.notes

    def test_zero_stop_pips_returns_zero(self):
        """Degenerate input — caller must not divide by zero.
        Sizing returns 0 with a note; runner skips the trade."""
        client = MagicMock()
        result = compute_position_units(
            instrument="EUR_USD",
            balance=10_000.0,
            account_currency="USD",
            risk_pct=0.005,
            stop_pips=0,
            safety_cap_units=None,
            client=client,
        )
        assert result.capped_units == 0
        assert result.notes == "degenerate_inputs"

    def test_missing_conversion_raises(self):
        """If OANDA can't quote either direction of the conversion
        pair, we refuse to size rather than guess."""
        client = MagicMock()
        client.get_pricing.side_effect = Exception("pricing down")
        with pytest.raises(ValueError, match="no OANDA pricing"):
            compute_position_units(
                instrument="EUR_USD",
                balance=10_000.0,
                account_currency="ZWL",  # nonsense currency
                risk_pct=0.005,
                stop_pips=20,
                safety_cap_units=None,
                client=client,
            )
