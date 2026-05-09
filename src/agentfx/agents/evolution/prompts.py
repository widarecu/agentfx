"""Prompt templates and the LLM-facing response schema.

The LLM is asked to propose strategy genomes that conform to a Pydantic
schema. We keep the LLM-facing schema *flatter* than the full DSL —
specifically, entry conditions are single comparisons rather than nested
AND/OR — because Anthropic's structured outputs do not support recursive
JSON Schemas. The GA can still evolve flat comparisons into compound ones
later via mutation; gen-0 LLM proposals are deliberately simple.

The system prompt is split into a static block (catalog, schema sketch,
worked examples, hard rules) that gets cached, and a per-call user
message (top genomes, fitness trajectory, instrument context) that varies.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, Field

from agentfx.core.dsl.grammar import (
    Comparison,
    ComparatorName,
    EntryConditions,
    ExitRules,
    Expr,
    SizeRules,
    StrategySpec,
)
from agentfx.core.dsl.indicators import INDICATOR_ARG_BOUNDS

# ─────────────────────────────────────────────────────────────────────────
# LLM-facing response schema (flattened — no recursive AND/OR)
# ─────────────────────────────────────────────────────────────────────────


class LLMEntryConditions(BaseModel):
    """LLM-facing entry conditions — flat single comparisons only.

    The GA can later mutate these into nested AND/OR compositions; v1 LLM
    proposals stay flat to keep the structured-output schema non-recursive.
    """

    long: Comparison | None = None
    short: Comparison | None = None


class LLMStrategySpec(BaseModel):
    """LLM-facing version of `StrategySpec` with flat entry conditions."""

    entry: LLMEntryConditions
    exit: ExitRules
    size: SizeRules


class ProposedGenome(BaseModel):
    """A single LLM-proposed genome with its rationale."""

    spec: LLMStrategySpec
    reasoning: Annotated[
        str,
        Field(
            description=(
                "Brief (≤ 80 words) rationale grounded in the provided fitness "
                "history and top genomes. Explain *why* this proposal is likely "
                "to outperform or diversify the current population."
            )
        ),
    ]


class ProposeStrategyResponse(BaseModel):
    """The structured response from a hypothesis-injection call."""

    genomes: list[ProposedGenome] = Field(min_length=1, max_length=10)


def to_full_spec(llm_spec: LLMStrategySpec) -> StrategySpec:
    """Convert an LLM-flat spec into a canonical full `StrategySpec`.

    The flattening is one-way at the type level (LLM gives us simple
    Comparisons; the full grammar wraps them as Conditions). The conversion
    is lossless because a single Comparison *is* a valid Condition.
    """
    return StrategySpec(
        entry=EntryConditions(
            long=llm_spec.entry.long,
            short=llm_spec.entry.short,
        ),
        exit=llm_spec.exit,
        size=llm_spec.size,
    )


# ─────────────────────────────────────────────────────────────────────────
# Static system prompt
# ─────────────────────────────────────────────────────────────────────────


def _format_indicator_catalog() -> str:
    """Render the indicator catalog as a human-readable bullet list for the
    system prompt. Uses INDICATOR_ARG_BOUNDS so this stays in sync with the
    DSL automatically."""
    lines = []
    for name, bounds in INDICATOR_ARG_BOUNDS.items():
        if not bounds:
            lines.append(f"- `{name}()` — close-price series")
        else:
            arg_descs = []
            for i, (lo, hi) in enumerate(bounds):
                arg_descs.append(f"arg{i} ∈ [{lo}, {hi}]")
            lines.append(f"- `{name}({', '.join(arg_descs)})`")
    return "\n".join(lines)


_INDICATOR_PURPOSES = """\
- `close()`           — current close price (no args)
- `sma(n)`            — simple moving average over n bars
- `ema(n)`            — exponential moving average, span n
- `rsi(n)`            — relative strength index, period n; oscillator in [0, 100]
- `atr(n)`            — average true range over n bars (price units)
- `bb_upper(n, k)`    — Bollinger upper band: sma(n) + k·stdev(n)
- `bb_lower(n, k)`    — Bollinger lower band: sma(n) − k·stdev(n)
- `donchian_high(n)`  — running max of prior n highs (excludes current bar)
- `donchian_low(n)`   — running min of prior n lows (excludes current bar)
"""


_COMPARATOR_HELP = """\
- `>`, `<`, `>=`, `<=`  — bar-by-bar scalar comparisons
- `crosses_above`        — lhs[t-1] ≤ rhs[t-1] AND lhs[t] > rhs[t]
- `crosses_below`        — lhs[t-1] ≥ rhs[t-1] AND lhs[t] < rhs[t]
"""


def _example_spec_json() -> str:
    """A worked example rendered as JSON for the system prompt."""
    example = {
        "spec": {
            "entry": {
                "long": {
                    "lhs": {"ind": "ema", "args": [20]},
                    "cmp": "crosses_above",
                    "rhs": {"ind": "ema", "args": [50]},
                },
                "short": {
                    "lhs": {"ind": "rsi", "args": [14]},
                    "cmp": ">",
                    "rhs": 70,
                },
            },
            "exit": {
                "stop_pips": 15,
                "target_pips": 30,
                "max_hold_bars": 240,
                "opposite_signal": True,
            },
            "size": {"risk_pct": 0.005},
        },
        "reasoning": (
            "Trend-follow long via fast/slow EMA cross; mean-revert short on "
            "RSI overbought. Asymmetric — long with momentum, short against "
            "extension. Plausible on EUR_USD M1 in trend regimes."
        ),
    }
    return json.dumps(example, indent=2)


SYSTEM_PROMPT = f"""\
You are a quantitative strategist proposing FX trading strategies as
structured JSON genomes for an evolutionary search loop. Each generation
the genetic algorithm shows you the current top performers and recent
fitness trajectory, and asks you to propose novel strategies that are
*structurally dissimilar* to the current population — so the GA can
explore beyond local mutation.

# Indicator catalogue

You may reference these indicators on either side of any comparison.
Argument bounds are enforced at compile time; values outside the bounds
will cause your proposal to be rejected.

{_INDICATOR_PURPOSES}

Specifically (machine-readable bounds):

{_format_indicator_catalog()}

# Comparators

{_COMPARATOR_HELP}

# Hard rules (your proposals MUST satisfy these)

1. `stop_pips` MUST be strictly less than `target_pips` (negative
   expectancy by construction otherwise).
2. `risk_pct` MUST be in [0.001, 0.005].
3. `max_hold_bars` MUST be in [1, 2880].
4. Each comparison MUST reference at least one indicator on lhs or rhs
   (no comparing two scalar literals).
5. `crosses_above` / `crosses_below` MUST be used with at least one
   indicator side (a series can only cross another series or a level).
6. RSI ranges 0–100; ATR is in price units; SMA/EMA/BB/Donchian are price
   levels. Comparing RSI to a price-scale indicator is nonsensical —
   compare RSI to a numeric literal in [0, 100] instead.
7. Each entry condition is a single comparison (no AND/OR — the GA will
   compose those later if useful).
8. Either `entry.long` or `entry.short` MUST be non-null (or both).

# Reasoning

For each proposal, include a short rationale (≤ 80 words) grounded in the
provided fitness history and top genomes. Reference *why* this proposal
is likely to outperform or diversify the current population — e.g.
"current top is RSI mean-reversion, propose breakout to cover a different
regime", "fitness has plateaued for 3 generations, try a tighter stop".

# Worked example

```json
{_example_spec_json()}
```

# What you are NOT being asked to do

- Do not write Python code; only emit structured JSON conforming to the
  response schema.
- Do not invent new indicator names; use only the catalogue above.
- Do not propose strategies identical or near-identical to the current
  top genomes (the GA already has those covered by elitism + mutation).
"""


# ─────────────────────────────────────────────────────────────────────────
# Per-call user prompt
# ─────────────────────────────────────────────────────────────────────────


def build_user_prompt(
    instrument: str,
    granularity: str,
    generation_index: int,
    fitness_history: list[float],
    top_genomes: list[tuple[StrategySpec, float | None]],
    n_proposals: int,
) -> str:
    """Render the per-call user prompt.

    Args:
        instrument: e.g. "EUR_USD"
        granularity: e.g. "M1"
        generation_index: 0-based current generation number
        fitness_history: best-fitness per generation, oldest-first
        top_genomes: list of (spec, fitness) tuples for the current top-K
        n_proposals: how many genomes to propose
    """
    fitness_line = (
        ", ".join(f"{f:.3f}" if f is not None else "—" for f in fitness_history[-6:])
        if fitness_history
        else "—"
    )

    top_lines = []
    for i, (spec, fitness) in enumerate(top_genomes, start=1):
        fit_str = f"{fitness:.3f}" if fitness is not None else "n/a"
        # Compact JSON representation
        spec_json = json.dumps(spec.model_dump(mode="json"), separators=(",", ":"))
        top_lines.append(f"#{i} (Sharpe {fit_str}): {spec_json}")
    top_block = "\n".join(top_lines) if top_lines else "(none yet — generation 0)"

    return f"""\
INSTRUMENT: {instrument} {granularity}
GENERATION: {generation_index}
FITNESS TRAJECTORY (last 6 generations, OOS Sharpe): {fitness_line}

CURRENT TOP {len(top_genomes)} GENOMES:
{top_block}

Propose exactly {n_proposals} fresh strategies, each with a brief reasoning.
Make them structurally dissimilar to the genomes above and to one another.
Return them as a JSON object matching the response schema (a `genomes`
array of `{{spec, reasoning}}` objects)."""


__all__ = [
    "LLMEntryConditions",
    "LLMStrategySpec",
    "ProposedGenome",
    "ProposeStrategyResponse",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "to_full_spec",
]


# Resolve forward references — not strictly needed since Comparison is
# already concrete, but keeps the import-graph predictable.
_ = Expr
_ = ComparatorName
