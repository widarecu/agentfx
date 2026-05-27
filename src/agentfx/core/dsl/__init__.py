"""Strategy DSL — structured representation of trading strategies.

A `StrategySpec` is the canonical representation of a strategy. It's the
genome the GA evolves, the JSON Schema the LLM emits, and the persistable
form stored in the `strategy_genome` table.

The compiler turns a `StrategySpec` into a `Strategy` instance conforming to
the existing `generate_signals(df) -> pd.Series` interface. The backtest
pipeline does not change.
"""

from agentfx.core.dsl.compiler import CompilerError, compile_spec
from agentfx.core.dsl.grammar import (
    AndCondition,
    Comparison,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    OrCondition,
    SizeRules,
    StrategySpec,
)

__all__ = [
    "AndCondition",
    "Comparison",
    "CompilerError",
    "EntryConditions",
    "ExitRules",
    "IndicatorCall",
    "OrCondition",
    "SizeRules",
    "StrategySpec",
    "compile_spec",
]
