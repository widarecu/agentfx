"""Grammar (Pydantic schema) validation for the v1 Strategy DSL."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

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


def _ind(name: str, *args: float) -> IndicatorCall:
    return IndicatorCall(ind=name, args=list(args))  # type: ignore[arg-type]


def _minimal_spec() -> StrategySpec:
    return StrategySpec(
        entry=EntryConditions(
            long=Comparison(lhs=_ind("sma", 20), cmp=">", rhs=_ind("sma", 50))
        ),
        exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
        size=SizeRules(risk_pct=0.005),
    )


class TestStrategySpec:
    def test_minimal_spec_constructs(self) -> None:
        spec = _minimal_spec()
        assert spec.version == 1
        assert spec.entry.long is not None
        assert spec.entry.short is None

    def test_at_least_one_entry_side_required(self) -> None:
        with pytest.raises(ValidationError, match="at least one of long or short"):
            EntryConditions()

    def test_stop_must_be_less_than_target(self) -> None:
        with pytest.raises(ValidationError, match="must be < target_pips"):
            ExitRules(stop_pips=50, target_pips=20, max_hold_bars=100)

    def test_risk_pct_bounds(self) -> None:
        SizeRules(risk_pct=0.001)  # lower bound ok
        SizeRules(risk_pct=0.005)  # upper bound ok
        with pytest.raises(ValidationError):
            SizeRules(risk_pct=0.0005)  # below lower
        with pytest.raises(ValidationError):
            SizeRules(risk_pct=0.01)  # above upper

    def test_stop_pips_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ExitRules(stop_pips=4, target_pips=20, max_hold_bars=100)
        with pytest.raises(ValidationError):
            ExitRules(stop_pips=500, target_pips=600, max_hold_bars=100)

    def test_max_hold_bars_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ExitRules(stop_pips=10, target_pips=20, max_hold_bars=0)
        with pytest.raises(ValidationError):
            ExitRules(stop_pips=10, target_pips=20, max_hold_bars=3000)

    def test_min_hold_bars_default_is_zero_legacy(self) -> None:
        """min_hold_bars defaults to 0 — preserves legacy behaviour
        when older specs don't carry the field."""
        rules = ExitRules(stop_pips=10, target_pips=20, max_hold_bars=100)
        assert rules.min_hold_bars == 0

    def test_min_hold_bars_bounds(self) -> None:
        # Negative is rejected
        with pytest.raises(ValidationError):
            ExitRules(
                stop_pips=10,
                target_pips=20,
                max_hold_bars=100,
                min_hold_bars=-1,
            )
        # Above the upper bound is rejected
        with pytest.raises(ValidationError):
            ExitRules(
                stop_pips=10,
                target_pips=20,
                max_hold_bars=100,
                min_hold_bars=3000,
            )

    def test_min_hold_must_be_less_than_max_hold(self) -> None:
        """Pins the invariant — otherwise an entry can be locked in
        forever (no exit path could ever fire)."""
        with pytest.raises(ValidationError, match="must be < .*max_hold_bars"):
            ExitRules(
                stop_pips=10,
                target_pips=20,
                max_hold_bars=100,
                min_hold_bars=100,  # equal → invalid
            )
        with pytest.raises(ValidationError, match="must be < .*max_hold_bars"):
            ExitRules(
                stop_pips=10,
                target_pips=20,
                max_hold_bars=100,
                min_hold_bars=101,  # greater → invalid
            )


class TestComparison:
    def test_indicator_vs_literal_ok(self) -> None:
        Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30.0)

    def test_literal_vs_indicator_ok(self) -> None:
        Comparison(lhs=70.0, cmp=">", rhs=_ind("rsi", 14))

    def test_two_literals_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="must reference at least one indicator"
        ):
            Comparison(lhs=10.0, cmp=">", rhs=20.0)

    def test_unknown_indicator_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IndicatorCall(ind="unknown_indicator", args=[14])  # type: ignore[arg-type]

    def test_unknown_comparator_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Comparison(lhs=_ind("sma", 20), cmp="==", rhs=_ind("sma", 50))  # type: ignore[arg-type]


class TestLogicalConditions:
    def test_and_with_one_arg(self) -> None:
        AndCondition(args=[Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30.0)])

    def test_and_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AndCondition(args=[])

    def test_or_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrCondition(args=[])

    def test_nested_and_or(self) -> None:
        spec = StrategySpec(
            entry=EntryConditions(
                long=AndCondition(
                    args=[
                        Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30.0),
                        OrCondition(
                            args=[
                                Comparison(
                                    lhs=_ind("sma", 20), cmp=">", rhs=_ind("sma", 50)
                                ),
                                Comparison(
                                    lhs=_ind("ema", 20),
                                    cmp="crosses_above",
                                    rhs=_ind("ema", 50),
                                ),
                            ]
                        ),
                    ]
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        assert isinstance(spec.entry.long, AndCondition)


class TestSchemaExport:
    def test_json_schema_round_trips(self) -> None:
        """The exported JSON Schema should be valid JSON and re-importable
        (the LLM client will use it as the propose_strategy tool's input
        schema).
        """
        schema = StrategySpec.model_json_schema()
        # Must serialize cleanly
        as_json = json.dumps(schema)
        re_loaded = json.loads(as_json)
        assert re_loaded["type"] == "object"
        assert "properties" in re_loaded

    def test_spec_serializes_to_json(self) -> None:
        spec = _minimal_spec()
        payload = spec.model_dump(mode="json")
        # Round-trip
        restored = StrategySpec.model_validate(payload)
        assert restored == spec
