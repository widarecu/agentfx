"""Unit tests for the currency-aware position sizing helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentfx.services.paper_sizing import (
    _pip_size_for_pair,
    compute_failsafe_sl_pips,
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


class TestComputeFailsafeSLPips:
    """Wilson 2026-08-15 audit: broker order carries only a wide
    fail-safe SL sized from the hard-halt loss cap. The DSL's own
    `stop_pips` is enforced walk-side on bar close."""

    def test_scales_with_kill_switch_dd(self):
        # $10k balance, 10k units EUR_USD (USD account),
        # kill_switch_dd_pct = 5% → max loss $500 → 500 pips
        # ($500 / (10_000 × 0.0001) = 500).
        pips = compute_failsafe_sl_pips(
            balance=10_000.0,
            units=10_000,
            pip_value_account=0.0001,  # pip_size × 1.0 (USD/USD)
            kill_switch_dd_pct=5.0,
        )
        assert pips == 500

    def test_scales_with_position_size(self):
        # Same balance and DD cap; 25_000 units → tighter fail-safe.
        # $500 / (25_000 × 0.0001) = 200 pips (well above the 100 floor).
        pips = compute_failsafe_sl_pips(
            balance=10_000.0,
            units=25_000,
            pip_value_account=0.0001,
            kill_switch_dd_pct=5.0,
        )
        assert pips == 200

    def test_min_pips_floor_prevents_broker_from_beating_walk(self):
        """If the computed fail-safe would be tighter than the DSL stop
        (typical for large positions), the floor keeps the broker from
        firing before the walk's own close-based check gets a chance."""
        pips = compute_failsafe_sl_pips(
            balance=10_000.0,
            units=1_000_000,  # very large → computed ~5 pips
            pip_value_account=0.0001,
            kill_switch_dd_pct=5.0,
        )
        assert pips == 100  # floored

    def test_aud_account_higher_pip_value_reduces_pips(self):
        # AUD-denominated account, EUR_USD trade. USD/AUD ≈ 1.50 (AUD is
        # weaker), so pip_value_account = 0.0001 × 1.50 = 0.00015. Loss
        # in AUD per pip per unit is higher → fewer pips to hit cap.
        pips = compute_failsafe_sl_pips(
            balance=10_000.0,  # AUD
            units=10_000,
            pip_value_account=0.00015,
            kill_switch_dd_pct=5.0,
        )
        # $500 / (10_000 × 0.00015) = 333.33 → 333
        assert pips == 333

    def test_degenerate_units_returns_floor(self):
        assert (
            compute_failsafe_sl_pips(
                balance=10_000.0,
                units=0,
                pip_value_account=0.0001,
                kill_switch_dd_pct=5.0,
            )
            == 100
        )

    def test_degenerate_pip_value_returns_floor(self):
        assert (
            compute_failsafe_sl_pips(
                balance=10_000.0,
                units=10_000,
                pip_value_account=0.0,
                kill_switch_dd_pct=5.0,
            )
            == 100
        )

    def test_zero_dd_cap_returns_floor(self):
        assert (
            compute_failsafe_sl_pips(
                balance=10_000.0,
                units=10_000,
                pip_value_account=0.0001,
                kill_switch_dd_pct=0.0,
            )
            == 100
        )

    def test_custom_min_pips(self):
        pips = compute_failsafe_sl_pips(
            balance=10_000.0,
            units=1_000_000,
            pip_value_account=0.0001,
            kill_switch_dd_pct=5.0,
            min_pips=250,
        )
        assert pips == 250
