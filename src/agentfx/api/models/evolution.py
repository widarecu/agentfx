"""SQLAlchemy models for the v0.3.0 Autonomous Strategy Loop.

Five new tables wired to the same `Base` as the v0.2.0 optimization family:

- evolution_runs: top-level run lifecycle (status, config, stop reason, LLM cost)
- generations: per-generation lineage within a run
- strategy_genomes: individual DSL specs evaluated, with origin + lineage
- validation_folds: walk-forward fold metrics (one row per fold per genome)
- decision_logs: append-only audit trail of every agent action

ID format follows the v0.2.0 convention of semantic string keys
(evorun_{date}_{short_uuid}, gen_{run_id}_{index}, …) rather than raw UUIDs
so they're human-readable in logs and the portal. Decision-log rows use raw
UUID hex since they're append-only with no need for a semantic key.

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md for the full contract.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class EvolutionRunStatus(str, Enum):
    """Lifecycle states of an evolution run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"  # manual halt via POST .../stop


class GenomeOrigin(str, Enum):
    """How a genome came to exist in its generation."""

    SEED = "seed"  # gen-0 canonical seed (MA/RSI/Donchian DSL)
    WARM_START = "warm_start"  # gen-0 import from a prior completed run
    CROSSOVER = "crossover"  # GA crossover of two parents
    MUTATION = "mutation"  # GA mutation of a single parent
    LLM = "llm"  # LLM proposal (gen-0 seed or mid-run injection)


class AgentType(str, Enum):
    """Which agent wrote a decision-log row.

    The values are stored as strings (not Python enum) for portability. The
    set is open for extension in v0.5.0 when new agents (research, regime,
    etc.) are added.
    """

    ORCHESTRATOR = "orchestrator"
    GA = "ga"
    LLM = "llm"
    VALIDATOR = "validator"
    # v0.6.0 (US-018): the caller isn't an autonomous agent — a real user
    # explicitly acted (create/delete run, approve/reject genome, deploy
    # paper, kill paper, update credentials). `user_id` on the row
    # identifies which user; `action` names the specific verb.
    USER = "user"


class UserAction(str, Enum):
    """Action strings written to DecisionLog when `agent_type == USER`.

    New in v0.6.0 for US-018 and the paper-trading approval state
    machine. `DecisionLog.action` is a plain String(40) so these values
    are enforced at the write site (not at the DB level) — the enum is
    the canonical vocabulary reviewers should match against.
    """

    USER_CREATE_RUN = "user_create_run"
    USER_DELETE_RUN = "user_delete_run"
    USER_STOP_RUN = "user_stop_run"
    USER_APPROVE_GENOME = "user_approve_genome"
    USER_REJECT_GENOME = "user_reject_genome"
    USER_DEPLOY_PAPER = "user_deploy_paper"
    USER_KILL_PAPER = "user_kill_paper"
    USER_UPDATE_CREDENTIALS = "user_update_credentials"


# ─────────────────────────────────────────────────────────────────────────


class EvolutionRun(Base):
    """Top-level evolution run.

    One row per run; persisted by `POST /api/v1/evolution-runs` and updated
    by the orchestrator's child process.
    """

    __tablename__ = "evolution_runs"

    # Format: evorun_{YYYYMMDD}_{short_uuid}
    id = Column(String(50), primary_key=True, index=True)

    status = Column(
        SQLEnum(
            EvolutionRunStatus,
            values_callable=lambda x: [e.value for e in x],
            name="evolutionrunstatus",
        ),
        nullable=False,
        default=EvolutionRunStatus.QUEUED,
        index=True,
    )

    instrument = Column(String(10), nullable=False, index=True)
    granularity = Column(String(5), nullable=False)

    # Full request body for reproducibility (US-003 contract)
    config_json = Column(JSON, nullable=False)
    # Stop conditions snapshot (max_generations, max_wall_clock_seconds, …)
    stop_conditions_json = Column(JSON, nullable=False)
    # One of: max_generations|max_wall_clock|target_reached|no_improvement|
    # llm_budget_exhausted|process_crashed|manual_stop
    stop_reason = Column(String(40), nullable=True)

    current_generation = Column(Integer, nullable=False, default=0)
    best_genome_id = Column(String(80), nullable=True)
    llm_cost_usd = Column(Numeric(10, 4), nullable=False, default=0)
    process_pid = Column(Integer, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Relationships
    generations = relationship(
        "Generation",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Generation.index",
    )
    decisions = relationship(
        "DecisionLog",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="DecisionLog.created_at",
    )
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<EvolutionRun {self.id} {self.instrument}/{self.granularity} ({self.status})>"


class Generation(Base):
    """A single generation within an evolution run."""

    __tablename__ = "generations"

    # Format: gen_{run_id}_{index}
    id = Column(String(80), primary_key=True, index=True)
    run_id = Column(
        String(50),
        ForeignKey("evolution_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    index = Column(Integer, nullable=False)  # 0-based generation number
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    llm_invoked = Column(Boolean, nullable=False, default=False)
    llm_call_id = Column(String(80), nullable=True)
    best_fitness = Column(Float, nullable=True)

    run = relationship("EvolutionRun", back_populates="generations")
    genomes = relationship(
        "StrategyGenome",
        back_populates="generation",
        cascade="all, delete-orphan",
        order_by="desc(StrategyGenome.fitness)",
    )

    __table_args__ = (
        UniqueConstraint("run_id", "index", name="uq_generation_run_index"),
    )

    def __repr__(self) -> str:
        return f"<Generation {self.id} gen={self.index}>"


class StrategyGenome(Base):
    """A single strategy genome — a DSL spec with metrics + lineage."""

    __tablename__ = "strategy_genomes"

    # Format: genome_{generation_id}_{index_in_generation}
    id = Column(String(120), primary_key=True, index=True)
    generation_id = Column(
        String(80),
        ForeignKey("generations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The full StrategySpec (Pydantic model) serialized as JSON
    dsl_json = Column(JSON, nullable=False)
    # List of parent genome IDs: [] for seeds/llm, [a] for mutation, [a, b] for crossover
    parent_ids = Column(JSON, nullable=False, default=list)
    origin = Column(
        SQLEnum(
            GenomeOrigin,
            values_callable=lambda x: [e.value for e in x],
            name="genomeorigin",
        ),
        nullable=False,
        index=True,
    )

    fitness = Column(Float, nullable=True)
    # Full metrics dict from the fitness evaluation: per-fold Sharpe /
    # MaxDD / hit-rate / trades / CAGR for every training fold, plus
    # the penalty breakdown (raw fitness, each penalty's contribution,
    # final fitness). Populated by the orchestrator's
    # _evaluate_population_fitness pipeline. Nullable for back-compat
    # with rows written before v0.5.0 and for elites that carried over
    # without re-evaluation.
    metrics_json = Column(JSON, nullable=True)
    validator_passed = Column(Boolean, nullable=True)
    # Human-readable failure messages from evaluate_gate_b()
    validation_failures = Column(JSON, nullable=False, default=list)

    # Only populated when origin='llm'
    llm_reasoning = Column(Text, nullable=True)
    llm_call_id = Column(String(80), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    generation = relationship("Generation", back_populates="genomes")
    validation_folds = relationship(
        "ValidationFold",
        back_populates="genome",
        cascade="all, delete-orphan",
        order_by="ValidationFold.fold_index",
    )

    __table_args__ = (
        # Top-N-by-fitness queries are the dominant access pattern at the
        # generation level; this composite index supports them efficiently.
        Index(
            "idx_genome_generation_fitness",
            "generation_id",
            "fitness",
        ),
    )

    def __repr__(self) -> str:
        return f"<StrategyGenome {self.id} origin={self.origin} fitness={self.fitness}>"


class ValidationFold(Base):
    """One walk-forward fold's metrics for one genome.

    Per US-004, the validator runs K=4 folds; the final fold is the held-out
    OOS test the GA never saw. is_oos=True identifies that fold.
    """

    __tablename__ = "validation_folds"

    # Format: fold_{genome_id}_{fold_index}
    id = Column(String(140), primary_key=True, index=True)
    genome_id = Column(
        String(120),
        ForeignKey("strategy_genomes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    fold_index = Column(Integer, nullable=False)  # 0..k-1
    fold_start = Column(DateTime(timezone=True), nullable=False)
    fold_end = Column(DateTime(timezone=True), nullable=False)
    is_oos = Column(Boolean, nullable=False)  # true only for the held-out fold

    sharpe = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    hit_rate = Column(Float, nullable=True)
    n_trades = Column(Integer, nullable=True)
    cagr = Column(Float, nullable=True)  # net annualised return
    passed_gate_b = Column(Boolean, nullable=False)

    # v0.4.0 regime tags (vol-quintile × trend-strength × direction) — see
    # core/regime.py. Nullable because legacy rows from v0.3.0 K=4 runs were
    # written before the orchestrator tagged folds; the UI shows "—" in
    # that case. `regime_direction` was added in the v0.4.0 follow-up
    # diagnostic pass; older v0.4.0 rows have direction NULL too.
    regime_volatility = Column(String(8), nullable=True)  # "low" | "med" | "high"
    regime_trend = Column(String(12), nullable=True)  # "trending" | "choppy" | "mixed"
    regime_direction = Column(String(8), nullable=True)  # "up" | "down" | "flat"

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    genome = relationship("StrategyGenome", back_populates="validation_folds")

    __table_args__ = (
        UniqueConstraint(
            "genome_id", "fold_index", name="uq_validation_fold_genome_index"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ValidationFold {self.id} fold={self.fold_index} "
            f"oos={self.is_oos} passed={self.passed_gate_b}>"
        )


class DecisionLog(Base):
    """Append-only audit trail of every agent action within a run.

    Never updated, never deleted (except via cascade when a run is deleted).
    The portal reads this to render the per-run "why did the system pick
    this genome?" timeline (US-016).
    """

    __tablename__ = "decision_logs"

    id = Column(String(36), primary_key=True, index=True)  # uuid hex
    run_id = Column(
        String(50),
        ForeignKey("evolution_runs.id", ondelete="CASCADE"),
        # Nullable since v0.6.0 (US-023): user-initiated audit rows
        # (paper-run kill / credential update / deploy paper) aren't
        # attached to a specific evolution run. Legacy agent-authored
        # rows still populate it via the FK.
        nullable=True,
        index=True,
    )
    generation_id = Column(
        String(80),
        ForeignKey("generations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    agent_type = Column(String(30), nullable=False, index=True)
    action = Column(String(40), nullable=False, index=True)
    inputs_json = Column(JSON, nullable=True)
    outputs_json = Column(JSON, nullable=True)
    reasoning_text = Column(Text, nullable=True)
    llm_call_id = Column(String(80), nullable=True)
    # Populated when `agent_type == USER` so a full audit trail exists
    # for human-initiated actions (US-018). Nullable for the pre-existing
    # agent-authored rows and for legacy rows written before the v0.6.0
    # migration ran.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run = relationship("EvolutionRun", back_populates="decisions")
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        # Per-run timeline view in the portal — paginated by created_at desc.
        Index("idx_decision_log_run_created", "run_id", "created_at"),
        Index("idx_decision_log_agent_action", "agent_type", "action"),
    )

    def __repr__(self) -> str:
        return f"<DecisionLog {self.id} {self.agent_type}.{self.action}>"


__all__ = [
    "AgentType",
    "DecisionLog",
    "EvolutionRun",
    "EvolutionRunStatus",
    "Generation",
    "GenomeOrigin",
    "StrategyGenome",
    "UserAction",
    "ValidationFold",
]
