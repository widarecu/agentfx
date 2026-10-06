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
        client = anthropic.Anthropic(max_retries=2)

    try:
        resp = client.messages.parse(
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
        )
    except Exception as exc:  # noqa: BLE001 — logged + wrapped
        logger.warning(
            "fleet.llm: structured call failed model=%s err=%s",
            model,
            exc,
        )
        return _skipped_result(model, "api_failure")

    output = _extract_parsed(resp, schema)
    reasoning = _extract_reasoning(resp)
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
]
