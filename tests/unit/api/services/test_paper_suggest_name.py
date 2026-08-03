"""Unit tests for `paper.suggest_deployment_name` (v0.6.2).

Two layers under test:

1. `_sanitize_suggested_name` — pure string trimming. Locks the
   contract against LLM misbehaviour (surrounding quotes, trailing
   punctuation, second-line rambling, length cap).

2. `suggest_deployment_name` — the service function. Mocked
   `anthropic.Anthropic` client so we don't hit the real API. Covers:
     - happy path (Haiku returns "MACD Momentum Rider" → passthrough)
     - LLM adds noise (trailing period, wrapping quotes) → sanitized
     - LLM returns empty → RuntimeError so the router 502s
     - ANTHROPIC_API_KEY missing → RuntimeError
     - genome not owned by caller → ValueError bubbles from
       `_load_owned_validated_genome` (router maps to 400)
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from agentfx.api.services.paper import (
    _sanitize_suggested_name,
    suggest_deployment_name,
)


# ─────────────────────────────────────────────────────────────────────
# _sanitize_suggested_name
# ─────────────────────────────────────────────────────────────────────


class TestSanitize:
    def test_passthrough_clean_name(self):
        assert _sanitize_suggested_name("MACD Momentum Rider") == "MACD Momentum Rider"

    def test_strips_surrounding_double_quotes(self):
        assert (
            _sanitize_suggested_name('"MACD Momentum Rider"') == "MACD Momentum Rider"
        )

    def test_strips_surrounding_single_quotes(self):
        assert (
            _sanitize_suggested_name("'MACD Momentum Rider'") == "MACD Momentum Rider"
        )

    def test_strips_surrounding_backticks(self):
        assert (
            _sanitize_suggested_name("`MACD Momentum Rider`") == "MACD Momentum Rider"
        )

    def test_strips_trailing_period(self):
        assert _sanitize_suggested_name("MACD Momentum Rider.") == "MACD Momentum Rider"

    def test_strips_multi_trailing_punctuation(self):
        assert (
            _sanitize_suggested_name("MACD Momentum Rider!!") == "MACD Momentum Rider"
        )

    def test_takes_only_first_nonempty_line(self):
        raw = "\n\nMACD Momentum Rider\n\nThis strategy uses MACD crossovers."
        assert _sanitize_suggested_name(raw) == "MACD Momentum Rider"

    def test_caps_at_max_length(self):
        raw = "A" * 200
        assert len(_sanitize_suggested_name(raw)) == 60

    def test_empty_returns_empty(self):
        assert _sanitize_suggested_name("") == ""
        assert _sanitize_suggested_name("   \n\n  ") == ""


# ─────────────────────────────────────────────────────────────────────
# suggest_deployment_name
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_genome_loader():
    """Patch _load_owned_validated_genome so we don't need a real DB.
    Yields the mock so individual tests can customize behaviour."""
    with patch("agentfx.api.services.paper._load_owned_validated_genome") as m:
        yield m


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Inject a fake `anthropic` module BEFORE the service's lazy
    import. The service does `import anthropic` inside the function
    body so we can slot in a stand-in via `sys.modules`.

    Yields the MagicMock client class so tests can configure the
    `.messages.create(...)` return value.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_module = types.ModuleType("anthropic")
    fake_client_cls = MagicMock()
    fake_client_instance = MagicMock()
    fake_client_cls.return_value = fake_client_instance
    fake_module.Anthropic = fake_client_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    yield fake_client_instance


def _make_llm_response(text: str) -> MagicMock:
    """Ape the anthropic SDK's response shape:
    `resp.content` is a list of blocks; each block has `.text`."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _make_fake_genome(dsl_json: dict | None = None) -> MagicMock:
    g = MagicMock()
    g.id = "g_test"
    g.dsl_json = dsl_json or {
        "entry": {"long": {"op": "gt", "left": "sma_20", "right": "sma_50"}},
        "exit": {"stop_pips": 22, "target_pips": 40},
    }
    return g


def _make_fake_user() -> MagicMock:
    user = MagicMock()
    user.id = 1
    return user


class TestSuggestDeploymentName:
    def test_happy_path_returns_llm_name(self, mock_anthropic, mock_genome_loader):
        mock_genome_loader.return_value = _make_fake_genome()
        mock_anthropic.messages.create.return_value = _make_llm_response(
            "MACD Momentum Rider"
        )
        name = suggest_deployment_name(
            db=MagicMock(),
            strategy_genome_id="g_test",
            current_user=_make_fake_user(),
        )
        assert name == "MACD Momentum Rider"
        # LLM was called with the Haiku model + short prompt containing DSL.
        args, kwargs = mock_anthropic.messages.create.call_args
        assert kwargs["model"] == "claude-haiku-4-5"
        assert "sma_20" in kwargs["messages"][0]["content"]

    def test_sanitizes_wrapping_quotes_and_period(
        self, mock_anthropic, mock_genome_loader
    ):
        mock_genome_loader.return_value = _make_fake_genome()
        mock_anthropic.messages.create.return_value = _make_llm_response(
            '"MACD Momentum Rider."'
        )
        name = suggest_deployment_name(
            db=MagicMock(),
            strategy_genome_id="g_test",
            current_user=_make_fake_user(),
        )
        assert name == "MACD Momentum Rider"

    def test_empty_llm_response_raises_runtime_error(
        self, mock_anthropic, mock_genome_loader
    ):
        mock_genome_loader.return_value = _make_fake_genome()
        mock_anthropic.messages.create.return_value = _make_llm_response("   ")
        with pytest.raises(RuntimeError, match="empty"):
            suggest_deployment_name(
                db=MagicMock(),
                strategy_genome_id="g_test",
                current_user=_make_fake_user(),
            )

    def test_missing_api_key_raises_runtime_error(
        self, monkeypatch, mock_genome_loader
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_genome_loader.return_value = _make_fake_genome()
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            suggest_deployment_name(
                db=MagicMock(),
                strategy_genome_id="g_test",
                current_user=_make_fake_user(),
            )

    def test_genome_owner_error_bubbles(self, mock_anthropic, mock_genome_loader):
        # e.g. genome not found / not owned by caller — the router
        # will map ValueError to 400.
        mock_genome_loader.side_effect = ValueError("genome not found")
        with pytest.raises(ValueError, match="genome not found"):
            suggest_deployment_name(
                db=MagicMock(),
                strategy_genome_id="nope",
                current_user=_make_fake_user(),
            )
        mock_anthropic.messages.create.assert_not_called()
