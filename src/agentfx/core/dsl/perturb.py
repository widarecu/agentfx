"""±10% parameter nudges for the robustness check (AFX-153).

A real edge sits on a plateau: nudging one number in the strategy by about
10% barely changes its out-of-sample results. An overfitted strategy sits
on a spike, and the same nudge collapses it. This module finds every
tunable number in a strategy and builds the nudged copies; the validator
backtests them.

Tunable numbers, one at a time:

- every indicator argument (periods, Bollinger width), clamped to the
  indicator's bounds;
- every nonzero numeric threshold in an entry comparison (``rsi < 30``);
- the exit numbers ``stop_pips``, ``target_pips``, ``max_hold_bars`` and
  ``min_hold_bars``, clamped to their grammar bounds.

Not nudged: ``size.risk_pct`` (position sizing scales every trade the same
way and does not change which trades happen), the ``eval_mode`` and
``opposite_signal`` switches, and zero thresholds (10% of zero is zero).

Whole-number parameters move to the nearest whole number at least 1 away.
Indicator periods count as whole numbers even when stored as fractions,
because the indicators drop the fraction: an RSI period of 10.7525 runs
as 10, so its nudges are 9 and 11.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from annotated_types import Ge, Le
from pydantic import BaseModel, ValidationError

from agentfx.core.dsl.compiler import CompilerError, compile_spec
from agentfx.core.dsl.grammar import ExitRules, StrategySpec
from agentfx.core.dsl.indicators import INDICATOR_ARG_BOUNDS

PERTURBATION = 0.10

# The only indicator arguments the indicator functions keep fractional;
# every other argument is a bar count truncated with int().
_FRACTIONAL_ARGS = {("bb_upper", 1), ("bb_lower", 1)}

_EXIT_FIELDS = ("stop_pips", "target_pips", "max_hold_bars", "min_hold_bars")


@dataclass(frozen=True)
class TunableParameter:
    """One number in a strategy that the robustness check nudges.

    ``path`` addresses the number in the strategy JSON, for example
    ``entry.long.lhs.args[0]``.
    """

    path: str
    description: str
    value: float
    integer: bool
    lower: float | None = None
    upper: float | None = None


@dataclass(frozen=True)
class ParameterVariant:
    """A strategy with exactly one parameter nudged.

    ``spec`` is None when the nudged strategy is not a valid strategy (for
    example a stop nudged past the target); ``invalid_reason`` says why.
    """

    parameter: TunableParameter
    direction: str  # "down" | "up"
    value: float
    spec: StrategySpec | None
    invalid_reason: str | None = None


def _field_bounds(
    model: type[BaseModel], name: str
) -> tuple[float | None, float | None]:
    lower = upper = None
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, Ge):
            lower = float(meta.ge)
        elif isinstance(meta, Le):
            upper = float(meta.le)
    return lower, upper


def _walk(node: Any, path: str) -> Iterator[tuple[str, dict]]:
    """Yield ``(path, comparison)`` for every comparison under ``node``."""
    if not isinstance(node, dict):
        return
    if "cmp" in node:
        yield path, node
    elif node.get("op") in ("AND", "OR"):
        for i, sub in enumerate(node.get("args", [])):
            yield from _walk(sub, f"{path}.args[{i}]")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tunable_parameters(spec: StrategySpec) -> list[TunableParameter]:
    """Every number in ``spec`` the robustness check nudges, in a stable
    order: entry conditions first, then exit rules."""
    data = spec.model_dump(mode="json")
    found: list[TunableParameter] = []
    for side in ("long", "short"):
        for cmp_path, cmp in _walk(data["entry"].get(side), f"entry.{side}"):
            for key in ("lhs", "rhs"):
                operand = cmp[key]
                path = f"{cmp_path}.{key}"
                if isinstance(operand, dict):
                    name = operand["ind"]
                    bounds = INDICATOR_ARG_BOUNDS[name]
                    for i, arg in enumerate(operand["args"]):
                        lo, hi = bounds[i] if i < len(bounds) else (None, None)
                        found.append(
                            TunableParameter(
                                path=f"{path}.args[{i}]",
                                description=f"{name} argument {i + 1}",
                                value=float(arg),
                                integer=(name, i) not in _FRACTIONAL_ARGS,
                                lower=lo,
                                upper=hi,
                            )
                        )
                elif _is_number(operand) and operand != 0:
                    found.append(
                        TunableParameter(
                            path=path,
                            description=f"entry {side} threshold",
                            value=float(operand),
                            integer=isinstance(operand, int),
                        )
                    )
    for name in _EXIT_FIELDS:
        lo, hi = _field_bounds(ExitRules, name)
        found.append(
            TunableParameter(
                path=f"exit.{name}",
                description=name,
                value=float(data["exit"][name]),
                integer=True,
                lower=lo,
                upper=hi,
            )
        )
    return found


def _nudged(param: TunableParameter, sign: int, fraction: float) -> float | None:
    """The parameter moved by ``fraction`` in direction ``sign``, clamped to
    its bounds; None when the clamp leaves it where it was."""
    if param.integer:
        base = math.trunc(param.value)
        step = max(1, round(abs(base) * fraction))
        new: float = base + sign * step
        lo = math.ceil(param.lower) if param.lower is not None else None
        hi = math.floor(param.upper) if param.upper is not None else None
    else:
        base = param.value
        new = base * (1 + sign * fraction)
        lo, hi = param.lower, param.upper
    if lo is not None:
        new = max(lo, new)
    if hi is not None:
        new = min(hi, new)
    return None if new == base else new


def _set(data: dict, path: str, value: float | int) -> None:
    """Assign ``value`` at a dotted path such as ``entry.long.args[1].rhs``."""
    parts: list[str | int] = []
    for token in path.split("."):
        if "[" in token:
            key, index = token[:-1].split("[")
            parts.extend([key, int(index)])
        else:
            parts.append(token)
    node: Any = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def perturbed_variants(
    spec: StrategySpec, fraction: float = PERTURBATION
) -> list[ParameterVariant]:
    """One strategy per tunable parameter and direction (down, then up),
    each with only that parameter nudged by ``fraction``. A direction is
    left out when the parameter already sits on that bound."""
    variants: list[ParameterVariant] = []
    for param in tunable_parameters(spec):
        for direction, sign in (("down", -1), ("up", 1)):
            new = _nudged(param, sign, fraction)
            if new is None:
                continue
            value: float | int = int(new) if param.integer else new
            data = spec.model_dump(mode="json")
            _set(data, param.path, value)
            try:
                variant = StrategySpec.from_stored_genome(data)
                compile_spec(variant)
            except (ValidationError, CompilerError) as exc:
                reason = (
                    "; ".join(e["msg"] for e in exc.errors())
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                variants.append(
                    ParameterVariant(param, direction, float(value), None, reason)
                )
                continue
            variants.append(ParameterVariant(param, direction, float(value), variant))
    return variants


__all__ = [
    "PERTURBATION",
    "ParameterVariant",
    "TunableParameter",
    "perturbed_variants",
    "tunable_parameters",
]
