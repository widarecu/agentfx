"""Pydantic schemas for the v0.3.0 Autonomous Strategy Loop API.

Mirrors the optimization schemas conventions: `*Base` / `*Create` /
`*Public` / `*List` per resource. The orchestrator's `OrchestratorConfig`
and `StopConditions` are exposed as nested schemas so a single
`POST /evolution-runs` body fully configures a run.

See planning/requirements/US-003-evolution-run-lifecycle.md for the API
contract and US-016-evolution-observability.md for the decision-log
endpoints.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from .base import BaseSchema


# ─────────────────────────────────────────────────────────────────────────
# Configuration sub-schemas
# ─────────────────────────────────────────────────────────────────────────


class StopConditionsSchema(BaseModel):
    """Stop conditions for an evolution run. First-to-fire wins."""

    max_generations: int = Field(default=30, ge=1, le=200)
    max_wall_clock_seconds: int = Field(default=6 * 3600, ge=60)
    target_fitness: float = Field(default=1.5)
    no_improvement_generations: int = Field(default=5, ge=1)


class OrchestratorConfigSchema(BaseModel):
    """Per-run orchestrator config. Persisted on the run row as JSON.

    All fields are optional — when used as an override on
    `EvolutionRunCreate.config`, the service layer merges any provided
    fields over the defaults. The top-level `EvolutionRunCreate.instrument`
    + `granularity` are authoritative; the same fields here are accepted
    for self-describing rows but ignored on conflict.
    """

    instrument: str | None = Field(default=None, max_length=10)
    granularity: str | None = Field(default=None, max_length=5)
    population_size: int = Field(default=30, ge=4, le=200)
    llm_injection_every_n: int = Field(default=3, ge=1)
    n_llm_proposals: int = Field(default=5, ge=1, le=10)
    n_validation_folds: int = Field(default=4, ge=2, le=10)
    top_n_to_validate: int = Field(default=5, ge=1)
    crossover_prob: float = Field(default=0.6, ge=0.0, le=1.0)
    mutation_prob: float = Field(default=0.3, ge=0.0, le=1.0)
    elite_fraction: float = Field(default=0.10, ge=0.0, le=0.5)
    tournament_size: int = Field(default=3, ge=2)
    rng_seed: int = Field(default=42)
    account_balance: float = Field(default=10000.0, gt=0)
    position_size: int = Field(default=10000, gt=0)
    spread_pips: float | None = None


# ─────────────────────────────────────────────────────────────────────────
# Evolution run
# ─────────────────────────────────────────────────────────────────────────


class EvolutionRunCreate(BaseModel):
    """POST body for creating a new evolution run."""

    instrument: str = Field(min_length=1, max_length=10)
    granularity: str = Field(default="M1", max_length=5)
    data_years: int = Field(default=5, ge=1, le=20)

    # Optional overrides — if absent, sensible defaults from the
    # OrchestratorConfigSchema/StopConditionsSchema apply.
    config: OrchestratorConfigSchema | None = None
    stop_conditions: StopConditionsSchema | None = None
    llm_budget_usd: float = Field(default=5.0, ge=0.0)


class EvolutionRunCreated(BaseModel):
    """202 Accepted response from POST /evolution-runs."""

    run_id: str
    status: str
    status_url: str
    instrument: str
    granularity: str
    estimated_max_generations: int
    created_at: datetime


class EvolutionRunPublic(BaseSchema):
    """Detail view returned by GET /evolution-runs/{id}."""

    id: str
    status: str
    instrument: str
    granularity: str
    config_json: dict[str, Any]
    stop_conditions_json: dict[str, Any]
    stop_reason: str | None = None
    current_generation: int
    best_genome_id: str | None = None
    llm_cost_usd: Decimal
    process_pid: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class EvolutionRunList(BaseModel):
    items: list[EvolutionRunPublic]
    total: int
    page: int
    page_size: int


# ─────────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────────


class GenerationPublic(BaseSchema):
    """One row from /evolution-runs/{id}/generations."""

    id: str
    run_id: str
    index: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    llm_invoked: bool
    llm_call_id: str | None = None
    best_fitness: float | None = None


class GenerationList(BaseModel):
    items: list[GenerationPublic]
    total: int


# ─────────────────────────────────────────────────────────────────────────
# Strategy genome (with optional lineage / validation folds)
# ─────────────────────────────────────────────────────────────────────────


class ValidationFoldPublic(BaseSchema):
    """One walk-forward fold's metrics, returned in the genome detail view."""

    id: str
    fold_index: int
    fold_start: datetime
    fold_end: datetime
    is_oos: bool
    sharpe: float | None = None
    max_drawdown: float | None = None
    hit_rate: float | None = None
    n_trades: int | None = None
    passed_gate_b: bool


class StrategyGenomePublic(BaseSchema):
    """Detail view for one genome.

    Lineage children are returned as a list of `{id, generation_index}`
    summaries — the portal can navigate to each via its own GET call.
    """

    id: str
    generation_id: str
    generation_index: int
    dsl_json: dict[str, Any]
    parent_ids: list[str]
    origin: str  # GenomeOrigin value
    fitness: float | None = None
    validator_passed: bool | None = None
    validation_failures: list[str] = Field(default_factory=list)
    llm_reasoning: str | None = None
    llm_call_id: str | None = None
    created_at: datetime
    children: list[dict[str, Any]] = Field(default_factory=list)
    validation_folds: list[ValidationFoldPublic] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Decision log
# ─────────────────────────────────────────────────────────────────────────


class DecisionLogPublic(BaseSchema):
    """One append-only audit-log row."""

    id: str
    run_id: str
    generation_id: str | None = None
    agent_type: str
    action: str
    inputs_json: dict[str, Any] | None = None
    outputs_json: dict[str, Any] | None = None
    reasoning_text: str | None = None
    llm_call_id: str | None = None
    created_at: datetime


class DecisionLogList(BaseModel):
    items: list[DecisionLogPublic]
    total: int
    page: int
    page_size: int


__all__ = [
    "DecisionLogList",
    "DecisionLogPublic",
    "EvolutionRunCreate",
    "EvolutionRunCreated",
    "EvolutionRunList",
    "EvolutionRunPublic",
    "GenerationList",
    "GenerationPublic",
    "OrchestratorConfigSchema",
    "StopConditionsSchema",
    "StrategyGenomePublic",
    "ValidationFoldPublic",
]
