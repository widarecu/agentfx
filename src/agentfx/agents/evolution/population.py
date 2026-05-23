"""Genome and Population containers for the evolutionary loop.

A `Genome` wraps a `StrategySpec` with the metadata the GA and persistence
layer need: origin (seed/crossover/mutation/llm), parent lineage, fitness
(via DEAP's `Fitness` so selection algorithms work), and metrics.

A `Population` is just a generation of genomes plus its index. Persistence
of genome rows to Postgres happens in the orchestrator/service layer; this
module is in-memory only.

The DEAP fitness wrapper is created at module import time (idempotent —
DEAP's `creator` rejects duplicate names cleanly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from deap import base, creator

from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl import StrategySpec

# DEAP creator state is module-global. Re-running tests in the same process
# would otherwise raise "class FitnessMax already exists" on the second
# import. Guard accordingly.
if not hasattr(creator, "FitnessMax"):
    creator.create("FitnessMax", base.Fitness, weights=(1.0,))


@dataclass
class Genome:
    """A single genome in the GA population.

    The DSL spec is the gene; everything else is bookkeeping. `id` is
    populated by the persistence layer when the genome is saved; before
    that it's None.
    """

    spec: StrategySpec
    origin: GenomeOrigin
    parent_ids: list[str] = field(default_factory=list)
    metrics: dict | None = None
    id: str | None = None
    llm_reasoning: str | None = None
    llm_call_id: str | None = None
    fitness: object = field(init=False)

    def __post_init__(self) -> None:
        # Each genome gets its own FitnessMax instance — DEAP's selection
        # functions read .fitness.values, so it must be a separate object.
        self.fitness = creator.FitnessMax()

    def set_fitness(self, value: float) -> None:
        """Record the fitness value (single objective for v1)."""
        self.fitness.values = (float(value),)

    @property
    def fitness_value(self) -> float:
        """Convenience accessor returning -inf if fitness wasn't set."""
        if not self.fitness.valid:
            return float("-inf")
        return float(self.fitness.values[0])

    def has_fitness(self) -> bool:
        return bool(self.fitness.valid)


@dataclass
class Population:
    """The set of genomes in one generation."""

    generation_index: int
    genomes: list[Genome]

    def __len__(self) -> int:
        return len(self.genomes)

    def __iter__(self):
        return iter(self.genomes)

    def best(self) -> Genome | None:
        """Highest-fitness genome in the population, or None if no genome
        has been evaluated yet."""
        evaluated = [g for g in self.genomes if g.has_fitness()]
        if not evaluated:
            return None
        return max(evaluated, key=lambda g: g.fitness_value)


def initialize_population(
    seeds: list[StrategySpec] | list[Genome],
    size: int,
    rng: Random,
    *,
    mutate_fn,
    run_id: str | None = None,
) -> Population:
    """Construct generation 0.

    Strategy: include every seed verbatim, then fill the rest of the
    population with mutated copies of randomly-chosen seeds. This gives the
    GA known-good priors plus locally-varied starting points — better than
    random initialization, which often produces uninteresting strategies.

    The `seeds` argument accepts EITHER a list of bare `StrategySpec`s
    (back-compat — every spec is wrapped as `origin=SEED`) OR a list of
    pre-built `Genome` objects with the caller's chosen `origin`. The
    Genome path is what the orchestrator uses for the composite gen-0
    (canonical seeds + warm-start + LLM-proposed), so each starting
    point keeps its true origin (`seed` / `warm_start` / `llm`) rather
    than being flattened to a generic SEED.

    Lineage tracking: when `run_id` is provided, we pre-assign deterministic
    IDs to the seed genomes (matching the persister's
    `generate_genome_id(generation_id, index)` convention — the seeds are
    indices 0..N-1 of gen 0) and record those IDs as `parent_ids` on each
    mutated copy. This lets the portal show "this mutation came from MA
    crossover seed" without an extra DB round-trip. With `run_id=None`
    (unit tests that don't care about lineage), mutations have empty
    parent_ids — matching the legacy behaviour.

    Args:
        seeds: Either a list of `StrategySpec` (all tagged SEED) or a
            list of pre-built `Genome` (each retains its `origin`).
        size: Total population size for generation 0.
        rng: Random source (passed in for reproducibility).
        mutate_fn: Callable `(spec, rng) -> spec` — kept as a parameter
            rather than imported here to avoid a circular import with
            `ga.py`. Pass `agentfx.agents.evolution.ga.mutate_spec` in the
            normal case.
        run_id: Run identifier used to pre-compute the seeds' eventual
            persister IDs. Required for mutated copies to carry correct
            `parent_ids`; safe to omit in tests.

    Returns:
        Population at generation_index=0 with `size` genomes.
    """
    if not seeds:
        raise ValueError("at least one seed strategy is required")

    # Normalize to a list of Genome objects. Plain StrategySpecs get the
    # legacy SEED origin so existing callers (tests) keep working.
    seed_genomes: list[Genome] = []
    for s in seeds:
        if isinstance(s, Genome):
            seed_genomes.append(s)
        else:
            seed_genomes.append(Genome(spec=s, origin=GenomeOrigin.SEED))

    if size < len(seed_genomes):
        raise ValueError(
            f"population size ({size}) must be >= number of seeds ({len(seed_genomes)})"
        )

    # Pre-compute the seeds' eventual persister IDs so mutated copies can
    # record their lineage. The persister assigns
    # `genome_{gen_id}_{index_in_gen:03d}` where the first N indices are
    # the N seeds in insertion order — see
    # `agentfx.api.services.evolution.SqlAlchemyPersister.save_generation`.
    if run_id is not None:
        gen0_id = f"gen_{run_id}_000"
        for i, g in enumerate(seed_genomes):
            if g.id is None:
                g.id = f"genome_{gen0_id}_{i:03d}"

    genomes: list[Genome] = list(seed_genomes)

    while len(genomes) < size:
        parent_idx = rng.randrange(len(seed_genomes))
        parent = seed_genomes[parent_idx]
        mutated = mutate_fn(parent.spec, rng)
        parent_ids = [parent.id] if parent.id is not None else []
        genomes.append(
            Genome(
                spec=mutated,
                origin=GenomeOrigin.MUTATION,
                parent_ids=parent_ids,
            )
        )

    return Population(generation_index=0, genomes=genomes)


__all__ = [
    "Genome",
    "Population",
    "initialize_population",
]
