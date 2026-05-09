"""Add evolution tables for v0.3.0 Autonomous Strategy Loop

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f6
Create Date: 2026-05-09

Creates the persistence layer for the v0.3.0 autonomous loop:
- evolution_runs: top-level run lifecycle, config, stop reason, LLM cost
- generations: per-generation lineage within a run
- strategy_genomes: DSL specs evaluated, with origin (seed/crossover/mutation/llm)
  and walk-forward validation outcome
- validation_folds: K=4 walk-forward fold metrics per genome (one fold flagged
  is_oos=True is the held-out OOS fold the GA never saw)
- decision_logs: append-only audit trail of every agent action

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md for the full contract.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # ── Enums ────────────────────────────────────────────────────────────
    evolutionrunstatus_enum = postgresql.ENUM(
        "queued",
        "running",
        "completed",
        "failed",
        "stopped",
        name="evolutionrunstatus",
        create_type=False,
    )
    evolutionrunstatus_enum.create(op.get_bind(), checkfirst=True)

    genomeorigin_enum = postgresql.ENUM(
        "seed",
        "crossover",
        "mutation",
        "llm",
        name="genomeorigin",
        create_type=False,
    )
    genomeorigin_enum.create(op.get_bind(), checkfirst=True)

    # ── evolution_runs ──────────────────────────────────────────────────
    op.create_table(
        "evolution_runs",
        sa.Column("id", sa.String(50), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued",
                "running",
                "completed",
                "failed",
                "stopped",
                name="evolutionrunstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("instrument", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(5), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("stop_conditions_json", sa.JSON(), nullable=False),
        sa.Column("stop_reason", sa.String(40), nullable=True),
        sa.Column(
            "current_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("best_genome_id", sa.String(80), nullable=True),
        sa.Column(
            "llm_cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("process_pid", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evolution_runs_id", "evolution_runs", ["id"])
    op.create_index("ix_evolution_runs_status", "evolution_runs", ["status"])
    op.create_index("ix_evolution_runs_instrument", "evolution_runs", ["instrument"])
    op.create_index("ix_evolution_runs_created_by", "evolution_runs", ["created_by"])

    # ── generations ─────────────────────────────────────────────────────
    op.create_table(
        "generations",
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "llm_invoked", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("llm_call_id", sa.String(80), nullable=True),
        sa.Column("best_fitness", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["evolution_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "index", name="uq_generation_run_index"),
    )
    op.create_index("ix_generations_id", "generations", ["id"])
    op.create_index("ix_generations_run_id", "generations", ["run_id"])

    # ── strategy_genomes ────────────────────────────────────────────────
    op.create_table(
        "strategy_genomes",
        sa.Column("id", sa.String(120), nullable=False),
        sa.Column("generation_id", sa.String(80), nullable=False),
        sa.Column("dsl_json", sa.JSON(), nullable=False),
        sa.Column(
            "parent_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "origin",
            postgresql.ENUM(
                "seed",
                "crossover",
                "mutation",
                "llm",
                name="genomeorigin",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("fitness", sa.Float(), nullable=True),
        sa.Column("validator_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "validation_failures",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("llm_reasoning", sa.Text(), nullable=True),
        sa.Column("llm_call_id", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["generations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_genomes_id", "strategy_genomes", ["id"])
    op.create_index(
        "ix_strategy_genomes_generation_id", "strategy_genomes", ["generation_id"]
    )
    op.create_index("ix_strategy_genomes_origin", "strategy_genomes", ["origin"])
    op.create_index(
        "idx_genome_generation_fitness",
        "strategy_genomes",
        ["generation_id", "fitness"],
    )

    # ── validation_folds ────────────────────────────────────────────────
    op.create_table(
        "validation_folds",
        sa.Column("id", sa.String(140), nullable=False),
        sa.Column("genome_id", sa.String(120), nullable=False),
        sa.Column("fold_index", sa.Integer(), nullable=False),
        sa.Column("fold_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fold_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_oos", sa.Boolean(), nullable=False),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("hit_rate", sa.Float(), nullable=True),
        sa.Column("n_trades", sa.Integer(), nullable=True),
        sa.Column("passed_gate_b", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["genome_id"], ["strategy_genomes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "genome_id", "fold_index", name="uq_validation_fold_genome_index"
        ),
    )
    op.create_index("ix_validation_folds_id", "validation_folds", ["id"])
    op.create_index("ix_validation_folds_genome_id", "validation_folds", ["genome_id"])

    # ── decision_logs ───────────────────────────────────────────────────
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("generation_id", sa.String(80), nullable=True),
        sa.Column("agent_type", sa.String(30), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=True),
        sa.Column("outputs_json", sa.JSON(), nullable=True),
        sa.Column("reasoning_text", sa.Text(), nullable=True),
        sa.Column("llm_call_id", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evolution_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["generations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_logs_id", "decision_logs", ["id"])
    op.create_index("ix_decision_logs_run_id", "decision_logs", ["run_id"])
    op.create_index(
        "ix_decision_logs_generation_id", "decision_logs", ["generation_id"]
    )
    op.create_index("ix_decision_logs_agent_type", "decision_logs", ["agent_type"])
    op.create_index("ix_decision_logs_action", "decision_logs", ["action"])
    op.create_index(
        "idx_decision_log_run_created", "decision_logs", ["run_id", "created_at"]
    )
    op.create_index(
        "idx_decision_log_agent_action", "decision_logs", ["agent_type", "action"]
    )


def downgrade():
    op.drop_index("idx_decision_log_agent_action", table_name="decision_logs")
    op.drop_index("idx_decision_log_run_created", table_name="decision_logs")
    op.drop_index("ix_decision_logs_action", table_name="decision_logs")
    op.drop_index("ix_decision_logs_agent_type", table_name="decision_logs")
    op.drop_index("ix_decision_logs_generation_id", table_name="decision_logs")
    op.drop_index("ix_decision_logs_run_id", table_name="decision_logs")
    op.drop_index("ix_decision_logs_id", table_name="decision_logs")
    op.drop_table("decision_logs")

    op.drop_index("ix_validation_folds_genome_id", table_name="validation_folds")
    op.drop_index("ix_validation_folds_id", table_name="validation_folds")
    op.drop_table("validation_folds")

    op.drop_index("idx_genome_generation_fitness", table_name="strategy_genomes")
    op.drop_index("ix_strategy_genomes_origin", table_name="strategy_genomes")
    op.drop_index("ix_strategy_genomes_generation_id", table_name="strategy_genomes")
    op.drop_index("ix_strategy_genomes_id", table_name="strategy_genomes")
    op.drop_table("strategy_genomes")

    op.drop_index("ix_generations_run_id", table_name="generations")
    op.drop_index("ix_generations_id", table_name="generations")
    op.drop_table("generations")

    op.drop_index("ix_evolution_runs_created_by", table_name="evolution_runs")
    op.drop_index("ix_evolution_runs_instrument", table_name="evolution_runs")
    op.drop_index("ix_evolution_runs_status", table_name="evolution_runs")
    op.drop_index("ix_evolution_runs_id", table_name="evolution_runs")
    op.drop_table("evolution_runs")

    sa.Enum(name="genomeorigin").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="evolutionrunstatus").drop(op.get_bind(), checkfirst=True)
