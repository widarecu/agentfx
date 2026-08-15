"""Pydantic schema for the v1 Strategy DSL.

This module is the single source of truth for the grammar. Both the compiler
(see `compiler.py`) and the LLM hypothesis injector (which exports
`StrategySpec.model_json_schema()` as the input schema for its
`propose_strategy` tool) consume this.

The grammar is deliberately small in v1; growth is the v0.4.0 axis.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

INDICATOR_NAMES = (
    "close",
    "sma",
    "ema",
    "rsi",
    "atr",
    "bb_upper",
    "bb_lower",
    "donchian_high",
    "donchian_low",
    # v0.4.0 expansion
    "macd_line",
    "macd_signal",
    "stoch",
    "adx",
    "obv",
    "ichimoku_tenkan",
    "ichimoku_kijun",
)
IndicatorName = Literal[
    "close",
    "sma",
    "ema",
    "rsi",
    "atr",
    "bb_upper",
    "bb_lower",
    "donchian_high",
    "donchian_low",
    # v0.4.0 expansion (2026-06-16)
    "macd_line",
    "macd_signal",
    "stoch",
    "adx",
    "obv",
    "ichimoku_tenkan",
    "ichimoku_kijun",
]

ComparatorName = Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]


# Mechanical-event taxonomy for the same-event pathology check.
# Indicators are grouped by the kind of market behaviour they react to.
# Two crossover triggers whose indicators all fall in the same family
# are mechanically equivalent (e.g. both listening for "fast MA crosses
# above slow MA"). This is the pathology the 2026-06-10 diagnostic
# surfaced: long and short triggers both fired on the same upward MA
# crossover, encoding an accidental net-short bias rather than a real
# bidirectional edge. See `_long_and_short_must_have_distinct_event`.
_INDICATOR_FAMILY: dict[str, str] = {
    "sma": "ma",
    "ema": "ma",
    "rsi": "oscillator",
    "atr": "vol_scalar",
    "bb_upper": "vol_band",
    "bb_lower": "vol_band",
    "donchian_high": "channel",
    "donchian_low": "channel",
    "close": "price",
    # v0.4.0 expansion. Group each new indicator into the closest of the
    # existing families so the same-mechanical-event constraint
    # naturally extends to them. MACD line + signal are MA-derived
    # (EMA-of-close arithmetic) so they sit in "ma". Stochastic + ADX
    # are bounded oscillators like RSI. Ichimoku tenkan/kijun are
    # high-low channel midpoints — "channel" alongside Donchian. OBV
    # is its own family ("volume") since it's the only volume-based
    # indicator so far; no other "volume" entries means the same-
    # mechanical-event check won't ever fire on it.
    "macd_line": "ma",
    "macd_signal": "ma",
    "stoch": "oscillator",
    "adx": "oscillator",
    "obv": "volume",
    "ichimoku_tenkan": "channel",
    "ichimoku_kijun": "channel",
}


class IndicatorCall(BaseModel):
    """Reference to a named indicator with its numeric arguments.

    Args are positional (matches indicator function signatures). Values are
    bounded; the compiler enforces these bounds again at compile time.
    """

    ind: IndicatorName
    args: list[float] = Field(default_factory=list, max_length=3)

    model_config = {"frozen": True}


# An expression on either side of a comparator: a numeric literal or an indicator.
Expr = float | int | IndicatorCall


class Comparison(BaseModel):
    """A single comparison: <expr> <cmp> <expr>."""

    lhs: Expr
    cmp: ComparatorName
    rhs: Expr

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _at_least_one_side_is_indicator(self) -> Comparison:
        if not isinstance(self.lhs, IndicatorCall) and not isinstance(
            self.rhs, IndicatorCall
        ):
            raise ValueError(
                "comparison must reference at least one indicator on lhs or rhs"
            )
        if self.cmp in ("crosses_above", "crosses_below"):
            if not (
                isinstance(self.lhs, IndicatorCall)
                or isinstance(self.rhs, IndicatorCall)
            ):
                raise ValueError(f"{self.cmp} requires a series on at least one side")
        return self


class AndCondition(BaseModel):
    op: Literal["AND"] = "AND"
    args: list[Annotated[Condition, Field(discriminator=None)]] = Field(min_length=1)

    model_config = {"frozen": True}


class OrCondition(BaseModel):
    op: Literal["OR"] = "OR"
    args: list[Annotated[Condition, Field(discriminator=None)]] = Field(min_length=1)

    model_config = {"frozen": True}


# A condition is either a single comparison or a logical combination of conditions.
Condition = Comparison | AndCondition | OrCondition


class EntryConditions(BaseModel):
    """Long and short entry conditions.

    Either may be None to disable that side. At least one must be non-None.
    """

    long: Condition | None = None
    short: Condition | None = None

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _at_least_one_side(self) -> EntryConditions:
        if self.long is None and self.short is None:
            raise ValueError("entry must specify at least one of long or short")
        return self

    @model_validator(mode="after")
    def _long_and_short_must_differ(self) -> EntryConditions:
        # Degenerate spec: when long_condition == short_condition the
        # backtest emits long_sig - short_sig == 0 on every bar, so the
        # strategy never enters a trade. These leak in via GA mutations
        # that swap one branch's indicator/comparator to match the
        # other side. Reject at grammar level so the GA's
        # `try/except ValidationError` paths in mutate_spec /
        # crossover_specs retry with a different operator instead of
        # producing a guaranteed zero-trade genome.
        if self.long is not None and self.short is not None and self.long == self.short:
            raise ValueError(
                "long entry condition is identical to short — the resulting "
                "position series is identically zero (no trades)."
            )
        return self

    @model_validator(mode="after")
    def _long_and_short_must_have_distinct_event(self) -> EntryConditions:
        """Reject genomes where long and short triggers fire on the same
        mechanical event.

        Specifically targets the pattern surfaced by the 2026-06-10
        diagnostic on genome ``..._3bc80a6a_001_010``:

            long:  ema(10)  crosses_above  sma(34)    -> MA crossover, UP
            short: ema(8.6) crosses_above  ema(34)    -> MA crossover, UP

        Both triggers fire on the same kind of event (upward MA
        crossover) with no actual downward signal mechanism. The
        strategy encodes whatever directional bias the training-fold
        drift happened to give it; when OOS drift flips, it inverts.
        The rolling-median validator from PR #9 surfaces the failure
        AFTER the run; this constraint prevents the GA from spending
        its budget on the pathology in the first place.

        The constraint is deliberately narrow:

        - Only fires when BOTH long and short are single Comparison
          nodes (not AND/OR compounds). Compound conditions naturally
          tend toward mechanical distinctness so the false-positive
          rate is higher for them.
        - Only fires when BOTH comparators are crossover comparators
          AND in the same direction (both ``crosses_above`` or both
          ``crosses_below``). Crossovers are the only place where
          identical comparator + identical indicator family ALWAYS
          means the same mechanical event.
        - Only fires when ALL indicators appearing on either side of
          both triggers belong to the same family (e.g. all MA-family
          or all volatility-band-family). Different families mean
          different mechanical signals even if the comparator agrees.

        Asymmetric strategies remain legal under this rule:

        - long: ``ema crosses_above sma`` + short: ``rsi > 80``
          -> different families, allowed
        - long: ``ema crosses_above sma`` + short: ``ema crosses_below sma``
          -> opposite crossover directions, allowed
        - long: ``sma > close`` + short: ``sma < close * 0.99``
          -> not crossovers (the rule only constrains crossovers),
          allowed even with asymmetric thresholds
        """
        if not isinstance(self.long, Comparison) or not isinstance(
            self.short, Comparison
        ):
            return self
        if self.long.cmp not in ("crosses_above", "crosses_below"):
            return self
        if self.long.cmp != self.short.cmp:
            return self

        def _indicator_families(cmp: Comparison) -> set[str]:
            fams: set[str] = set()
            for side in (cmp.lhs, cmp.rhs):
                if isinstance(side, IndicatorCall):
                    fam = _INDICATOR_FAMILY.get(side.ind)
                    # "price" (raw close) is not a real indicator family
                    # for this check — it's just a reference scaffolding
                    # for moving averages and bands to compare against.
                    if fam and fam != "price":
                        fams.add(fam)
            return fams

        long_fams = _indicator_families(self.long)
        short_fams = _indicator_families(self.short)
        if not long_fams or not short_fams:
            return self
        if long_fams == short_fams:
            raise ValueError(
                "long and short triggers both fire on the same mechanical "
                f"event ({self.long.cmp} on the {next(iter(long_fams))} "
                "family) — strategy has no actual short-signal mechanism. "
                "Use a different comparator direction (e.g. crosses_below "
                "for one side), a different indicator family, or a "
                "compound condition that introduces real asymmetry."
            )
        return self


class ExitRules(BaseModel):
    """Exit rules. The compiler walks per-bar position state and enforces
    every field here — stop / target / max-hold / min-hold / opposite-signal
    are all live as of v0.4.0.
    """

    stop_pips: int = Field(ge=5, le=400)
    target_pips: int = Field(ge=5, le=800)
    max_hold_bars: int = Field(ge=1, le=2880)
    # Minimum bars a position must stay open before any exit path can
    # fire (stop / target / opposite-signal). 0 = legacy behaviour
    # (exits can fire on the entry bar's next tick). Larger values
    # discourage over-trading on short timeframes — the bleed pattern
    # we kept hitting on M1 EUR_USD was strategies that churned ~6000
    # trades/year, each paying 0.7-1.2 pips of spread, so a 55% win
    # rate still lost net. The GA mutates this and the LLM injection
    # prompt biases proposals toward non-zero values on minute-scale
    # timeframes.
    min_hold_bars: int = Field(default=0, ge=0, le=2880)
    opposite_signal: bool = True
    # How the walk state machine (and backtest exit machine) evaluate
    # SL and TP each bar. Wilson 2026-08-15 audit:
    #
    #   * "close" (default, legacy): only the bar's CLOSE counts.
    #     Intra-bar wicks that touch the SL/TP but recover before the
    #     bar ends do NOT trigger an exit. Matches every DSL genome
    #     validated before this field existed.
    #
    #   * "wick": the bar's LOW (for LONG SL / SHORT TP) and HIGH
    #     (for LONG TP / SHORT SL) are checked. Any touch triggers
    #     the exit at exactly the SL/TP price. Realistic model of how
    #     broker-side stop / take-profit orders fire — recommended for
    #     genomes that will eventually run under a tight broker SL.
    #
    # New genomes' GA searches over this field. Existing genomes
    # default to "close" so their OOS backtest metrics remain valid.
    eval_mode: Literal["close", "wick"] = "close"

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _stop_lt_target(self) -> ExitRules:
        if self.stop_pips >= self.target_pips:
            raise ValueError(
                f"stop_pips ({self.stop_pips}) must be < target_pips ({self.target_pips})"
            )
        return self

    @model_validator(mode="after")
    def _min_hold_lt_max_hold(self) -> ExitRules:
        if self.min_hold_bars >= self.max_hold_bars:
            raise ValueError(
                f"min_hold_bars ({self.min_hold_bars}) must be < "
                f"max_hold_bars ({self.max_hold_bars})"
            )
        return self


class SizeRules(BaseModel):
    """Position sizing. risk_pct is a fraction of account balance per trade."""

    risk_pct: float = Field(ge=0.001, le=0.005)

    model_config = {"frozen": True}


class StrategySpec(BaseModel):
    """v1 Strategy DSL spec.

    The full grammar in one model. The genome the GA evolves; the JSON the
    LLM emits; the row stored in `strategy_genome.dsl_json`.
    """

    version: Literal[1] = 1
    entry: EntryConditions
    exit: ExitRules
    size: SizeRules

    model_config = {"frozen": True}


# Resolve forward references in recursive types
AndCondition.model_rebuild()
OrCondition.model_rebuild()
