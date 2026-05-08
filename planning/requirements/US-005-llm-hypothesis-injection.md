# US-005: LLM-Driven Hypothesis Injection

**Version:** v0.3.0 - Autonomous Strategy Loop
**Priority:** P0 - Must Have
**Status:** TODO

## Given-When-Then

**GIVEN** an evolution run is in progress with a population of `StrategySpec` genomes evolving via GA
  **AND** every N=3 generations the orchestrator triggers an LLM injection step
  **AND** valid Anthropic API credentials are configured (`ANTHROPIC_API_KEY`)
  **AND** the run's accumulated LLM cost is below `EVOLUTION_LLM_BUDGET_USD`
**WHEN** the LLM hypothesis injector is invoked with the current top-10 genomes, recent fitness trajectory, and instrument context
**THEN** the LLM client:
1. Calls Claude (default `claude-sonnet-4-6`) with prompt caching enabled — the static block (indicator catalog, JSON Schema, instructions) is cached as a system prompt
2. Uses tool-use against a `propose_strategy` tool whose input schema is the exported `StrategySpec` JSON Schema
3. Returns a list of N≈5 fresh genomes that are guaranteed schema-valid by construction
4. Each returned genome is recorded with `origin='llm'`, the LLM's reasoning text, and a reference to the `llm_call_id`
5. Tracks cost and aborts the call cleanly if the per-run budget would be exceeded
6. On total failure (network, rate limit, unrecoverable error) the GA proceeds without injection that generation — the orchestrator is never blocked by LLM unavailability

## Acceptance Criteria

- [ ] `LLMHypothesisInjector` class with `inject(top_genomes: list[StrategySpec], fitness_history: list[float], context: InstrumentContext) -> list[ProposedGenome]`
- [ ] Anthropic SDK (`anthropic>=0.40`) wired with `claude-sonnet-4-6` as default; `EVOLUTION_LLM_MODEL` env override
- [ ] Prompt caching: static block (indicator catalog spec, schema, instructions, example I/O) sent as a `cache_control: ephemeral` system prompt; only rolling state (top genomes JSON, fitness trajectory, instrument) varies between calls
- [ ] Cache hit-rate measured and emitted as a Prometheus metric — must be ≥ 0.7 across a real run (steady state)
- [ ] Structured output via Anthropic tool-use against a `propose_strategy` tool whose schema = `StrategySpec.model_json_schema()`
- [ ] Hard per-run cost cap enforced *before* each call — if next call's projected max cost would exceed remaining budget, return empty list (skip injection that generation) and log a warning
- [ ] Exponential-backoff retries (3 attempts) on transient errors (rate limit, 5xx, network)
- [ ] LLM reasoning text persisted on each proposed genome (used by portal `GenomeDetail` page when `origin='llm'`)
- [ ] `llm_call_id` is unique and stored on `generation` row + each `strategy_genome` row produced by that call
- [ ] Cost tracking: tokens (input cached/uncached, output) and USD cost computed from current Anthropic pricing, accumulated on `evolution_run.llm_cost_usd`

## Prompt Template (sketch)

System prompt (cached):
```
You are a quantitative strategist proposing FX trading strategies as JSON
specs against a fixed grammar. You have access to one tool, `propose_strategy`,
which validates and accepts each proposal.

INDICATOR CATALOG: {sma, ema, rsi, atr, bb_upper, bb_lower with bounds...}

GRAMMAR: {full StrategySpec JSON Schema}

GUIDELINES:
- Propose strategies STRUCTURALLY DISSIMILAR to the current top-10
- Favor combinations the GA is unlikely to discover via local mutation
- Stop_pips < target_pips; risk_pct ≤ 0.005; bound respect mandatory
- Reasoning must be ≤ 80 words and grounded in the provided fitness history

EXAMPLES: {1-2 worked examples}
```

User prompt (uncached, varies per call):
```
INSTRUMENT: {pair} {granularity}
GENERATION: {n} of run {run_id}
FITNESS TRAJECTORY (last 6 generations): {array}
CURRENT TOP 10 (DSL specs + OOS Sharpe): {json}

Propose 5 fresh strategies, each with brief reasoning.
```

## Files Involved

### Create
- `src/agentfx/agents/evolution/__init__.py`
- `src/agentfx/agents/evolution/llm_client.py` — `LLMHypothesisInjector`, `ProposedGenome` dataclass, cost accountant
- `src/agentfx/agents/evolution/prompts.py` — system prompt template, user prompt builder, schema export helper
- `tests/unit/agents/evolution/test_llm_client.py` — Anthropic SDK mocked end-to-end; budget enforcement; retry behaviour; structured-output validation; cache-control header check
- `tests/integration/evolution/test_llm_injection_real.py` — gated by `@pytest.mark.live_llm`, makes one real Anthropic call (manual run pre-release)

### Modify
- `requirements.in` — add `anthropic>=0.40`
- `src/agentfx/api/core/config.py` — add `ANTHROPIC_API_KEY`, `EVOLUTION_LLM_BUDGET_USD` (default 5.00), `EVOLUTION_LLM_MODEL` (default `claude-sonnet-4-6`)

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | _required_ | Authenticates SDK calls |
| `EVOLUTION_LLM_BUDGET_USD` | `5.00` | Hard per-run cost cap |
| `EVOLUTION_LLM_MODEL` | `claude-sonnet-4-6` | Default model. `claude-haiku-4-5-20251001` for cheap diversity variant |
| `EVOLUTION_LLM_CACHE_TTL` | `300` | Prompt cache TTL in seconds (Anthropic default) |

## Verification Commands

```bash
# Unit tests with mocked Anthropic SDK
make test-file FILE=tests/unit/agents/evolution/test_llm_client.py

# Live LLM smoke (requires real ANTHROPIC_API_KEY in .env.test)
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -m live_llm tests/integration/evolution/test_llm_injection_real.py -v
```

## Dependencies

- US-006 (Strategy DSL) — schema export feeds the tool-use definition
- Anthropic Python SDK `>=0.40`
- Existing Prometheus metrics surface at [src/agentfx/api/core/metrics.py](../../src/agentfx/api/core/metrics.py)

## Notes

- **Prompt caching is not optional** — without it, cost would scale linearly per call. With it, the static block is paid once per cache TTL window. Per the project's claude-api skill guidance.
- The schema exported to the tool definition is the SAME `StrategySpec` Pydantic model used by the compiler. There is one source of truth for the grammar, and the LLM cannot return anything that the compiler can't consume.
- Cost cap enforcement uses *projected max cost* (worst-case input + max_output_tokens at full price), not actual cost. Better to skip a call than to exceed budget and have to refund.
- Diversity-injection variant (`claude-haiku-4-5-20251001`) is a P2 enhancement — useful when you want frequent low-cost seeding rather than infrequent expensive proposals.
- The LLM never executes code, never touches the database directly, and never sees real-money state. It only proposes specs that pass the same compiler-level safety checks the GA outputs go through.
