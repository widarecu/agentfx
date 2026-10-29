"""Shared Anthropic SDK helper for the v0.8.x Agent Fleet.

A thin wrapper around `anthropic.messages.parse` (structured outputs
via Pydantic) with:
- Prompt caching on the static system block (saves 50-70% on
  repeat PM / QA / RA calls per Agent 3 research).
- Per-call USD accounting from `response.usage` → recorded in
  `agent_decisions` by the caller.
- Model routing driven by `settings.AGENT_<ROLE>_MODEL` env vars.
- Honest fallback: on API failure / budget skip, return an
  `LLMCallResult` whose `output` is None so the caller can emit a
  deterministic brief instead of crashing the orchestrator loop.

Why a fleet-local module and not reuse `evolution.llm_client`?
Different needs. Evolution's client owns a per-run budget state
and a 300-line Pydantic schema for genome injection. Fleet agents
each have their own schema, their budget is enforced globally by
the orchestrator (not per-call), and they want a smaller surface.

Keeping the two modules separate also means the evolution loop
can keep its stable Sonnet 4.6 default while the fleet uses the
current-generation Sonnet 5 / Haiku 4.5 / Opus 5 models.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, TypeVar

try:
    import anthropic
except ImportError:  # pragma: no cover — the SDK is in requirements
    anthropic = None  # type: ignore

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ─────────────────────────────────────────────────────────────────────
# Model pricing (per the Claude API model table, 2026-10)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token USD pricing. Mirrors the evolution module's
    helper but with the current-generation entries the fleet needs."""

    input_per_mtok: float
    output_per_mtok: float

    @property
    def cache_read_per_mtok(self) -> float:
        """Cache reads cost ~10% of the base input rate."""
        return self.input_per_mtok * 0.1

    @property
    def cache_write_per_mtok(self) -> float:
        """First write to cache costs ~1.25× the base input rate."""
        return self.input_per_mtok * 1.25


MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-haiku-4-5": ModelPricing(input_per_mtok=1.00, output_per_mtok=5.00),
    "claude-sonnet-4-6": ModelPricing(input_per_mtok=3.00, output_per_mtok=15.00),
    "claude-sonnet-5": ModelPricing(input_per_mtok=2.00, output_per_mtok=10.00),
    "claude-opus-4-7": ModelPricing(input_per_mtok=5.00, output_per_mtok=25.00),
    "claude-opus-4-8": ModelPricing(input_per_mtok=5.00, output_per_mtok=25.00),
    "claude-opus-5": ModelPricing(input_per_mtok=5.00, output_per_mtok=25.00),
}


def _price_call(model: str, usage: Any) -> float:
    """Compute USD cost from an SDK usage object.

    Falls back to the Opus 5 rate if the model is unknown — better
    to over-estimate cost than silently let an unrecognised model
    bypass the daily cap.
    """
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-opus-5"])
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        (input_tokens * pricing.input_per_mtok)
        + (output_tokens * pricing.output_per_mtok)
        + (cache_creation * pricing.cache_write_per_mtok)
        + (cache_read * pricing.cache_read_per_mtok)
    ) / 1_000_000.0


# ─────────────────────────────────────────────────────────────────────
# Result wrapper
# ─────────────────────────────────────────────────────────────────────


@dataclass
class LLMCallResult:
    """What the fleet LLM helper returns on every call.

    `output` is None when the call was skipped (no API key) or
    failed (SDK exception). Callers must branch on that and either
    retry later or fall back to a deterministic alternative — the
    orchestrator loop must never crash because of an LLM error.

    Typed as a BaseModel subclass at the call site via `structured_call`'s
    `schema=` argument; the attribute itself is untyped on the
    dataclass to keep Python 3.11 compatibility (PEP 695 generic
    class syntax is 3.12+).
    """

    output: BaseModel | None
    reasoning: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    usd_cost: float
    skipped_reason: str | None = None  # "no_api_key" | "api_failure" | None


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def structured_call(
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int = 2048,
    client: Any = None,
) -> LLMCallResult:
    """Make one structured Claude call for a fleet agent.

    `schema` is a Pydantic model class; the returned `output` is an
    instance of that class (or None on failure).

    `system` is cached (prompt-caching on the static block), so
    repeat calls across days reuse the prefix cheaply. `user` is
    always fresh — put the volatile per-call context there.

    Prompt caching requires the system block to be a list of
    content parts, not a plain string. We build that shape here so
    callers can pass a plain string.
    """
    if os.environ.get("ANTHROPIC_API_KEY") is None and client is None:
        logger.info(
            "fleet.llm: no ANTHROPIC_API_KEY set; skipping call model=%s", model
        )
        return LLMCallResult(
            output=None,
            reasoning="",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            usd_cost=0.0,
            skipped_reason="no_api_key",
        )

    if client is None:
        if anthropic is None:
            logger.warning("fleet.llm: anthropic SDK unavailable")
            return _skipped_result(model, "no_api_key")
        # SDK retries on 408/409/429/5xx with jittered exponential
        # backoff. Bumped from 2 → 4 (Wilson 2026-10-22 — QA fell
        # back to deterministic on a transient Anthropic blip).
        # 4 retries covers ~1–2 min of API trouble before the SDK
        # gives up; the application-level retry wrapper
        # (`structured_call_with_backoff`) covers the longer tail.
        client = anthropic.Anthropic(max_retries=4)

    try:
        # Use streaming (not `messages.parse`) because Sonnet 5 with
        # adaptive extended thinking + a large `max_tokens` budget
        # can produce responses the SDK estimates > 10 min, and the
        # non-streaming `.parse()` path raises
        # `ValueError: Streaming is required...` pre-flight (seen in
        # Railway logs 2026-10-27T10:41Z). `.stream()` is a context
        # manager that returns the final completed message via
        # `.get_final_message()`, with the same shape as
        # `.parse()` returned — so `_extract_parsed` / `_extract_reasoning`
        # continue to work unchanged downstream.
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        ) as stream:
            resp = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 — logged + wrapped
        # For a Pydantic ValidationError, `exc.errors()` returns a
        # structured list of (loc, msg, type, input) tuples that
        # names the failing field + value. str(exc) gives a
        # human-readable summary but truncates per-field detail.
        # We log both so Railway log review can see which field
        # Haiku/Sonnet broke on without a second round trip.
        # Previous version (2026-10-07) logged only str(exc) and
        # we lost ~15 min diagnosing a schema mismatch each time.
        errors_detail: Any = None
        try:
            errors_detail = exc.errors()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        logger.warning(
            "fleet.llm: structured call failed model=%s err_type=%s err=%s "
            "pydantic_errors=%s",
            model,
            type(exc).__name__,
            exc,
            errors_detail,
        )
        return _skipped_result(model, "api_failure")

    output = _extract_parsed(resp, schema)
    reasoning = _extract_reasoning(resp)

    # When extraction returned None despite a successful API call,
    # dump the response shape so the next investigation has evidence.
    # This fires only on the failing path — healthy calls incur zero
    # extra logging.
    if output is None:
        stop_reason = getattr(resp, "stop_reason", None)
        content = getattr(resp, "content", None) or []
        content_summary: list[dict[str, Any]] = []
        for i, block in enumerate(content[:3]):
            block_text = getattr(block, "text", None)
            content_summary.append(
                {
                    "idx": i,
                    "type": getattr(block, "type", type(block).__name__),
                    "text_head": (
                        (block_text[:500] + "…")
                        if isinstance(block_text, str) and len(block_text) > 500
                        else block_text
                    ),
                    "has_parsed_attr": hasattr(block, "parsed"),
                    "has_input_attr": hasattr(block, "input"),
                }
            )
        logger.warning(
            "fleet.llm: extraction returned None for %s — stop_reason=%s "
            "parsed_output_is=%r content_blocks=%d first_blocks=%s",
            schema.__name__,
            stop_reason,
            getattr(resp, "parsed_output", "<missing>"),
            len(content),
            content_summary,
        )

    usage = getattr(resp, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    usd = _price_call(model, usage) if usage is not None else 0.0

    return LLMCallResult(
        output=output,
        reasoning=reasoning,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        usd_cost=usd,
    )


def _skipped_result(model: str, reason: str) -> LLMCallResult:
    return LLMCallResult(
        output=None,
        reasoning="",
        model=model,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        usd_cost=0.0,
        skipped_reason=reason,
    )


# Default backoff schedule for `structured_call_with_backoff`: three
# attempts after the first (fourth attempt), spaced to cover ~7
# minutes of Anthropic trouble. The SDK's own `max_retries=4` already
# covers ~1–2 min of 429/5xx bursts; this wraps a longer tail so a
# brief outage doesn't force QA into the deterministic fallback that
# surfaces to Wilson as "LLM analysis unavailable".
_DEFAULT_BACKOFF_SECONDS = (30.0, 90.0, 270.0)


def structured_call_with_backoff(
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int = 2048,
    client: Any = None,
    backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
    retry_on_extract_none: bool = False,
) -> LLMCallResult:
    """`structured_call` with application-level retry on transient
    API failure.

    The SDK already retries 429/5xx inside each call (max_retries=4).
    This wrapper adds an outer loop that retries the WHOLE call on
    `skipped_reason="api_failure"` with escalating backoff, so a
    short Anthropic outage doesn't push callers into deterministic
    fallback.

    - `no_api_key` is NOT retried (config is unlikely to change
      inside the call window).
    - `api_failure` IS retried.
    - `extract_none` (model returned 200 but malformed output) is
      only retried if `retry_on_extract_none=True` — defaults to
      False because a schema-mismatch is deterministic, retrying
      just wastes cost.

    Preserves the signature of `structured_call` for the caller.
    """
    import time

    attempt = 0
    result = structured_call(
        model=model,
        system=system,
        user=user,
        schema=schema,
        max_tokens=max_tokens,
        client=client,
    )
    while attempt < len(backoff_seconds):
        reason = result.skipped_reason
        retryable = reason == "api_failure" or (
            retry_on_extract_none and reason == "extract_none"
        )
        if result.output is not None or not retryable:
            return result
        wait = backoff_seconds[attempt]
        logger.warning(
            "fleet.llm: retrying after %s (model=%s, attempt=%d/%d, wait=%.1fs)",
            reason,
            model,
            attempt + 1,
            len(backoff_seconds),
            wait,
        )
        time.sleep(wait)
        attempt += 1
        result = structured_call(
            model=model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            client=client,
        )
    return result


def _extract_parsed(resp: Any, schema: type[T]) -> T | None:
    """Pull the parsed Pydantic object out of a `messages.parse` response.

    Known locations across SDK versions:
    - `response.parsed_output` — Anthropic SDK ≥0.90 (current).
    - `response.output_parsed` — alternate attribute name used in
      some docs; checked for forward-compatibility.
    - `response.parsed` — legacy direct name.
    - `response.content[i].parsed` / `.input` — some codepaths put
      the parsed object on the content block itself.
    - Fallback: parse the JSON text content ourselves (the SDK
      serialises the structured output into a text block's `.text`
      field as JSON).

    Logs the schema-validation error on failure (as WARNING, so the
    QA tech-health scan sees it later) instead of silently swallowing
    it. A silent swallow burned us on 2026-10-06 when Sonnet 5's
    output for QADivergenceReport returned 200 OK but failed Pydantic
    validation; the caller just got None and fell back to the stub.
    """
    # Preferred: top-level attributes.
    for attr in ("parsed_output", "output_parsed", "parsed"):
        direct = getattr(resp, attr, None)
        if isinstance(direct, schema):
            return direct
        if isinstance(direct, dict):
            try:
                return schema.model_validate(direct)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fleet.llm: schema validation failed at resp.%s for %s: %s",
                    attr,
                    schema.__name__,
                    exc,
                )

    # Fallback: inspect content blocks.
    import json as _json

    content = getattr(resp, "content", None) or []
    for i, block in enumerate(content):
        for attr in ("parsed", "input"):
            parsed = getattr(block, attr, None)
            if isinstance(parsed, schema):
                return parsed
            if isinstance(parsed, dict):
                try:
                    return schema.model_validate(parsed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "fleet.llm: schema validation failed at content[%d].%s "
                        "for %s: %s",
                        i,
                        attr,
                        schema.__name__,
                        exc,
                    )
                    continue
        # Last resort: the SDK serialises structured output into the
        # text block as a JSON string.
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip().startswith(("{", "[")):
            try:
                return schema.model_validate(_json.loads(text))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fleet.llm: schema validation failed at content[%d].text "
                    "(JSON parse) for %s: %s — raw text head: %r",
                    i,
                    schema.__name__,
                    exc,
                    text[:200],
                )
                continue
    logger.warning(
        "fleet.llm: could not extract %s from response — no known location "
        "returned a valid instance (resp attrs=%s, content blocks=%d)",
        schema.__name__,
        [a for a in ("parsed_output", "output_parsed", "parsed") if hasattr(resp, a)],
        len(content),
    )
    return None


def _extract_reasoning(resp: Any) -> str:
    """Pull any plain text out of a response for the reasoning field.

    Fleet agents log the LLM's prose reasoning into
    `agent_decisions.reasoning_text` for Reflexion to re-read. If
    the response is pure structured output with no text blocks,
    returns an empty string.
    """
    content = getattr(resp, "content", None) or []
    chunks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "\n".join(chunks)


__all__ = [
    "LLMCallResult",
    "ModelPricing",
    "MODEL_PRICING",
    "structured_call",
    "structured_call_with_backoff",
]
