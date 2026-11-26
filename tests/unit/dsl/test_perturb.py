"""±10% parameter nudges for the robustness check (AFX-153)."""

from __future__ import annotations

from typing import Any

import pytest

from agentfx.core.dsl.grammar import (
    AndCondition,
    Comparison,
    EntryConditions,
    ExitRules,
    IndicatorCall,
    SizeRules,
    StrategySpec,
)
from agentfx.core.dsl.perturb import perturbed_variants, tunable_parameters


def _ind(name: str, *args: float) -> IndicatorCall:
    return IndicatorCall(ind=name, args=list(args))  # type: ignore[arg-type]


def _spec(
    long: Any,
    *,
    short: Any = None,
    stop: int = 20,
    target: int = 60,
    max_hold: int = 240,
    min_hold: int = 0,
) -> StrategySpec:
    return StrategySpec(
        entry=EntryConditions(long=long, short=short),
        exit=ExitRules(
            stop_pips=stop,
            target_pips=target,
            max_hold_bars=max_hold,
            min_hold_bars=min_hold,
        ),
        size=SizeRules(risk_pct=0.005),
    )


def _values(spec: StrategySpec, path: str) -> dict[str, float]:
    """``{direction: nudged value}`` for one parameter."""
    return {
        v.direction: v.value
        for v in perturbed_variants(spec)
        if v.parameter.path == path
    }


def _leaves(node: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(node, dict):
        return {
            k: v
            for key, sub in node.items()
            for k, v in _leaves(sub, f"{prefix}.{key}").items()
        }
    if isinstance(node, list):
        return {
            k: v
            for i, sub in enumerate(node)
            for k, v in _leaves(sub, f"{prefix}[{i}]").items()
        }
    return {prefix: node}


def test_fractional_period_is_nudged_around_the_period_it_runs_as() -> None:
    """The losing paper genome from AFX-49 used RSI period 10.7525. The
    indicator runs it as 10, so the nudges are 9 and 11."""
    spec = _spec(Comparison(lhs=_ind("rsi", 10.7525), cmp="<", rhs=30))
    assert _values(spec, "entry.long.lhs.args[0]") == {"down": 9, "up": 11}


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        (5, {"down": 4, "up": 6}),  # 10% rounds to 0: step by 1
        (14, {"down": 13, "up": 15}),
        (200, {"down": 180, "up": 220}),
        (2, {"up": 3}),  # already at the lower bound
        (400, {"down": 360}),  # already at the upper bound
        (380, {"down": 342, "up": 400}),  # clamped to the bound
    ],
)
def test_whole_number_steps_are_at_least_one_and_respect_bounds(
    period, expected
) -> None:
    spec = _spec(Comparison(lhs=_ind("close"), cmp=">", rhs=_ind("sma", period)))
    assert _values(spec, "entry.long.rhs.args[0]") == expected


def test_bollinger_width_is_nudged_as_a_fraction_within_bounds() -> None:
    spec = _spec(Comparison(lhs=_ind("close"), cmp="<", rhs=_ind("bb_lower", 20, 2.0)))
    assert _values(spec, "entry.long.rhs.args[1]") == pytest.approx(
        {"down": 1.8, "up": 2.2}
    )
    edge = _spec(Comparison(lhs=_ind("close"), cmp="<", rhs=_ind("bb_lower", 20, 4.0)))
    assert _values(edge, "entry.long.rhs.args[1]") == pytest.approx({"down": 3.6})


def test_thresholds_are_nudged_and_zero_thresholds_are_left_alone() -> None:
    spec = _spec(
        AndCondition(
            args=[
                Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30),
                Comparison(lhs=_ind("macd_line", 12, 26), cmp=">", rhs=0),
            ]
        )
    )
    paths = [p.path for p in tunable_parameters(spec)]
    assert "entry.long.args[0].rhs" in paths
    assert "entry.long.args[1].rhs" not in paths  # zero has no 10% neighbourhood
    assert _values(spec, "entry.long.args[0].rhs") == pytest.approx(
        {"down": 27, "up": 33}
    )


def test_exit_numbers_are_nudged_and_sizing_is_not() -> None:
    spec = _spec(
        Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30),
        stop=400,
        target=800,
        max_hold=240,
        min_hold=0,
    )
    paths = [p.path for p in tunable_parameters(spec)]
    assert [p for p in paths if p.startswith("exit.")] == [
        "exit.stop_pips",
        "exit.target_pips",
        "exit.max_hold_bars",
        "exit.min_hold_bars",
    ]
    assert not any(p.startswith("size.") for p in paths)
    assert _values(spec, "exit.stop_pips") == {"down": 360}  # 400 is the cap
    assert _values(spec, "exit.max_hold_bars") == {"down": 216, "up": 264}
    assert _values(spec, "exit.min_hold_bars") == {"up": 1}  # cannot go below 0


def test_each_variant_changes_exactly_one_number() -> None:
    spec = _spec(
        Comparison(lhs=_ind("ema", 12), cmp="crosses_above", rhs=_ind("sma", 48)),
        short=Comparison(lhs=_ind("rsi", 10.7525), cmp=">", rhs=70),
    )
    base = _leaves(spec.model_dump(mode="json"))
    variants = perturbed_variants(spec)
    assert len(variants) == 2 * len(tunable_parameters(spec)) - 1  # min_hold: up only
    for variant in variants:
        assert variant.spec is not None
        changed = {
            k
            for k, v in _leaves(variant.spec.model_dump(mode="json")).items()
            if v != base[k]
        }
        assert changed == {"." + variant.parameter.path}


def test_variant_that_breaks_the_grammar_is_marked_invalid() -> None:
    """A stop nudged up to the target is not a valid strategy; it is
    reported, not backtested."""
    spec = _spec(Comparison(lhs=_ind("rsi", 14), cmp="<", rhs=30), stop=95, target=100)
    [up] = [
        v
        for v in perturbed_variants(spec)
        if v.parameter.path == "exit.stop_pips" and v.direction == "up"
    ]
    assert up.spec is None
    assert up.value == 105
    assert "stop_pips (105) must be < target_pips (100)" in up.invalid_reason
