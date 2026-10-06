"""Unit tests for `agentfx.agents.fleet.llm`.

The LLM helper is a thin wrapper around `anthropic.messages.parse`
— tests focus on the pieces we actually own: pricing, skipped-
result shape, structured-output extraction, and the never-raises
contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from agentfx.agents.fleet import llm as fleet_llm

pytestmark = pytest.mark.unit


class _DummySchema(BaseModel):
    text: str
    score: float


class TestPricing:
    """Verify USD costs match the catalog."""

    def test_haiku_input_output(self):
        usage = SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        # $1/M input + $5/M output = $6
        assert fleet_llm._price_call("claude-haiku-4-5", usage) == pytest.approx(6.0)

    def test_sonnet_5_rates(self):
        usage = SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        # $2/M input + $10/M output = $12
        assert fleet_llm._price_call("claude-sonnet-5", usage) == pytest.approx(12.0)

    def test_opus_5_rates(self):
        usage = SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        # $5/M input + $25/M output = $30
        assert fleet_llm._price_call("claude-opus-5", usage) == pytest.approx(30.0)

    def test_cache_read_is_discounted(self):
        """Cache reads should bill at 10% of base input rate."""
        usage = SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        # Haiku base $1/M → cache read $0.10/M → 1M tokens = $0.10
        assert fleet_llm._price_call("claude-haiku-4-5", usage) == pytest.approx(0.10)

    def test_unknown_model_defaults_to_opus_5_rates(self):
        """Over-estimate not under — unknown IDs cost more, not less."""
        usage = SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        # Opus 5 input = $5/M
        assert fleet_llm._price_call("claude-mystery-99", usage) == pytest.approx(5.0)


class TestSkippedResult:
    def test_no_api_key_returns_skipped_result(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="sys",
            user="user",
            schema=_DummySchema,
        )
        assert result.output is None
        assert result.skipped_reason == "no_api_key"
        assert result.usd_cost == 0.0

    def test_api_failure_returns_skipped_result(self, monkeypatch):
        """SDK exception must not propagate — orchestrator depends
        on this never raising."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        broken_client = MagicMock()
        broken_client.messages.parse.side_effect = RuntimeError("api melted")

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="sys",
            user="user",
            schema=_DummySchema,
            client=broken_client,
        )
        assert result.output is None
        assert result.skipped_reason == "api_failure"


class TestExtraction:
    def test_pulls_output_parsed_attribute(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        parsed = _DummySchema(text="hello", score=0.42)
        resp = MagicMock()
        resp.output_parsed = parsed
        resp.content = []
        resp.usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        client = MagicMock()
        client.messages.parse.return_value = resp

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="s",
            user="u",
            schema=_DummySchema,
            client=client,
        )
        assert result.output is parsed
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.usd_cost > 0

    def test_pulls_parsed_from_content_block(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        parsed = _DummySchema(text="from_block", score=0.9)
        resp = MagicMock()
        resp.output_parsed = None
        resp.parsed = None
        resp.content = [SimpleNamespace(parsed=parsed, text=None)]
        resp.usage = SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        client = MagicMock()
        client.messages.parse.return_value = resp

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="s",
            user="u",
            schema=_DummySchema,
            client=client,
        )
        assert result.output is parsed

    def test_pulls_reasoning_text(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        resp = MagicMock()
        resp.output_parsed = _DummySchema(text="x", score=0.1)
        resp.content = [SimpleNamespace(text="my reasoning", parsed=None)]
        resp.usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        client = MagicMock()
        client.messages.parse.return_value = resp

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="s",
            user="u",
            schema=_DummySchema,
            client=client,
        )
        assert result.reasoning == "my reasoning"
