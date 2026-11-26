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
            entry_close=1.10,
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
            entry_close=1.10,
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
            entry_close=1.10,
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
            entry_close=1.10,
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
                entry_close=1.10,
                safety_cap_units=None,
                client=client,
            )

    @pytest.mark.parametrize(
        ("instrument", "entry_close", "currency", "quotes", "expected"),
        [
            ("EUR_USD", 1.12, "USD", [], 267_857),
            ("EUR_USD", 1.10, "AUD", [[], [{"bid": 0.66, "ask": 0.66}]], 180_000),
            ("USD_JPY", 150.0, "USD", [[], [{"bid": 150.0, "ask": 150.0}]], 300_000),
        ],
    )
    def test_leverage_clamps_in_account_currency(
        self, caplog, instrument, entry_close, currency, quotes, expected
    ):
        client = MagicMock()
        client.get_pricing.side_effect = quotes
        result = compute_position_units(
            instrument=instrument,
            balance=10_000,
            account_currency=currency,
            risk_pct=0.02,
            stop_pips=5,
            entry_close=entry_close,
            safety_cap_units=None,
            client=client,
        )
        assert result.desired_units > result.capped_units == expected
        assert result.notes == "capped_at_leverage_limit_30_to_1"
        assert "Paper leverage cap hit" in caplog.text


class TestComputeFailsafeSLPips:
    """AFX-104: broker order carries only a wide fail-safe SL sized
    from the genome's own `stop_pips` (3×, floored at 100 pips). The
    DSL stop itself is enforced walk-side on bar close."""

    def test_three_times_dsl_stop(self):
        assert compute_failsafe_sl_pips(stop_pips=50) == 150

    def test_wide_dsl_stop_scales(self):
        assert compute_failsafe_sl_pips(stop_pips=400) == 1200

    def test_min_pips_floor_prevents_broker_from_beating_walk(self):
        assert compute_failsafe_sl_pips(stop_pips=20) == 100

    def test_missing_stop_returns_floor(self):
        assert compute_failsafe_sl_pips(stop_pips=None) == 100
        assert compute_failsafe_sl_pips(stop_pips=0) == 100

    def test_custom_multiple_and_floor(self):
        assert compute_failsafe_sl_pips(stop_pips=50, multiple=2) == 100
        assert compute_failsafe_sl_pips(stop_pips=20, min_pips=250) == 250

    def test_not_sized_from_balance_or_kill_switch(self):
        import inspect

        params = inspect.signature(compute_failsafe_sl_pips).parameters
        assert "kill_switch_dd_pct" not in params
        assert "balance" not in params
