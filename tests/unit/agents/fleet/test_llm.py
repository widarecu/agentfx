"""Unit tests for `agentfx.agents.fleet.llm`.

The LLM helper wraps `anthropic.messages.stream(...).get_final_message()`
(switched from the sync `.parse()` call on 2026-10-27 after Sonnet 5
+ 32768 max_tokens tripped the SDK's 10-min pre-flight check). Tests
focus on the pieces we actually own: pricing, skipped-result shape,
structured-output extraction, and the never-raises contract.
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


def _wire_stream(client, *, resp=None, exc=None):
    """Wire `client.messages.stream(...)` to behave as a context
    manager whose `get_final_message()` returns `resp` or raises
    `exc`. Mirrors how the production code consumes the stream."""
    cm = MagicMock()
    inner = MagicMock()
    if exc is not None:
        inner.get_final_message.side_effect = exc
    else:
        inner.get_final_message.return_value = resp
    cm.__enter__.return_value = inner
    cm.__exit__.return_value = False
    client.messages.stream.return_value = cm
    return cm


def _wire_stream_sequence(client, items):
    """Per-call side_effect for `client.messages.stream`. Each item
    in `items` is either a response (returned) or an Exception
    (raised when the context manager entry runs). Used for multi-
    attempt backoff tests."""

    def _side_effect(*_args, **_kwargs):
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        cm = MagicMock()
        inner = MagicMock()
        inner.get_final_message.return_value = item
        cm.__enter__.return_value = inner
        cm.__exit__.return_value = False
        return cm

    client.messages.stream.side_effect = _side_effect


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
        _wire_stream(broken_client, exc=RuntimeError("api melted"))

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="sys",
            user="user",
            schema=_DummySchema,
            client=broken_client,
        )
        assert result.output is None
        assert result.skipped_reason == "api_failure"

    def test_uses_streaming_not_parse(self, monkeypatch):
        """AFX-41 regression: `structured_call` must use
        `messages.stream(...)` (context manager) and never
        `messages.parse(...)`. The non-streaming path hits the
        SDK's 10-min pre-flight gate for Sonnet 5 with
        max_tokens=32768."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        parsed = _DummySchema(text="x", score=0.1)
        resp = MagicMock()
        resp.output_parsed = parsed
        resp.content = []
        resp.usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        client = MagicMock()
        _wire_stream(client, resp=resp)

        result = fleet_llm.structured_call(
            model="claude-sonnet-5",
            system="s",
            user="u",
            schema=_DummySchema,
            max_tokens=32768,
            client=client,
        )
        assert result.output is parsed
        # stream was called with the long-request-safe path
        assert client.messages.stream.called
        # parse was NEVER called
        assert not client.messages.parse.called


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
        _wire_stream(client, resp=resp)

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
        _wire_stream(client, resp=resp)

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
        _wire_stream(client, resp=resp)

        result = fleet_llm.structured_call(
            model="claude-haiku-4-5",
            system="s",
            user="u",
            schema=_DummySchema,
            client=client,
        )
        assert result.reasoning == "my reasoning"


class TestStructuredCallWithBackoff:
    """App-level retry on `api_failure` — covers Wilson's 2026-10-22
    observation where QA fell back to deterministic on a transient
    Anthropic blip."""

    def _mk_resp(self, parsed) -> MagicMock:
        resp = MagicMock()
        resp.output_parsed = parsed
        resp.content = []
        resp.usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return resp

    def test_success_on_first_try_no_retry(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        parsed = _DummySchema(text="ok", score=1.0)
        client = MagicMock()
        _wire_stream(client, resp=self._mk_resp(parsed))
        slept: list[float] = []
        with patch("time.sleep", side_effect=slept.append):
            result = fleet_llm.structured_call_with_backoff(
                model="claude-haiku-4-5",
                system="s",
                user="u",
                schema=_DummySchema,
                client=client,
            )
        assert result.output is parsed
        assert slept == []  # No retry needed → no sleep
        assert client.messages.stream.call_count == 1

    def test_api_failure_then_success(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        parsed = _DummySchema(text="finally", score=0.9)
        client = MagicMock()
        # First call raises → api_failure. Second call succeeds.
        _wire_stream_sequence(
            client,
            [
                RuntimeError("502 bad gateway"),
                self._mk_resp(parsed),
            ],
        )
        slept: list[float] = []
        with patch("time.sleep", side_effect=slept.append):
            result = fleet_llm.structured_call_with_backoff(
                model="claude-sonnet-5",
                system="s",
                user="u",
                schema=_DummySchema,
                client=client,
                backoff_seconds=(0.01, 0.02, 0.03),
            )
        assert result.output is parsed
        assert len(slept) == 1
        assert slept[0] == 0.01
        assert client.messages.stream.call_count == 2

    def test_persistent_api_failure_exhausts_retries(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        client = MagicMock()
        _wire_stream(client, exc=RuntimeError("still 500"))
        slept: list[float] = []
        with patch("time.sleep", side_effect=slept.append):
            result = fleet_llm.structured_call_with_backoff(
                model="claude-sonnet-5",
                system="s",
                user="u",
                schema=_DummySchema,
                client=client,
                backoff_seconds=(0.01, 0.02, 0.03),
            )
        assert result.output is None
        assert result.skipped_reason == "api_failure"
        # Initial attempt + 3 retries = 4 calls; 3 sleeps between.
        assert client.messages.stream.call_count == 4
        assert slept == [0.01, 0.02, 0.03]

    def test_extract_none_is_not_retried_by_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        resp = MagicMock()
        # No parsed anywhere → extract_none path.
        resp.output_parsed = None
        resp.parsed_output = None
        resp.parsed = None
        resp.content = []
        resp.usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        client = MagicMock()
        _wire_stream(client, resp=resp)
        slept: list[float] = []
        with patch("time.sleep", side_effect=slept.append):
            result = fleet_llm.structured_call_with_backoff(
                model="claude-haiku-4-5",
                system="s",
                user="u",
                schema=_DummySchema,
                client=client,
                backoff_seconds=(0.01, 0.02, 0.03),
            )
        assert result.output is None
        # No retry — extract_none is deterministic, retrying wastes cost.
        assert client.messages.stream.call_count == 1
        assert slept == []

    def test_no_api_key_short_circuits_without_retry(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        slept: list[float] = []
        with patch("time.sleep", side_effect=slept.append):
            result = fleet_llm.structured_call_with_backoff(
                model="claude-haiku-4-5",
                system="s",
                user="u",
                schema=_DummySchema,
                backoff_seconds=(0.01, 0.02),
            )
        assert result.output is None
        assert result.skipped_reason == "no_api_key"
        assert slept == []
