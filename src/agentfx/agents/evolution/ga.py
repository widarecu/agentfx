"""Genetic algorithm operators for `StrategySpec` genomes.

We use DEAP for selection plumbing (tournament, best-N elitism) and write
mutation/crossover ourselves because DEAP's tree-GP primitives don't fit
the nested Pydantic DSL we use.

Mutation strategy:
- Pick one of six mutation types uniformly at random.
- Each operates on the spec's dict representation, applies a single
  change, then re-validates back to a `StrategySpec`.
- If a chosen mutation has no candidate sites (e.g. "perturb exit numeric"
  is always valid; "swap indicator" needs an IndicatorCall to swap), the
  mutator returns the dict unchanged. The dispatcher tries up to 3
  alternative mutations before giving up and returning the input spec.
- If validation fails after a mutation (rare — bounds violations from
  perturbation, e.g. stop_pips going below 5), we return the original
  spec rather than risk an invalid genome.

Crossover strategy:
- Pick a slot (long, short, exit) uniformly.
- Swap that slot between parents to produce two children.
- If swap produces an invalid spec (e.g. both `entry.long` and
  `entry.short` end up None), return parents unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from random import Random
from typing import Any
from collections.abc import Callable

from deap import tools
from pydantic import ValidationError

from agentfx.agents.evolution.population import Genome, Population
from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl import StrategySpec
from agentfx.core.dsl.grammar import INDICATOR_NAMES
from agentfx.core.dsl.indicators import INDICATOR_ARG_BOUNDS

# ─────────────────────────────────────────────────────────────────────────
# Mutation: spec-tree walking helpers
# ─────────────────────────────────────────────────────────────────────────


def _is_indicator_call(node: Any) -> bool:
    return isinstance(node, dict) and "ind" in node and "args" in node


def _is_comparison(node: Any) -> bool:
    return isinstance(node, dict) and {"lhs", "cmp", "rhs"}.issubset(node)


def _is_logical(node: Any) -> bool:
    return isinstance(node, dict) and node.get("op") in ("AND", "OR")


def _walk_comparisons(condition: Any, visit: Callable[[dict], None]) -> None:
    """Visit every Comparison dict reachable under `condition`."""
    if condition is None:
        return
    if _is_comparison(condition):
        visit(condition)
    elif _is_logical(condition):
        for sub in condition.get("args", []):
            _walk_comparisons(sub, visit)


def _collect_comparisons(spec_dict: dict) -> list[dict]:
    found: list[dict] = []
    entry = spec_dict.get("entry", {})
    for side in ("long", "short"):
        cond = entry.get(side)
        if cond is not None:
            _walk_comparisons(cond, found.append)
    return found


def _collect_indicator_calls(spec_dict: dict) -> list[dict]:
    """Every IndicatorCall dict on either side of any Comparison."""
    found: list[dict] = []
    for cmp_node in _collect_comparisons(spec_dict):
        for side in (cmp_node.get("lhs"), cmp_node.get("rhs")):
            if _is_indicator_call(side):
                found.append(side)
    return found


def _collect_numeric_literals(spec_dict: dict) -> list[tuple[dict, str]]:
    """Numeric-literal sides of comparisons, returned as (parent_cmp, side_key)
    tuples so we can replace them in place."""
    found: list[tuple[dict, str]] = []
    for cmp_node in _collect_comparisons(spec_dict):
        for side_key in ("lhs", "rhs"):
            side = cmp_node.get(side_key)
            if isinstance(side, (int, float)) and not isinstance(side, bool):
                found.append((cmp_node, side_key))
    return found


# ─────────────────────────────────────────────────────────────────────────
# Mutation operators (each: dict -> bool indicating whether a change was made)
# ─────────────────────────────────────────────────────────────────────────

# Comparators are split into "scalar" and "cross" categories — flipping a
# comparator only swaps within its own category, so a `>` doesn't suddenly
# become `crosses_above` (different semantics, often invalid in context).
_SCALAR_CMPS = (">", "<", ">=", "<=")
_CROSS_CMPS = ("crosses_above", "crosses_below")


def _mut_flip_comparator(spec_dict: dict, rng: Random) -> bool:
    cmps = _collect_comparisons(spec_dict)
    if not cmps:
        return False
    target = rng.choice(cmps)
    current = target["cmp"]
    if current in _SCALAR_CMPS:
        alternatives = [c for c in _SCALAR_CMPS if c != current]
    else:
        alternatives = [c for c in _CROSS_CMPS if c != current]
    if not alternatives:
        return False
    target["cmp"] = rng.choice(alternatives)
    return True


def _mut_perturb_threshold(spec_dict: dict, rng: Random) -> bool:
    literals = _collect_numeric_literals(spec_dict)
    if not literals:
        return False
    parent_cmp, side_key = rng.choice(literals)
    current = parent_cmp[side_key]
    factor = rng.uniform(0.8, 1.2)
    new_value = current * factor
    # Keep ints as ints (int comparators are common for RSI thresholds);
    # keep floats as floats.
    if isinstance(current, int):
        new_value = max(1, int(round(new_value)))
    parent_cmp[side_key] = new_value
    return True


def _mut_swap_indicator(spec_dict: dict, rng: Random) -> bool:
    calls = _collect_indicator_calls(spec_dict)
    if not calls:
        return False
    target = rng.choice(calls)
    current = target["ind"]
    # Pick a different indicator with the same arity so the args slot in
    # without surgery.
    current_arity = len(INDICATOR_ARG_BOUNDS.get(current, []))
    candidates = [
        name
        for name in INDICATOR_NAMES
        if name != current and len(INDICATOR_ARG_BOUNDS.get(name, [])) == current_arity
    ]
    if not candidates:
        return False
    new_name = rng.choice(candidates)
    target["ind"] = new_name
    # Clamp existing args to the new indicator's bounds.
    new_bounds = INDICATOR_ARG_BOUNDS[new_name]
    args = target.get("args", [])
    clamped = []
    for i, val in enumerate(args):
        if i >= len(new_bounds):
            break
        lo, hi = new_bounds[i]
        clamped_val = max(lo, min(hi, val))
        # Preserve int-ness for the first numeric arg (windows are ints).
        if isinstance(val, int):
            clamped_val = int(round(clamped_val))
        clamped.append(clamped_val)
    target["args"] = clamped
    return True


def _mut_change_window(spec_dict: dict, rng: Random) -> bool:
    calls = _collect_indicator_calls(spec_dict)
    # Pick only calls whose first arg has bounds (i.e. parameterised
    # indicators — not `close`).
    eligible = [c for c in calls if c.get("args")]
    if not eligible:
        return False
    target = rng.choice(eligible)
    name = target["ind"]
    bounds = INDICATOR_ARG_BOUNDS.get(name, [])
    if not bounds:
        return False
    lo, hi = bounds[0]
    current = target["args"][0]
    factor = rng.uniform(0.75, 1.25)
    new_val = current * factor
    new_val = max(lo, min(hi, new_val))
    if isinstance(current, int):
        new_val = max(int(lo), min(int(hi), int(round(new_val))))
    if new_val == current:
        # Walked off the bound; nudge by 1 in a random direction.
        new_val = max(lo, min(hi, current + rng.choice([-1, 1])))
        if isinstance(current, int):
            new_val = int(round(new_val))
    target["args"][0] = new_val
    return True


def _mut_toggle_opposite_signal(spec_dict: dict, rng: Random) -> bool:
    exit_node = spec_dict.get("exit")
    if not isinstance(exit_node, dict):
        return False
    exit_node["opposite_signal"] = not exit_node.get("opposite_signal", True)
    return True


def _mut_perturb_exit_numeric(spec_dict: dict, rng: Random) -> bool:
    exit_node = spec_dict.get("exit")
    if not isinstance(exit_node, dict):
        return False
    fields = ["stop_pips", "target_pips", "max_hold_bars"]
    field = rng.choice(fields)
    current = exit_node.get(field)
    if not isinstance(current, int):
        return False
    factor = rng.uniform(0.8, 1.2)
    new_val = max(1, int(round(current * factor)))
    exit_node[field] = new_val
    return True


def _mut_perturb_min_hold(spec_dict: dict, rng: Random) -> bool:
    """Mutate ``min_hold_bars``. Gives the GA a knob to escape the
    over-trading failure mode (M1 strategies churning 6000+ trades/year
    paid more in spread than they earned in edge). With this mutation
    in play, lineages that lower their min-hold trade more and bleed
    more; lineages that raise it trade less and may compound the
    edge they have.

    Three modes, chosen by rng:
      * with prob 0.20, "bump from zero" — if currently 0, set to a
        random value in [3, 15]. Bootstraps the dimension from gen-0
        seeds that don't carry a non-zero default.
      * with prob 0.40, multiplicative perturbation by ±25% (similar
        to the other exit-numeric perturbations).
      * with prob 0.40, additive perturbation by ±1..±3 bars. Lets
        small lineages walk the dimension without big jumps.

    Result is always clamped to ``[0, max_hold_bars - 1]`` so the
    grammar's ``min_hold < max_hold`` validator never fires.
    """
    exit_node = spec_dict.get("exit")
    if not isinstance(exit_node, dict):
        return False
    current = exit_node.get("min_hold_bars", 0)
    if not isinstance(current, int):
        return False
    max_hold = exit_node.get("max_hold_bars")
    if not isinstance(max_hold, int) or max_hold < 2:
        return False
    upper = max(0, max_hold - 1)

    roll = rng.random()
    if roll < 0.20 and current == 0:
        new_val = rng.randint(3, 15)
    elif roll < 0.60:
        factor = rng.uniform(0.75, 1.25)
        new_val = int(round(current * factor))
    else:
        new_val = current + rng.choice([-3, -2, -1, 1, 2, 3])

    new_val = max(0, min(upper, new_val))
    if new_val == current:
        return False  # no-op; let the mutation chain try a different kind
    exit_node["min_hold_bars"] = new_val
    return True


def _mut_toggle_eval_mode(spec_dict: dict, rng: Random) -> bool:
    """Flip `exit.eval_mode` between "close" and "wick".

    Wilson 2026-08-15: added so the GA can search over evaluation
    semantics. Wick-mode genomes model touch-based SL/TP fills
    (realistic broker behaviour); close-mode genomes only exit on a
    full bar close beyond the threshold (backtest-legacy behaviour).
    Both are legitimate strategies — the GA discovers which one the
    edge lives in for a given entry rule + SL/TP combo.
    """
    exit_node = spec_dict.get("exit")
    if not isinstance(exit_node, dict):
        return False
    current = exit_node.get("eval_mode", "close")
    exit_node["eval_mode"] = "wick" if current == "close" else "close"
    return True


_MUTATIONS: tuple[Callable[[dict, Random], bool], ...] = (
    _mut_flip_comparator,
    _mut_perturb_threshold,
    _mut_swap_indicator,
    _mut_change_window,
    _mut_toggle_opposite_signal,
    _mut_perturb_exit_numeric,
    _mut_perturb_min_hold,
    _mut_toggle_eval_mode,
)


def mutate_spec(spec: StrategySpec, rng: Random) -> StrategySpec:
    """Apply a single random mutation to a spec.

    Tries up to 3 mutation types before giving up. If validation of the
    resulting dict fails, returns the original spec — the GA prefers
    losing one mutation opportunity to producing a malformed genome.
    """
    base = spec.model_dump(mode="json")

    candidates = list(_MUTATIONS)
    rng.shuffle(candidates)

    for mut_fn in candidates[:3]:
        candidate_dict = deepcopy(base)
        if mut_fn(candidate_dict, rng):
            try:
                return StrategySpec.model_validate(candidate_dict)
            except ValidationError:
                continue  # try next mutation

    return spec  # nothing applicable / all attempts failed validation


# ─────────────────────────────────────────────────────────────────────────
# Crossover
# ─────────────────────────────────────────────────────────────────────────


def crossover_specs(
    a: StrategySpec, b: StrategySpec, rng: Random
) -> tuple[StrategySpec, StrategySpec]:
    """Single-slot crossover: swap one of (entry.long, entry.short, exit)
    between parents to produce two children. Returns (a, b) unchanged if
    the swap produces an invalid spec.
    """
    a_dict = a.model_dump(mode="json")
    b_dict = b.model_dump(mode="json")

    slot = rng.choice(("long", "short", "exit"))
    if slot in ("long", "short"):
        a_entry, b_entry = a_dict["entry"], b_dict["entry"]
        a_entry[slot], b_entry[slot] = b_entry[slot], a_entry[slot]
    else:  # exit
        a_dict["exit"], b_dict["exit"] = b_dict["exit"], a_dict["exit"]

    try:
        new_a = StrategySpec.model_validate(a_dict)
        new_b = StrategySpec.model_validate(b_dict)
        return new_a, new_b
    except ValidationError:
        return a, b


# ─────────────────────────────────────────────────────────────────────────
# Selection (DEAP-backed)
# ─────────────────────────────────────────────────────────────────────────


def tournament_select(
    population: list[Genome], k: int, tournsize: int = 3
) -> list[Genome]:
    """Pick `k` genomes via `tournsize`-way tournament selection.

    All genomes must have had their fitness set (via `set_fitness()`) before
    this is called — DEAP's selTournament reads `.fitness.values`.
    """
    if not population:
        return []
    return list(tools.selTournament(population, k, tournsize))


def elite_select(population: list[Genome], k: int) -> list[Genome]:
    """Top-k by fitness. Used for elitism."""
    if not population:
        return []
    return list(tools.selBest(population, k))


# ─────────────────────────────────────────────────────────────────────────
# Generation step
# ─────────────────────────────────────────────────────────────────────────


def evolve_to_next_generation(
    population: Population,
    rng: Random,
    *,
    crossover_prob: float = 0.6,
    mutation_prob: float = 0.3,
    elite_fraction: float = 0.10,
    tournament_size: int = 3,
) -> Population:
    """Produce generation N+1 from generation N.

    Pipeline:
    1. Carry the top `elite_fraction` over unchanged.
    2. Fill the remaining slots by:
       - Selecting two parents via tournament.
       - With `crossover_prob` probability, crossover -> two children
         (origin=CROSSOVER); otherwise produce one child via mutation
         (origin=MUTATION).
       - With `mutation_prob` probability, additionally mutate any
         crossover-produced child (origin remains CROSSOVER — the
         dominant operator gets credit).
    3. Trim to original population size.

    Note: parents must have been evaluated (fitness set). The orchestrator
    is responsible for evaluating fitness before calling this.
    """
    pop_size = len(population)
    if pop_size == 0:
        raise ValueError("cannot evolve an empty population")

    n_elites = max(1, int(pop_size * elite_fraction))

    # Elites are the best by fitness, carried unchanged. We make fresh
    # Genome instances so each new generation has independent genome
    # objects (avoids shared identity across generations). LLM
    # provenance (origin/reasoning/call_id) is preserved on carryover
    # because the elite IS the same genome, just persisted into a new
    # generation row — its origin doesn't change.
    elite_genomes_orig = elite_select(list(population.genomes), n_elites)
    next_genomes: list[Genome] = []
    for elite in elite_genomes_orig:
        carryover = Genome(
            spec=elite.spec,
            origin=elite.origin,
            parent_ids=[elite.id] if elite.id else list(elite.parent_ids),
            llm_reasoning=elite.llm_reasoning,
            llm_call_id=elite.llm_call_id,
        )
        next_genomes.append(carryover)

    while len(next_genomes) < pop_size:
        parent_a = tournament_select(list(population.genomes), 1, tournament_size)[0]
        parent_b = tournament_select(list(population.genomes), 1, tournament_size)[0]

        produced: list[Genome] = []
        if rng.random() < crossover_prob and parent_a is not parent_b:
            child_a_spec, child_b_spec = crossover_specs(
                parent_a.spec, parent_b.spec, rng
            )
            for child_spec in (child_a_spec, child_b_spec):
                child = Genome(
                    spec=child_spec,
                    origin=GenomeOrigin.CROSSOVER,
                    parent_ids=[i for i in (parent_a.id, parent_b.id) if i is not None],
                )
                if rng.random() < mutation_prob:
                    child.spec = mutate_spec(child.spec, rng)
                produced.append(child)
        else:
            mutated_spec = mutate_spec(parent_a.spec, rng)
            produced.append(
                Genome(
                    spec=mutated_spec,
                    origin=GenomeOrigin.MUTATION,
                    parent_ids=[parent_a.id] if parent_a.id else [],
                )
            )

        for child in produced:
            next_genomes.append(child)
            if len(next_genomes) >= pop_size:
                break

    next_genomes = next_genomes[:pop_size]
    return Population(
        generation_index=population.generation_index + 1, genomes=next_genomes
    )


__all__ = [
    "crossover_specs",
    "elite_select",
    "evolve_to_next_generation",
    "mutate_spec",
    "tournament_select",
]
