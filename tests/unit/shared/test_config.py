"""Unit tests for src/agentfx/shared/config.py.

The module is a collection of pure env-var helpers + a config loader.
Coverage is straightforward: invoke each helper with the env set and
unset, verify defaults, and verify load_config's INGEST_* parsing.

These tests are pure (no DB, no Docker, no real OANDA) — they only
manipulate os.environ via monkeypatch and assert return values.
"""

from __future__ import annotations

import pytest

from agentfx.shared.config import (
    IngestConfig,
    MAJOR_PAIRS,
    backtest_default_slippage_pips,
    backtest_default_spread_pips,
    env_bool,
    env_float,
    env_int,
    env_str,
    load_config,
    oanda_account_id,
    oanda_api_host,
    oanda_api_key,
    oanda_env,
    policy_default_daily_loss_threshold,
    policy_default_max_abs_position,
    policy_default_max_trades_per_hour,
)


def _clear(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Ensure each env var is unset for the duration of the test."""
    for n in names:
        monkeypatch.delenv(n, raising=False)


@pytest.mark.unit
class TestEnvStr:
    def test_returns_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        assert env_str("FOO") == "bar"

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "FOO")
        assert env_str("FOO", "fallback") == "fallback"

    def test_returns_none_when_unset_and_no_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear(monkeypatch, "FOO")
        assert env_str("FOO") is None


@pytest.mark.unit
class TestEnvInt:
    def test_parses_valid_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "42")
        assert env_int("FOO") == 42

    def test_returns_default_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Invalid string falls back to the default — silently, matching
        # the helper's contract that env config never raises on parse
        # failure (so a typo in a Railway dashboard var doesn't crash
        # the whole worker; it logs the wrong value at most).
        monkeypatch.setenv("FOO", "not-a-number")
        assert env_int("FOO", 7) == 7

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "FOO")
        assert env_int("FOO", 99) == 99

    def test_returns_none_when_unset_and_no_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear(monkeypatch, "FOO")
        assert env_int("FOO") is None


@pytest.mark.unit
class TestEnvBool:
    @pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "t", "yes", "Y", "on"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
        monkeypatch.setenv("FOO", truthy)
        assert env_bool("FOO") is True

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "anything-else"])
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, falsy: str) -> None:
        monkeypatch.setenv("FOO", falsy)
        assert env_bool("FOO") is False

    def test_uses_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "FOO")
        assert env_bool("FOO", True) is True
        assert env_bool("FOO", False) is False


@pytest.mark.unit
class TestEnvFloat:
    def test_parses_valid_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "1.25")
        assert env_float("FOO") == 1.25

    def test_returns_default_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "not-a-float")
        assert env_float("FOO", 3.14) == 3.14

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "FOO")
        assert env_float("FOO", 0.5) == 0.5

    def test_returns_none_when_unset_and_no_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear(monkeypatch, "FOO")
        assert env_float("FOO") is None


@pytest.mark.unit
class TestLoadConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "INGEST_PAIRS", "INGEST_START_DAYS", "INGEST_FORCE_START")
        cfg = load_config()
        assert isinstance(cfg, IngestConfig)
        assert cfg.pairs == MAJOR_PAIRS
        assert cfg.start_days == 365
        assert cfg.force_start is False

    def test_overrides_pairs_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "INGEST_START_DAYS", "INGEST_FORCE_START")
        monkeypatch.setenv("INGEST_PAIRS", " EURUSD ,GBPUSD,  ")
        cfg = load_config()
        # Empty entries trimmed; surrounding whitespace stripped — important
        # so a Railway dashboard value pasted with a trailing comma doesn't
        # become a phantom empty-symbol ingest.
        assert cfg.pairs == ["EURUSD", "GBPUSD"]

    def test_overrides_start_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "INGEST_PAIRS", "INGEST_FORCE_START")
        monkeypatch.setenv("INGEST_START_DAYS", "30")
        cfg = load_config()
        assert cfg.start_days == 30

    def test_overrides_force_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "INGEST_PAIRS", "INGEST_START_DAYS")
        monkeypatch.setenv("INGEST_FORCE_START", "true")
        cfg = load_config()
        assert cfg.force_start is True


@pytest.mark.unit
class TestBrokerHelpers:
    """Each helper just wraps env_str with a documented default — verify
    both the unset-default and override paths so the OANDA env wiring
    can be debugged without spelunking through the broker adapter."""

    def test_oanda_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "OANDA_ENV")
        assert oanda_env() == "practice"

    def test_oanda_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OANDA_ENV", "live")
        assert oanda_env() == "live"

    def test_oanda_api_host_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "OANDA_API_HOST")
        assert oanda_api_host() == "api-fxpractice.oanda.com"

    def test_oanda_api_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "OANDA_API_KEY")
        assert oanda_api_key() is None

    def test_oanda_api_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OANDA_API_KEY", "test-key")
        assert oanda_api_key() == "test-key"

    def test_oanda_account_id_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "OANDA_ACCOUNT_ID")
        assert oanda_account_id() is None


@pytest.mark.unit
class TestBacktestDefaults:
    def test_spread_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "BACKTEST_SPREAD_PIPS")
        assert backtest_default_spread_pips() == 0.5

    def test_spread_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKTEST_SPREAD_PIPS", "1.2")
        assert backtest_default_spread_pips() == 1.2

    def test_slippage_default_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Default 0.0 was a deliberate change — the previous 0.1-pip
        # default was empirically phantom cost (see docstring). The
        # `or 0.0` guard ensures a malformed env var doesn't somehow
        # produce None.
        _clear(monkeypatch, "BACKTEST_SLIPPAGE_PIPS")
        assert backtest_default_slippage_pips() == 0.0

    def test_slippage_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKTEST_SLIPPAGE_PIPS", "0.3")
        assert backtest_default_slippage_pips() == 0.3


@pytest.mark.unit
class TestPolicyDefaults:
    def test_max_abs_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "POLICY_MAX_ABS_POSITION")
        assert policy_default_max_abs_position() == 1
        monkeypatch.setenv("POLICY_MAX_ABS_POSITION", "3")
        assert policy_default_max_abs_position() == 3

    def test_max_trades_per_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "POLICY_MAX_TRADES_PER_HOUR")
        assert policy_default_max_trades_per_hour() == 60
        monkeypatch.setenv("POLICY_MAX_TRADES_PER_HOUR", "100")
        assert policy_default_max_trades_per_hour() == 100

    def test_daily_loss_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear(monkeypatch, "POLICY_DAILY_LOSS_THRESHOLD")
        assert policy_default_daily_loss_threshold() == 0.02
        monkeypatch.setenv("POLICY_DAILY_LOSS_THRESHOLD", "0.05")
        assert policy_default_daily_loss_threshold() == 0.05
