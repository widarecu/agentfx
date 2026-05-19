"""Genome / Population / initialize_population tests."""

from __future__ import annotations

from random import Random

import pytest

from agentfx.agents.evolution.ga import mutate_spec
from agentfx.agents.evolution.population import (
    Genome,
    Population,
    initialize_population,
)
from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    SEED_SPECS,
)


class TestGenome:
    def test_fresh_genome_has_invalid_fitness(self) -> None:
        g = Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)
        assert not g.has_fitness()
        assert g.fitness_value == float("-inf")

    def test_set_fitness_records_value(self) -> None:
        g = Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)
        g.set_fitness(1.42)
        assert g.has_fitness()
        assert g.fitness_value == pytest.approx(1.42)

    def test_each_genome_has_independent_fitness(self) -> None:
        # DEAP creator state is module-global; each Genome must own its
        # own Fitness instance otherwise selection compares wrong values.
        a = Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)
        b = Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)
        a.set_fitness(1.0)
        b.set_fitness(2.0)
        assert a.fitness_value == 1.0
        assert b.fitness_value == 2.0


class TestPopulation:
    def test_len_and_iter(self) -> None:
        genomes = [Genome(spec=s, origin=GenomeOrigin.SEED) for s in SEED_SPECS]
        pop = Population(generation_index=0, genomes=genomes)
        assert len(pop) == 3
        assert list(pop) == genomes

    def test_best_returns_none_when_no_fitness_set(self) -> None:
        pop = Population(
            generation_index=0,
            genomes=[Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)],
        )
        assert pop.best() is None

    def test_best_returns_highest_fitness(self) -> None:
        genomes = []
        for fit in [0.5, 1.5, 0.8]:
            g = Genome(spec=MA_CROSSOVER_SEED, origin=GenomeOrigin.SEED)
            g.set_fitness(fit)
            genomes.append(g)
        pop = Population(generation_index=0, genomes=genomes)
        best = pop.best()
        assert best is not None
        assert best.fitness_value == pytest.approx(1.5)


class TestInitializePopulation:
    def test_includes_every_seed_verbatim(self) -> None:
        rng = Random(0)
        pop = initialize_population(SEED_SPECS, size=10, rng=rng, mutate_fn=mutate_spec)
        seed_genomes = [g for g in pop if g.origin == GenomeOrigin.SEED]
        assert len(seed_genomes) == 3
        seed_specs_in_pop = {id(g.spec) for g in seed_genomes}
        assert len(seed_specs_in_pop) == 3
        # Each original seed must appear exactly once
        for seed in SEED_SPECS:
            assert seed in [g.spec for g in seed_genomes]

    def test_remaining_slots_are_mutated_seeds(self) -> None:
        rng = Random(1)
        pop = initialize_population(SEED_SPECS, size=10, rng=rng, mutate_fn=mutate_spec)
        mutated = [g for g in pop if g.origin == GenomeOrigin.MUTATION]
        assert len(mutated) == 7  # 10 total - 3 seeds

    def test_size_matches_request(self) -> None:
        rng = Random(2)
        pop = initialize_population(SEED_SPECS, size=30, rng=rng, mutate_fn=mutate_spec)
        assert len(pop) == 30
        assert pop.generation_index == 0

    def test_size_below_seed_count_raises(self) -> None:
        rng = Random(3)
        with pytest.raises(ValueError, match="must be >="):
            initialize_population(SEED_SPECS, size=2, rng=rng, mutate_fn=mutate_spec)

    def test_empty_seeds_raises(self) -> None:
        rng = Random(4)
        with pytest.raises(ValueError, match="at least one seed"):
            initialize_population([], size=10, rng=rng, mutate_fn=mutate_spec)

    def test_run_id_assigns_seed_ids_and_mutation_parent_ids(self) -> None:
        """With run_id provided, seeds get persister-style IDs and each
        mutated copy carries the seed's ID as its parent. This is what
        makes the gen-0 lineage visible in the portal."""
        rng = Random(7)
        pop = initialize_population(
            SEED_SPECS,
            size=10,
            rng=rng,
            mutate_fn=mutate_spec,
            run_id="evorun_test_abc",
        )
        seeds = [g for g in pop if g.origin == GenomeOrigin.SEED]
        # Seeds get pre-assigned IDs matching the persister's format
        assert seeds[0].id == "genome_gen_evorun_test_abc_000_000"
        assert seeds[1].id == "genome_gen_evorun_test_abc_000_001"
        assert seeds[2].id == "genome_gen_evorun_test_abc_000_002"
        seed_id_set = {g.id for g in seeds}

        # Every mutation references one of the seed IDs as its parent
        mutations = [g for g in pop if g.origin == GenomeOrigin.MUTATION]
        assert len(mutations) == 7
        for m in mutations:
            assert len(m.parent_ids) == 1
            assert m.parent_ids[0] in seed_id_set

    def test_no_run_id_keeps_legacy_empty_parent_ids(self) -> None:
        """Without run_id (test/legacy path), seeds have no IDs and
        mutations have empty parent_ids — preserves the prior behaviour
        for code paths that don't need lineage tracking."""
        rng = Random(8)
        pop = initialize_population(SEED_SPECS, size=10, rng=rng, mutate_fn=mutate_spec)
        for g in pop:
            if g.origin == GenomeOrigin.SEED:
                assert g.id is None
            else:
                assert g.parent_ids == []

    def test_reproducible_with_same_seed(self) -> None:
        # Same RNG seed → same population structure
        rng_a = Random(99)
        rng_b = Random(99)
        pop_a = initialize_population(
            [MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED, DONCHIAN_BREAKOUT_SEED],
            size=10,
            rng=rng_a,
            mutate_fn=mutate_spec,
        )
        pop_b = initialize_population(
            [MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED, DONCHIAN_BREAKOUT_SEED],
            size=10,
            rng=rng_b,
            mutate_fn=mutate_spec,
        )
        for g_a, g_b in zip(pop_a, pop_b):
            assert g_a.spec.model_dump() == g_b.spec.model_dump()
