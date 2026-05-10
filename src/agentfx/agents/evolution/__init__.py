"""Evolutionary strategy discovery package (v0.3.0).

The autonomous loop's core: a population of `StrategySpec` genomes evolved
generation-to-generation under fitness selection, with periodic LLM
hypothesis injection and walk-forward validation at the end.

Sub-modules:
- population: `Genome` dataclass + `Population` container + gen-0 init
- ga: mutation, crossover, tournament selection, generation-to-generation step
- fitness: backtest-based fitness scoring with diversity penalty + Gate B dilution
- llm_client: Anthropic SDK wrapper for hypothesis injection (US-005)
- prompts: prompt templates and LLM-facing response schema
"""

from agentfx.agents.evolution.fitness import (
    compute_fitness,
    is_near_elite,
    spec_edit_distance,
)
from agentfx.agents.evolution.ga import (
    crossover_specs,
    elite_select,
    evolve_to_next_generation,
    mutate_spec,
    tournament_select,
)
from agentfx.agents.evolution.llm_client import (
    InjectionProposal,
    InjectionResult,
    LLMCallCost,
    LLMHypothesisInjector,
    MODEL_PRICING,
    ModelPricing,
)
from agentfx.agents.evolution.population import (
    Genome,
    Population,
    initialize_population,
)
from agentfx.agents.evolution.runner import (
    ResourceLimits,
    apply_resource_limits,
)

__all__ = [
    "Genome",
    "InjectionProposal",
    "InjectionResult",
    "LLMCallCost",
    "LLMHypothesisInjector",
    "MODEL_PRICING",
    "ModelPricing",
    "Population",
    "ResourceLimits",
    "apply_resource_limits",
    "compute_fitness",
    "crossover_specs",
    "elite_select",
    "evolve_to_next_generation",
    "initialize_population",
    "is_near_elite",
    "mutate_spec",
    "spec_edit_distance",
    "tournament_select",
]
