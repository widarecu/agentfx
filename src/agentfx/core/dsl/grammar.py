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
]

ComparatorName = Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]


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


class ExitRules(BaseModel):
    """Exit rules. Recorded in the spec; enforcement is a v0.4.0 backtest engine
    capability. In v1 the compiler emits a per-bar indicator-state position;
    these fields are forward-looking metadata for the GA.
    """

    stop_pips: int = Field(ge=5, le=400)
    target_pips: int = Field(ge=5, le=800)
    max_hold_bars: int = Field(ge=1, le=2880)
    opposite_signal: bool = True

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _stop_lt_target(self) -> ExitRules:
        if self.stop_pips >= self.target_pips:
            raise ValueError(
                f"stop_pips ({self.stop_pips}) must be < target_pips ({self.target_pips})"
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
