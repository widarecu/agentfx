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
                "min_hold_bars": 10,
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
4. `min_hold_bars` is OPTIONAL but RECOMMENDED on minute-scale
   timeframes (M1/M5). MUST be in [0, max_hold_bars - 1]. Default 0
   matches legacy behaviour where any exit can fire on the very next
   bar after entry. Set this to 5–30 bars (or higher on M1) to
   suppress over-trading — every closed trade pays 0.5–1.8 pips of
   spread depending on pair, so churning ≥5000 trades/year on M1
   has historically been the dominant failure mode. Mean-reverting
   genomes can use a smaller value (3–10 bars); trend-followers
   should use larger (15–60 bars).
5. Each comparison MUST reference at least one indicator on lhs or rhs
   (no comparing two scalar literals).
6. `crosses_above` / `crosses_below` MUST be used with at least one
   indicator side (a series can only cross another series or a level).
7. RSI ranges 0–100; ATR is in price units; SMA/EMA/BB/Donchian are price
   levels. Comparing RSI to a price-scale indicator is nonsensical —
   compare RSI to a numeric literal in [0, 100] instead.
8. Each entry condition is a single comparison (no AND/OR — the GA will
   compose those later if useful).
9. Either `entry.long` or `entry.short` MUST be non-null (or both).

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


def _format_metrics_line(metrics: dict | None) -> str:
    """One-line metric breakdown for a single genome. Helps the LLM
    diagnose *why* a genome is failing instead of seeing just Sharpe."""
    if not metrics:
        return "no metrics"
    parts: list[str] = []
    if "sharpe" in metrics and metrics["sharpe"] is not None:
        parts.append(f"Sharpe {float(metrics['sharpe']):.2f}")
    if "max_drawdown" in metrics and metrics["max_drawdown"] is not None:
        parts.append(f"MaxDD {float(metrics['max_drawdown']) * 100:.1f}%")
    if "hit_rate" in metrics and metrics["hit_rate"] is not None:
        parts.append(f"hit {float(metrics['hit_rate']) * 100:.1f}%")
    if "n_trades" in metrics and metrics["n_trades"] is not None:
        parts.append(f"{int(metrics['n_trades'])} trades")
    if "avg_pips_per_trade" in metrics and metrics["avg_pips_per_trade"] is not None:
        parts.append(f"{float(metrics['avg_pips_per_trade']):+.2f} avg-pips/trade")
    if "net_pips" in metrics and metrics["net_pips"] is not None:
        parts.append(f"net {float(metrics['net_pips']):+.0f} pips")
    if "cagr" in metrics and metrics["cagr"] is not None:
        parts.append(f"CAGR {float(metrics['cagr']) * 100:+.1f}%/yr")
    return ", ".join(parts) if parts else "no metrics"


def build_user_prompt(
    instrument: str,
    granularity: str,
    generation_index: int,
    fitness_history: list[float],
    top_genomes: list[tuple[StrategySpec, float | None, dict | None]],
    n_proposals: int,
    *,
    cost_per_trade_pips: float | None = None,
    prior_llm_proposals: (
        list[tuple[StrategySpec, float | None, dict | None]] | None
    ) = None,
    is_gen_zero_seeding: bool = False,
    min_cagr_target: float | None = None,
) -> str:
    """Render the per-call user prompt.

    Args:
        instrument: e.g. "EUR_USD"
        granularity: e.g. "M1"
        generation_index: 0-based current generation number
        fitness_history: best-fitness per generation, oldest-first
        top_genomes: list of (spec, fitness, metrics) tuples for the
            current top-K. The metrics dict — when supplied — surfaces
            hit_rate / max_dd / n_trades / avg_pips_per_trade per
            genome so the LLM can diagnose *why* it's failing.
        n_proposals: how many genomes to propose
        cost_per_trade_pips: realistic round-trip cost in pips for the
            (instrument, granularity) at the current cost model. Lets
            the LLM see that a 5-pip-stop strategy needs > 5 pips of
            edge per trade to break even.
        prior_llm_proposals: same shape as top_genomes, listing this
            run's earlier LLM-origin genomes and how they scored.
            Prevents the LLM from re-proposing the same shape that
            already failed.
        is_gen_zero_seeding: when True, the prompt frames the call as
            "propose the *initial* population for a brand-new run" —
            no incumbents, no fitness history, focus on diverse shapes
            tuned to the cost model.
    """
    fitness_line = (
        ", ".join(f"{f:.3f}" if f is not None else "—" for f in fitness_history[-6:])
        if fitness_history
        else "—"
    )

    def _unpack(row):
        # Accept (spec, fitness) for backwards compatibility with older
        # callers (and the public unit-test surface); the new orchestrator
        # path passes (spec, fitness, metrics) so the prompt can render
        # the per-elite metric breakdown.
        if len(row) == 3:
            return row[0], row[1], row[2]
        return row[0], row[1], None

    top_lines = []
    for i, row in enumerate(top_genomes, start=1):
        spec, fitness, metrics = _unpack(row)
        fit_str = f"{fitness:.3f}" if fitness is not None else "n/a"
        spec_json = json.dumps(spec.model_dump(mode="json"), separators=(",", ":"))
        metrics_str = _format_metrics_line(metrics)
        top_lines.append(f"#{i} (Sharpe {fit_str}) [{metrics_str}]: {spec_json}")
    top_block = "\n".join(top_lines) if top_lines else "(none yet — generation 0)"

    cost_block = ""
    if cost_per_trade_pips is not None:
        cost_block = (
            f"\nCOST MODEL: round-trip cost is ~{cost_per_trade_pips:.2f} pips per "
            f"trade (typical OANDA spread + slippage on this pair/granularity).\n"
            f"A strategy needs > {cost_per_trade_pips:.2f} pips of average edge per "
            f"trade to be net-profitable. Strategies that flip frequently on M1 "
            f"tend to bleed to this cost; favour wider edges + selective entries.\n"
        )

    cagr_block = ""
    if min_cagr_target is not None:
        cagr_block = (
            f"\nPROFIT TARGET: this run's Gate B requires a net CAGR "
            f"≥ {min_cagr_target * 100:.1f}%/year (after spread + swap, "
            f"on the held-out OOS fold). Strategies that backtest profitable "
            f"but only ~5%/year won't validate. Aim for proposals whose edge "
            f"per trade × expected annual trade count clears that bar.\n"
        )

    prior_block = ""
    if prior_llm_proposals:
        prior_lines = []
        for row in prior_llm_proposals:
            spec, fitness, metrics = _unpack(row)
            fit_str = f"{fitness:.3f}" if fitness is not None else "n/a"
            spec_json = json.dumps(spec.model_dump(mode="json"), separators=(",", ":"))
            metrics_str = _format_metrics_line(metrics)
            prior_lines.append(f"  Sharpe {fit_str} [{metrics_str}]: {spec_json}")
        prior_block = (
            "\nYOUR PRIOR PROPOSALS IN THIS RUN (and how they scored — "
            "avoid repeating shapes that already failed):\n"
            + "\n".join(prior_lines)
            + "\n"
        )

    exit_note = (
        "\nNOTE ON EXITS: stop_pips / target_pips / max_hold_bars / "
        "min_hold_bars / opposite_signal in the exit block are all "
        "enforced — your choices directly govern when each trade "
        "closes. Tight stops on a noisy timeframe = trades stopped "
        "out before the thesis plays out. Wide stops = each loss is "
        "large. Match exit budget to entry signal robustness.\n"
        "min_hold_bars in particular: on M1, every trade pays 0.5-1.8 "
        "pips of spread (varies by pair). A genome that flips position "
        "every couple of bars churns 5000+ trades/year, and the spread "
        "bleed exceeds any per-trade edge. Set min_hold_bars to a "
        "value that gives the entry signal time to play out — 5-15 on "
        "M1 for mean-reversion, 20-60 for trend-following.\n"
    )

    framing_blocks = f"{cost_block}{cagr_block}{exit_note}"

    if is_gen_zero_seeding:
        return f"""\
INSTRUMENT: {instrument} {granularity}
GENERATION: 0 (initial population seeding)
{framing_blocks}
This is a brand-new evolutionary search — no incumbent genomes yet. The
GA will be seeded with three canonical strategies (MA crossover, RSI
mean-reversion, Donchian breakout) plus your proposals. Your job is to
add {n_proposals} *structurally diverse* starting points that are
tuned to the cost model above. Cover regimes the canonical seeds miss
(e.g. volatility-filtered breakouts, multi-indicator confluence,
asymmetric long/short logic) and pick exit budgets that give each
strategy a fair chance to express its thesis without bleeding to spread.

Return {n_proposals} genomes as a JSON object matching the response
schema (a `genomes` array of `{{spec, reasoning}}` objects)."""

    return f"""\
INSTRUMENT: {instrument} {granularity}
GENERATION: {generation_index}
FITNESS TRAJECTORY (last 6 generations, OOS Sharpe): {fitness_line}
{framing_blocks}
CURRENT TOP {len(top_genomes)} GENOMES (with metric breakdown):
{top_block}
{prior_block}
Propose exactly {n_proposals} fresh strategies, each with a brief reasoning.
Make them structurally dissimilar to the genomes above, to your prior
proposals, and to one another. Use the metric breakdown to target what's
*specifically* failing in the incumbents (e.g. low hit-rate → propose a
more selective entry; huge n_trades + cost drag → propose wider holds).
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
