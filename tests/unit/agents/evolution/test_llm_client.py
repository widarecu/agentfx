"""LLM hypothesis injector tests with a fully mocked Anthropic SDK."""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import pytest

from agentfx.agents.evolution.llm_client import (
    LLMCallCost,
    LLMHypothesisInjector,
    MODEL_PRICING,
    _price_call,
)
from agentfx.agents.evolution.prompts import (
    SYSTEM_PROMPT,
    LLMEntryConditions,
    LLMStrategySpec,
    ProposedGenome,
    ProposeStrategyResponse,
    build_user_prompt,
    to_full_spec,
)
from agentfx.core.dsl.grammar import (
    Comparison,
    ExitRules,
    IndicatorCall,
    SizeRules,
)
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers — build mock SDK responses
# ─────────────────────────────────────────────────────────────────────────


def _llm_proposed(rsi_threshold: float = 35.0) -> ProposedGenome:
    """Build a valid LLM-flat proposal (RSI long under threshold)."""
    return ProposedGenome(
        spec=LLMStrategySpec(
            entry=LLMEntryConditions(
                long=Comparison(
                    lhs=IndicatorCall(ind="rsi", args=[14]),
                    cmp="<",
                    rhs=rsi_threshold,
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        ),
        reasoning=(
            "Mean-reversion long when RSI dips below the threshold; "
            "small stop, modest target, 4h holding cap."
        ),
    )


def _make_mock_response(
    *,
    proposals: list[ProposedGenome] | None = None,
    input_tokens: int = 200,
    output_tokens: int = 1500,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 4000,
):
    """Build a MagicMock that quacks like an Anthropic Message response."""
    if proposals is None:
        proposals = [_llm_proposed()]

    response = MagicMock()
    response.parsed_output = ProposeStrategyResponse(genomes=proposals)
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.usage.cache_creation_input_tokens = cache_creation_input_tokens
    response.usage.cache_read_input_tokens = cache_read_input_tokens
    return response


def _make_mock_client(parse_return=None, parse_side_effect=None) -> MagicMock:
    """Build a MagicMock anthropic client that supports `with_options()`
    chaining and `messages.parse()`."""
    client = MagicMock(spec=anthropic.Anthropic)
    # with_options() returns a configured view of the client; the SDK
    # uses it to apply per-call overrides like max_retries.
    client.with_options = MagicMock(return_value=client)
    if parse_side_effect is not None:
        client.messages.parse = MagicMock(side_effect=parse_side_effect)
    else:
        client.messages.parse = MagicMock(
            return_value=parse_return or _make_mock_response()
        )
    return client


# ─────────────────────────────────────────────────────────────────────────
# Pricing
# ─────────────────────────────────────────────────────────────────────────


class TestPricing:
    def test_sonnet_46_baseline_cost(self) -> None:
        # 1M input tokens × $3 = $3, 1M output × $15 = $15 → $18 total
        usage = MagicMock(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        cost = _price_call("claude-sonnet-4-6", usage)
        assert cost == pytest.approx(18.00)

    def test_cache_read_is_one_tenth_input(self) -> None:
        # 1M cache-read tokens at 0.1× input = $0.30
        usage = MagicMock(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        cost = _price_call("claude-sonnet-4-6", usage)
        assert cost == pytest.approx(0.30)

    def test_cache_write_is_one_and_a_quarter_input(self) -> None:
        # 1M cache-creation tokens at 1.25× input = $3.75
        usage = MagicMock(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=0,
        )
        cost = _price_call("claude-sonnet-4-6", usage)
        assert cost == pytest.approx(3.75)

    def test_haiku_is_cheaper_than_sonnet(self) -> None:
        usage = MagicMock(
            input_tokens=10_000,
            output_tokens=10_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        sonnet_cost = _price_call("claude-sonnet-4-6", usage)
        haiku_cost = _price_call("claude-haiku-4-5", usage)
        assert haiku_cost < sonnet_cost
        assert haiku_cost == pytest.approx(0.06)  # 1×10K + 5×10K = 60K @ $1/M = $0.06

    def test_unknown_model_falls_back_to_sonnet_pricing(self) -> None:
        # Better to over-estimate cost than to silently bypass the budget.
        usage = MagicMock(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        unknown_cost = _price_call("claude-totally-fake-model-99", usage)
        sonnet_cost = _price_call("claude-sonnet-4-6", usage)
        assert unknown_cost == pytest.approx(sonnet_cost)


# ─────────────────────────────────────────────────────────────────────────
# LLMCallCost
# ─────────────────────────────────────────────────────────────────────────


class TestLLMCallCost:
    def test_cache_hit_rate_zero_when_no_input(self) -> None:
        cost = LLMCallCost(0, 0, 0, 0, 0.0)
        assert cost.cache_hit_rate == 0.0

    def test_cache_hit_rate_full_cache(self) -> None:
        cost = LLMCallCost(0, 100, 0, 1000, 0.5)
        assert cost.cache_hit_rate == pytest.approx(1.0)

    def test_cache_hit_rate_mixed(self) -> None:
        # 200 uncached + 4000 read → 4000/4200 ≈ 0.952
        cost = LLMCallCost(200, 100, 0, 4000, 0.5)
        assert cost.cache_hit_rate == pytest.approx(4000 / 4200)


# ─────────────────────────────────────────────────────────────────────────
# Successful injection
# ─────────────────────────────────────────────────────────────────────────


class TestInject:
    def test_returns_proposals_on_success(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        result = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=5,
            fitness_history=[0.8, 1.0, 1.1, 1.1, 1.2],
            top_genomes=[(MA_CROSSOVER_SEED, 1.2), (RSI_MEAN_REVERSION_SEED, 0.9)],
            n_proposals=1,
        )
        assert result.succeeded is True
        assert result.skipped_reason is None
        assert len(result.proposals) == 1
        assert result.proposals[0].reasoning  # non-empty

    def test_call_id_is_unique_per_call(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        result_a = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        result_b = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        assert result_a.call_id != result_b.call_id

    def test_passes_system_prompt_with_cache_control(self) -> None:
        # Verifies the static block is sent with cache_control:ephemeral —
        # without this the static prompt wouldn't actually be cached.
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )

        kwargs = client.messages.parse.call_args.kwargs
        system = kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == SYSTEM_PROMPT

    def test_passes_user_message_with_rolling_state(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        injector.inject(
            instrument="GBP_USD",
            granularity="M5",
            generation_index=12,
            fitness_history=[1.0, 1.1, 1.15],
            top_genomes=[(MA_CROSSOVER_SEED, 1.42)],
            n_proposals=3,
        )

        kwargs = client.messages.parse.call_args.kwargs
        user_content = kwargs["messages"][0]["content"]
        assert "GBP_USD" in user_content
        assert "M5" in user_content
        assert "GENERATION: 12" in user_content
        assert "1.420" in user_content  # fitness from top_genomes

    def test_passes_output_format_pydantic_class(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        kwargs = client.messages.parse.call_args.kwargs
        assert kwargs["output_format"] is ProposeStrategyResponse


# ─────────────────────────────────────────────────────────────────────────
# Cost tracking + budget enforcement
# ─────────────────────────────────────────────────────────────────────────


class TestCostTracking:
    def test_cost_accumulates_across_calls(self) -> None:
        client = _make_mock_client(
            parse_return=_make_mock_response(
                input_tokens=1000, output_tokens=500, cache_read_input_tokens=0
            )
        )
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        before = injector.cost_used_usd
        injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        first = injector.cost_used_usd
        injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=1,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        second = injector.cost_used_usd

        assert first > before
        assert second > first
        # Each call: 1000×$3/M + 500×$15/M = $0.003 + $0.0075 = $0.0105
        assert first == pytest.approx(0.0105)
        assert second == pytest.approx(0.0210)

    def test_call_count_tracks_calls(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)
        assert injector.call_count == 0
        for _ in range(3):
            injector.inject(
                instrument="EUR_USD",
                granularity="M1",
                generation_index=0,
                fitness_history=[],
                top_genomes=[],
                n_proposals=1,
            )
        assert injector.call_count == 3


class TestBudgetEnforcement:
    def test_low_budget_blocks_first_call(self) -> None:
        client = _make_mock_client()
        # $0.01 budget — one Sonnet call's worst-case projection blows past it
        injector = LLMHypothesisInjector(client=client, budget_usd=0.01)

        result = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        assert result.succeeded is False
        assert result.skipped_reason == "budget_exhausted"
        assert result.proposals == []
        # Must NOT have hit the SDK
        client.messages.parse.assert_not_called()

    def test_budget_exhaustion_after_successful_calls(self) -> None:
        # Configure a budget that allows ~3 calls, then verify the 4th is
        # blocked. Each mock call costs ~$0.105 (10000 in/5000 out tokens).
        client = _make_mock_client(
            parse_return=_make_mock_response(
                input_tokens=10000,
                output_tokens=5000,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
        )
        # Budget of $0.50 — projected worst-case ($0.10 per call) means ~5 calls
        injector = LLMHypothesisInjector(
            client=client, budget_usd=0.50, max_output_tokens=5000
        )

        succeeded_calls = 0
        skipped_calls = 0
        for _ in range(10):
            result = injector.inject(
                instrument="EUR_USD",
                granularity="M1",
                generation_index=0,
                fitness_history=[],
                top_genomes=[],
                n_proposals=1,
            )
            if result.succeeded:
                succeeded_calls += 1
            else:
                assert result.skipped_reason == "budget_exhausted"
                skipped_calls += 1

        assert succeeded_calls > 0
        assert skipped_calls > 0
        # Total cost actually incurred must not exceed budget
        assert injector.cost_used_usd <= injector.budget_usd

    def test_cost_remaining_decreases_with_use(self) -> None:
        client = _make_mock_client()
        injector = LLMHypothesisInjector(client=client, budget_usd=5.0)
        before_remaining = injector.cost_remaining_usd
        injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        after_remaining = injector.cost_remaining_usd
        assert after_remaining < before_remaining


# ─────────────────────────────────────────────────────────────────────────
# Failure handling
# ─────────────────────────────────────────────────────────────────────────


class TestApiFailure:
    def test_rate_limit_error_returns_skipped_result(self) -> None:
        # Build a synthetic RateLimitError using the SDK's typed exception
        rate_limit_err = anthropic.RateLimitError(
            message="rate limit",
            response=MagicMock(),
            body=None,
        )
        client = _make_mock_client(parse_side_effect=rate_limit_err)
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)

        result = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        assert result.succeeded is False
        assert result.skipped_reason == "api_failure"
        assert result.proposals == []

    def test_internal_server_error_returns_skipped_result(self) -> None:
        server_err = anthropic.InternalServerError(
            message="500", response=MagicMock(), body=None
        )
        client = _make_mock_client(parse_side_effect=server_err)
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)
        result = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=1,
        )
        assert result.skipped_reason == "api_failure"


# ─────────────────────────────────────────────────────────────────────────
# Spec conversion
# ─────────────────────────────────────────────────────────────────────────


class TestSpecConversion:
    def test_to_full_spec_round_trips(self) -> None:
        llm_spec = _llm_proposed().spec
        full = to_full_spec(llm_spec)
        # Must be a valid full StrategySpec usable downstream
        from agentfx.core.dsl import compile_spec

        compile_spec(full)

    def test_proposals_returned_with_full_specs(self) -> None:
        client = _make_mock_client(
            parse_return=_make_mock_response(
                proposals=[_llm_proposed(35.0), _llm_proposed(25.0)]
            )
        )
        injector = LLMHypothesisInjector(client=client, budget_usd=10.0)
        result = injector.inject(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=2,
        )
        assert len(result.proposals) == 2
        # Full StrategySpec is what compile_spec consumes downstream
        from agentfx.core.dsl import StrategySpec, compile_spec

        for proposal in result.proposals:
            assert isinstance(proposal.spec, StrategySpec)
            compile_spec(proposal.spec)


# ─────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────


class TestUserPromptBuilder:
    def test_includes_all_required_sections(self) -> None:
        prompt = build_user_prompt(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=10,
            fitness_history=[0.5, 0.7, 1.1, 1.2, 1.25],
            top_genomes=[
                (MA_CROSSOVER_SEED, 1.25),
                (RSI_MEAN_REVERSION_SEED, 1.1),
                (DONCHIAN_BREAKOUT_SEED, 0.95),
            ],
            n_proposals=5,
        )
        assert "INSTRUMENT: EUR_USD M1" in prompt
        assert "GENERATION: 10" in prompt
        # Enriched prompt now annotates the top-genomes section with
        # the metric-breakdown qualifier; check for the stable prefix.
        assert "TOP 3 GENOMES" in prompt
        assert "5 fresh strategies" in prompt
        # Fitness values appear
        assert "1.250" in prompt
        assert "1.100" in prompt

    def test_handles_empty_history(self) -> None:
        prompt = build_user_prompt(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=0,
            fitness_history=[],
            top_genomes=[],
            n_proposals=3,
        )
        assert "GENERATION: 0" in prompt
        assert "(none yet — generation 0)" in prompt

    def test_truncates_history_to_last_six(self) -> None:
        # Long history — only last 6 should appear in the trajectory line
        long_history = [float(i) / 10 for i in range(20)]  # 0.0 .. 1.9
        prompt = build_user_prompt(
            instrument="EUR_USD",
            granularity="M1",
            generation_index=20,
            fitness_history=long_history,
            top_genomes=[],
            n_proposals=1,
        )
        # First five values shouldn't appear (they're 0.000..0.400)
        assert "0.000" not in prompt
        assert "0.100" not in prompt
        # Last six should
        for v in long_history[-6:]:
            assert f"{v:.3f}" in prompt
