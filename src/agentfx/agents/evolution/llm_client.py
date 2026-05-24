"""Anthropic SDK wrapper for LLM hypothesis injection.

Wraps `client.messages.parse()` (structured outputs via Pydantic schema)
with three v0.3.0-specific concerns layered on top:

1. **Prompt caching** — the static system block (catalog, schema, rules,
   worked example) carries an `ephemeral` cache_control marker so it's
   paid for once per cache TTL window rather than once per call. Cache
   read tokens are tracked separately for cost accounting.

2. **Per-run budget enforcement** — every call accumulates cost into
   `cost_used_usd`. Before each call, projected max cost is checked
   against the configured budget; an over-budget call returns an empty
   `InjectionResult` rather than firing. The orchestrator continues the
   GA run without the LLM injection that generation.

3. **Lossy-but-graceful failure** — transient API failures (rate limits,
   5xx) propagate as the SDK's typed exceptions; the SDK retries
   internally per `max_retries`. On total failure the orchestrator
   catches and skips the generation.

Tested with a mocked `anthropic.Anthropic` client — see
`tests/unit/agents/evolution/test_llm_client.py`. The
`@pytest.mark.live_llm` integration test (US-005) hits the real API.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from agentfx.agents.evolution.prompts import (
    SYSTEM_PROMPT,
    ProposedGenome,
    ProposeStrategyResponse,
    build_user_prompt,
    to_full_spec,
)
from agentfx.core.dsl import StrategySpec

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Pricing
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token USD pricing for an Anthropic model.

    Cache read is 0.1× input; cache write is 1.25× input (5-min TTL).
    Output is the standard model output rate.
    """

    input_per_mtok: float
    output_per_mtok: float

    @property
    def cache_read_per_mtok(self) -> float:
        return self.input_per_mtok * 0.1

    @property
    def cache_write_per_mtok(self) -> float:
        return self.input_per_mtok * 1.25


# Aliases match the canonical IDs from the Claude model catalog. Use the
# alias (no date suffix) — `client.messages.parse(model=...)` accepts it.
MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(input_per_mtok=3.00, output_per_mtok=15.00),
    "claude-haiku-4-5": ModelPricing(input_per_mtok=1.00, output_per_mtok=5.00),
    "claude-opus-4-7": ModelPricing(input_per_mtok=5.00, output_per_mtok=25.00),
}


def _price_call(model: str, usage: Any) -> float:
    """Compute USD cost from an SDK usage object.

    Falls back to the Sonnet 4.6 rate if the model is unknown — better to
    over-estimate cost than to silently let an unrecognised model bypass
    the budget check.
    """
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-sonnet-4-6"])

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    cost = (
        (input_tokens * pricing.input_per_mtok)
        + (output_tokens * pricing.output_per_mtok)
        + (cache_creation * pricing.cache_write_per_mtok)
        + (cache_read * pricing.cache_read_per_mtok)
    ) / 1_000_000.0
    return cost


# ─────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class LLMCallCost:
    """Token + USD cost summary for a single LLM call."""

    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    usd: float

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache (0..1).

        Returns 0 when there are no input-side tokens.
        """
        total = self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens
        if total == 0:
            return 0.0
        return self.cache_read_tokens / total


@dataclass
class InjectionProposal:
    """An LLM-proposed genome plus its rationale.

    Distinct from `agents.evolution.population.Genome` — this is the raw
    LLM output, not yet wrapped in a Genome. The orchestrator converts
    these into Genomes (with origin=LLM) before adding to the population.
    """

    spec: StrategySpec
    reasoning: str


@dataclass
class InjectionResult:
    """The output of one `LLMHypothesisInjector.inject()` call."""

    proposals: list[InjectionProposal]
    cost: LLMCallCost
    call_id: str
    skipped_reason: str | None = None  # "budget_exhausted" | "api_failure" | None

    @property
    def succeeded(self) -> bool:
        return self.skipped_reason is None and len(self.proposals) > 0


# ─────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _BudgetState:
    """Mutable cost accumulator carried across calls within one injector."""

    used_usd: float = 0.0
    call_log: list[LLMCallCost] = field(default_factory=list)


class LLMHypothesisInjector:
    """LLM-driven hypothesis injection for the evolution loop.

    Owns one `anthropic.Anthropic` client + per-run budget state. Create
    one instance per evolution run; call `.inject(...)` every N
    generations from the orchestrator.

    Args:
        client: Pre-configured Anthropic SDK client. If None, one is
            created from the environment (uses ANTHROPIC_API_KEY).
        model: Model alias (default `claude-sonnet-4-6`). Alternates:
            `claude-haiku-4-5` for the cheap diversity variant,
            `claude-opus-4-7` for maximum capability.
        budget_usd: Hard per-injector cost cap. Calls that would breach
            it are skipped, not retried later.
        max_retries: SDK-level retry count for transient errors. The SDK
            handles 408, 409, 429, and 5xx with exponential backoff.
        max_output_tokens: Output budget per call. Used both as the API's
            `max_tokens` ceiling and as the basis for the projected
            worst-case-cost budget check.
    """

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        *,
        model: str = "claude-sonnet-4-6",
        budget_usd: float = 5.0,
        max_retries: int = 3,
        max_output_tokens: int = 4096,
    ) -> None:
        if client is None:
            client = anthropic.Anthropic(max_retries=max_retries)
        else:
            # Respect the caller's max_retries on a pre-built client by
            # using with_options() — this returns a new client view, doesn't
            # mutate the input. Safe even if max_retries already matches.
            client = client.with_options(max_retries=max_retries)

        self._client = client
        self.model = model
        self.budget_usd = float(budget_usd)
        self.max_output_tokens = int(max_output_tokens)
        self._state = _BudgetState()

    # ── Public state accessors ──────────────────────────────────────

    @property
    def cost_used_usd(self) -> float:
        return self._state.used_usd

    @property
    def cost_remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self._state.used_usd)

    @property
    def call_count(self) -> int:
        return len(self._state.call_log)

    # ── Budget gate ─────────────────────────────────────────────────

    def _projected_max_cost(self) -> float:
        """Worst-case cost of one more call: assume full uncached input
        + max output. We don't know the prompt size precisely (caching
        helps, schema overhead varies), so use a conservative upper
        bound: model's input price × 8000 prompt tokens (catalog +
        rolling state) at write rate, plus full output at output rate.

        The 8000-token estimate is well above what the static block
        plus a 5-genome rolling state actually takes; the goal here is
        to refuse a call that *might* breach budget rather than to be
        precise.
        """
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING["claude-sonnet-4-6"])
        worst_input_cost = (8000 * pricing.cache_write_per_mtok) / 1_000_000.0
        worst_output_cost = (
            self.max_output_tokens * pricing.output_per_mtok
        ) / 1_000_000.0
        return worst_input_cost + worst_output_cost

    def _budget_allows(self) -> bool:
        return (self._state.used_usd + self._projected_max_cost()) <= self.budget_usd

    # ── Main entry point ────────────────────────────────────────────

    def inject(
        self,
        *,
        instrument: str,
        granularity: str,
        generation_index: int,
        fitness_history: list[float],
        top_genomes: list[tuple[StrategySpec, float | None, dict | None]],
        n_proposals: int = 5,
        cost_per_trade_pips: float | None = None,
        prior_llm_proposals: (
            list[tuple[StrategySpec, float | None, dict | None]] | None
        ) = None,
        is_gen_zero_seeding: bool = False,
        min_cagr_target: float | None = None,
    ) -> InjectionResult:
        """Request `n_proposals` novel genomes from the LLM.

        Returns an `InjectionResult` with `succeeded=False` if the
        per-run budget would be exceeded or the call fails. The
        orchestrator should treat both as "no LLM injection this
        generation" and continue.

        The extra context fields (`cost_per_trade_pips`,
        `prior_llm_proposals`, `is_gen_zero_seeding`) enrich the user
        prompt so the model can diagnose what's failing and avoid
        repeating shapes that already lost. See
        `prompts.build_user_prompt` for the rendering.
        """
        call_id = uuid.uuid4().hex
        empty_cost = LLMCallCost(0, 0, 0, 0, 0.0)

        if not self._budget_allows():
            logger.warning(
                "llm_injector budget would be exceeded; skipping call "
                "(used=%.4f budget=%.4f projected=%.4f)",
                self._state.used_usd,
                self.budget_usd,
                self._projected_max_cost(),
            )
            return InjectionResult(
                proposals=[],
                cost=empty_cost,
                call_id=call_id,
                skipped_reason="budget_exhausted",
            )

        user_prompt = build_user_prompt(
            instrument=instrument,
            granularity=granularity,
            generation_index=generation_index,
            fitness_history=fitness_history,
            top_genomes=top_genomes,
            n_proposals=n_proposals,
            cost_per_trade_pips=cost_per_trade_pips,
            prior_llm_proposals=prior_llm_proposals,
            is_gen_zero_seeding=is_gen_zero_seeding,
            min_cagr_target=min_cagr_target,
        )

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_output_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
                output_format=ProposeStrategyResponse,
            )
        except anthropic.APIError:
            # Includes RateLimitError, APIStatusError (5xx, etc.). The
            # SDK has already retried per `max_retries`; if we're here
            # it's a hard failure. Don't tear down the run.
            logger.exception("llm_injector API call failed; skipping injection")
            return InjectionResult(
                proposals=[],
                cost=empty_cost,
                call_id=call_id,
                skipped_reason="api_failure",
            )

        cost = self._record_cost(response)

        # Convert LLM-flat specs to full StrategySpecs. If any one fails
        # validation against the full grammar's downstream constraints,
        # log and drop it — don't blow up the rest of the batch.
        proposals: list[InjectionProposal] = []
        parsed: ProposeStrategyResponse = response.parsed_output
        for proposed in parsed.genomes:
            try:
                full_spec = to_full_spec(proposed.spec)
            except ValidationError:
                logger.warning(
                    "llm_injector dropped a proposal that failed full-grammar "
                    "validation (call_id=%s)",
                    call_id,
                )
                continue
            proposals.append(
                InjectionProposal(spec=full_spec, reasoning=proposed.reasoning)
            )

        return InjectionResult(proposals=proposals, cost=cost, call_id=call_id)

    # ── Internal ────────────────────────────────────────────────────

    def _record_cost(self, response: Any) -> LLMCallCost:
        usage = getattr(response, "usage", None)
        usd = _price_call(self.model, usage) if usage is not None else 0.0

        cost = LLMCallCost(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            usd=usd,
        )
        self._state.used_usd += cost.usd
        self._state.call_log.append(cost)
        return cost


__all__ = [
    "InjectionProposal",
    "InjectionResult",
    "LLMCallCost",
    "LLMHypothesisInjector",
    "MODEL_PRICING",
    "ModelPricing",
    "ProposedGenome",
]
