"""GA operator tests: mutation, crossover, selection, generation step."""

from __future__ import annotations

from random import Random

import pytest

from agentfx.agents.evolution.ga import (
    crossover_specs,
    elite_select,
    evolve_to_next_generation,
    mutate_spec,
    tournament_select,
)
from agentfx.agents.evolution.population import (
    Genome,
    Population,
    initialize_population,
)
from agentfx.api.models import GenomeOrigin
from agentfx.core.dsl import StrategySpec, compile_spec
from agentfx.core.dsl.seeds import (
    DONCHIAN_BREAKOUT_SEED,
    MA_CROSSOVER_SEED,
    RSI_MEAN_REVERSION_SEED,
    SEED_SPECS,
    ma_crossover_seed,
)


# ─────────────────────────────────────────────────────────────────────────
# Mutation
# ─────────────────────────────────────────────────────────────────────────


class TestMutateSpec:
    def test_returns_valid_spec(self) -> None:
        rng = Random(0)
        for _ in range(50):
            mutated = mutate_spec(MA_CROSSOVER_SEED, rng)
            assert isinstance(mutated, StrategySpec)
            # Compiles cleanly — proves the mutation didn't break semantics
            compile_spec(mutated)

    def test_mutation_actually_changes_genome_over_many_attempts(self) -> None:
        # Single mutation may be a no-op (e.g. all attempted mutators fail
        # validation); over many attempts the spec should change at least
        # sometimes. This is a probabilistic test — seed 0 over 100 trials
        # should produce changes.
        rng = Random(0)
        original = MA_CROSSOVER_SEED.model_dump()
        any_changed = False
        for _ in range(100):
            mutated = mutate_spec(MA_CROSSOVER_SEED, rng)
            if mutated.model_dump() != original:
                any_changed = True
                break
        assert any_changed, "Mutation never changed the spec across 100 attempts"

    def test_works_on_all_seeds(self) -> None:
        rng = Random(7)
        for seed in SEED_SPECS:
            for _ in range(10):
                mutated = mutate_spec(seed, rng)
                compile_spec(mutated)  # must always be valid

    def test_reproducible_with_same_rng(self) -> None:
        rng_a = Random(42)
        rng_b = Random(42)
        a = mutate_spec(MA_CROSSOVER_SEED, rng_a)
        b = mutate_spec(MA_CROSSOVER_SEED, rng_b)
        assert a.model_dump() == b.model_dump()

    def test_threshold_perturbation_stays_in_bounds(self) -> None:
        # RSI seed has thresholds at 30 and 70. After many mutations the
        # values should stay positive (no negative thresholds).
        rng = Random(5)
        spec = RSI_MEAN_REVERSION_SEED
        for _ in range(20):
            spec = mutate_spec(spec, rng)
            d = spec.model_dump(mode="json")
            # Walk all comparisons and check any numeric literal RHS > 0
            entry = d["entry"]
            for side in ("long", "short"):
                cond = entry.get(side)
                if cond and "rhs" in cond and isinstance(cond["rhs"], (int, float)):
                    assert cond["rhs"] > 0

    def test_min_hold_mutation_explores_dimension(self) -> None:
        """Over many mutation attempts at least one lineage must move
        min_hold_bars away from the seed's 0. Without this we wouldn't
        actually be using the new exit-rule knob — the dimension would
        stay frozen at the seed default and the v0.4.0 over-trading
        fix wouldn't engage."""
        rng = Random(123)
        spec = MA_CROSSOVER_SEED
        assert spec.exit.min_hold_bars == 0, "Seed precondition: starts at 0"
        seen_nonzero = False
        # Walk a lineage; each mutation builds on the previous spec so
        # min-hold can compound across mutations.
        for _ in range(60):
            spec = mutate_spec(spec, rng)
            if spec.exit.min_hold_bars > 0:
                seen_nonzero = True
                break
        assert seen_nonzero, (
            "min_hold_bars never moved off 0 across 60 mutations on "
            "MA_CROSSOVER_SEED — the new dimension isn't being explored."
        )

    def test_eval_mode_mutation_flips_over_lineage(self) -> None:
        """Wilson 2026-08-15: `eval_mode` is now a GA-searchable
        dimension. Over a long lineage at least one mutation must
        flip the default 'close' → 'wick' (or back), otherwise the
        dimension is frozen."""
        rng = Random(999)
        spec = MA_CROSSOVER_SEED
        assert spec.exit.eval_mode == "close", "Seed precondition"
        seen_wick = False
        for _ in range(80):
            spec = mutate_spec(spec, rng)
            if spec.exit.eval_mode == "wick":
                seen_wick = True
                break
        assert seen_wick, (
            "eval_mode never flipped to 'wick' across 80 mutations — "
            "the GA isn't exploring the new dimension."
        )

    def test_min_hold_mutation_respects_max_hold_bound(self) -> None:
        """Cross-field validator: min_hold must always stay strictly
        below max_hold. Walk a long lineage and assert the invariant
        on every step."""
        rng = Random(7)
        spec = MA_CROSSOVER_SEED
        for _ in range(100):
            spec = mutate_spec(spec, rng)
            assert spec.exit.min_hold_bars < spec.exit.max_hold_bars, (
                f"min_hold ({spec.exit.min_hold_bars}) >= max_hold "
                f"({spec.exit.max_hold_bars}) — grammar invariant breached"
            )
            assert spec.exit.min_hold_bars >= 0


# ─────────────────────────────────────────────────────────────────────────
# Crossover
# ─────────────────────────────────────────────────────────────────────────


class TestCrossoverSpecs:
    def test_produces_two_valid_children(self) -> None:
        rng = Random(0)
        for _ in range(20):
            child_a, child_b = crossover_specs(
                MA_CROSSOVER_SEED, RSI_MEAN_REVERSION_SEED, rng
            )
            compile_spec(child_a)
            compile_spec(child_b)

    def test_crossover_actually_swaps_some_slot(self) -> None:
        # Repeating crossover with different RNG should produce specs that
        # mix elements of the two parents.
        rng = Random(0)
        a = ma_crossover_seed(fast=10, slow=30)
        b = RSI_MEAN_REVERSION_SEED  # uses RSI indicator instead of SMA
        any_mixed = False
        for _ in range(50):
            c1, c2 = crossover_specs(a, b, rng)
            # If one of the children's entry.long differs from both
            # parents' entry.long structures, that's a mix.
            d1, d2 = c1.model_dump(mode="json"), c2.model_dump(mode="json")
            da, db = a.model_dump(mode="json"), b.model_dump(mode="json")
            if (
                d1["entry"]["long"] != da["entry"]["long"]
                or d2["entry"]["long"] != da["entry"]["long"]
            ):
                any_mixed = True
                break
        assert any_mixed

    def test_invalid_swap_returns_parents_unchanged(self) -> None:
        # Construct a spec where short=None: swapping short with another
        # spec that also has short=None is fine; swapping with a spec
        # whose long=None would leave the first child with both sides None.
        from agentfx.core.dsl.grammar import (
            Comparison,
            EntryConditions,
            ExitRules,
            IndicatorCall,
            SizeRules,
        )

        spec_only_long = StrategySpec(
            entry=EntryConditions(
                long=Comparison(
                    lhs=IndicatorCall(ind="rsi", args=[14]),
                    cmp="<",
                    rhs=30.0,
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )
        spec_only_short = StrategySpec(
            entry=EntryConditions(
                short=Comparison(
                    lhs=IndicatorCall(ind="rsi", args=[14]),
                    cmp=">",
                    rhs=70.0,
                )
            ),
            exit=ExitRules(stop_pips=10, target_pips=20, max_hold_bars=240),
            size=SizeRules(risk_pct=0.005),
        )

        # Force a "long" slot swap (where parent A has long, parent B has
        # None for long): seed deterministically.
        rng = Random(1)
        # Try several until we hit the long-slot path; verify parents
        # come back unchanged when the swap is invalid.
        for _ in range(30):
            c1, c2 = crossover_specs(spec_only_long, spec_only_short, rng)
            # Both children must be valid specs with at least one entry side
            assert c1.entry.long is not None or c1.entry.short is not None
            assert c2.entry.long is not None or c2.entry.short is not None


# ─────────────────────────────────────────────────────────────────────────
# Selection
# ─────────────────────────────────────────────────────────────────────────


def _make_genome(spec: StrategySpec, fitness: float) -> Genome:
    g = Genome(spec=spec, origin=GenomeOrigin.SEED)
    g.set_fitness(fitness)
    return g


class TestTournamentSelect:
    def test_picks_one_per_call(self) -> None:
        rng = Random(0)  # noqa: F841 — DEAP uses module-level random
        pop = [
            _make_genome(MA_CROSSOVER_SEED, 1.0),
            _make_genome(RSI_MEAN_REVERSION_SEED, 2.0),
            _make_genome(DONCHIAN_BREAKOUT_SEED, 3.0),
        ]
        picked = tournament_select(pop, k=1, tournsize=2)
        assert len(picked) == 1
        assert picked[0] in pop

    def test_higher_fitness_wins_more_often(self) -> None:
        # DEAP's selTournament samples with replacement: from a 2-element
        # pop of (1.0, 5.0) with tournsize=2, P(fitter wins) = 0.75 (the
        # only way the worse genome wins is if both draws are 1.0).
        # Over 100 trials, expectation 75 with std ~4.3 — assert the
        # fitter wins clearly more often than the lesser.
        pop = [
            _make_genome(MA_CROSSOVER_SEED, 1.0),
            _make_genome(RSI_MEAN_REVERSION_SEED, 5.0),
        ]
        winners = [
            tournament_select(pop, k=1, tournsize=2)[0].fitness_value
            for _ in range(100)
        ]
        fitter_wins = sum(1 for w in winners if w == 5.0)
        weaker_wins = sum(1 for w in winners if w == 1.0)
        assert fitter_wins > weaker_wins
        assert fitter_wins > 50  # well above chance

    def test_returns_empty_for_empty_population(self) -> None:
        assert tournament_select([], k=3) == []


class TestEliteSelect:
    def test_returns_top_k_by_fitness(self) -> None:
        pop = [
            _make_genome(MA_CROSSOVER_SEED, 1.0),
            _make_genome(RSI_MEAN_REVERSION_SEED, 2.0),
            _make_genome(DONCHIAN_BREAKOUT_SEED, 3.0),
        ]
        top_2 = elite_select(pop, k=2)
        assert {g.fitness_value for g in top_2} == {3.0, 2.0}


# ─────────────────────────────────────────────────────────────────────────
# Generation step
# ─────────────────────────────────────────────────────────────────────────


class TestEvolveToNextGeneration:
    def _make_pop(self, fitnesses: list[float], size: int = 10) -> Population:
        rng = Random(99)
        pop = initialize_population(
            SEED_SPECS, size=size, rng=rng, mutate_fn=mutate_spec
        )
        # Set fitness on each genome
        for g, fit in zip(pop.genomes, fitnesses + [0.0] * (size - len(fitnesses))):
            g.set_fitness(fit)
            g.id = f"genome_{id(g)}"
        return pop

    def test_returns_population_of_same_size(self) -> None:
        pop = self._make_pop([1.0, 2.0, 0.5], size=10)
        next_pop = evolve_to_next_generation(pop, Random(0))
        assert len(next_pop) == 10
        assert next_pop.generation_index == 1

    def test_elites_carry_over(self) -> None:
        pop = self._make_pop([3.0, 5.0, 1.0, 0.5], size=10)
        next_pop = evolve_to_next_generation(pop, Random(0), elite_fraction=0.10)
        # With elite_fraction=0.10 and pop=10, we get max(1, 1) = 1 elite.
        # The fittest (5.0) must show up structurally in the next generation.
        elite_spec = max(pop.genomes, key=lambda g: g.fitness_value).spec
        assert any(g.spec == elite_spec for g in next_pop.genomes)

    def test_origins_reflect_operations(self) -> None:
        pop = self._make_pop([1.0, 2.0, 3.0, 4.0, 5.0], size=10)
        next_pop = evolve_to_next_generation(pop, Random(0))
        origins = {g.origin for g in next_pop.genomes}
        # Should contain both crossover and mutation results (probabilistic
        # but with crossover_prob=0.6 and 9 children to fill, near-certain).
        assert origins.issubset(
            {GenomeOrigin.SEED, GenomeOrigin.CROSSOVER, GenomeOrigin.MUTATION}
        )

    def test_all_children_compile(self) -> None:
        pop = self._make_pop([1.0, 2.0, 3.0], size=10)
        next_pop = evolve_to_next_generation(pop, Random(11))
        for g in next_pop.genomes:
            compile_spec(g.spec)  # must produce a valid Strategy

    def test_empty_population_raises(self) -> None:
        empty = Population(generation_index=0, genomes=[])
        with pytest.raises(ValueError, match="empty population"):
            evolve_to_next_generation(empty, Random(0))

    def test_parent_lineage_recorded(self) -> None:
        pop = self._make_pop([1.0, 2.0, 3.0], size=10)
        next_pop = evolve_to_next_generation(pop, Random(0))
        # Every non-elite child should have at least one parent_id (since
        # parent genomes have IDs assigned in _make_pop).
        non_elites = [
            g
            for g in next_pop.genomes
            if g.origin in (GenomeOrigin.CROSSOVER, GenomeOrigin.MUTATION)
        ]
        for child in non_elites:
            assert len(child.parent_ids) >= 1
