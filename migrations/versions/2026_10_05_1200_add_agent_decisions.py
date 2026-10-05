"""add agent_decisions table (v0.8.0 Agent Fleet)

Append-only decision log for the four specialist agents (Portfolio
Manager, Quant Analyst, Platform Engineer, Research Agent). Separate
from `decision_logs` which is tied to evolution runs via FK — fleet
agents operate outside the per-run scope (PM morning briefs, QA
divergence reports spanning multiple runs, PE PRs, etc.).

Schema design in `src/agentfx/api/models/agent_decision.py`. Reflexion
fields (`outcome_json`, `outcome_recorded_at`, `outcome_grade`) are
nullable at insert and populated LATER when real-world outcomes are
observed; the original decision row is never otherwise mutated.

Indexes:
- `idx_agent_decision_role_created` — the primary read path, the
  Reflexion loop "most recent N decisions by this agent."
- `idx_agent_decision_grade_role` — filter for "past bad PM
  decisions" / "past good QA calls" when sampling for prompt
  context.

Revision ID: a1c4e7f9b2d3
Revises: b7f3d1a4c692
Create Date: 2026-10-05 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c4e7f9b2d3"
down_revision = "b7f3d1a4c692"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("agent_role", sa.String(length=4), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("decision_key", sa.String(length=200), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=True),
        sa.Column("outputs_json", sa.JSON(), nullable=True),
        sa.Column("reasoning_text", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("input_tokens", sa.Float(), nullable=True),
        sa.Column("output_tokens", sa.Float(), nullable=True),
        sa.Column("cache_read_tokens", sa.Float(), nullable=True),
        sa.Column("usd_cost", sa.Float(), nullable=True),
        sa.Column("outcome_json", sa.JSON(), nullable=True),
        sa.Column("outcome_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_grade", sa.String(length=12), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_decisions_id", "agent_decisions", ["id"], unique=False)
    op.create_index(
        "ix_agent_decisions_agent_role",
        "agent_decisions",
        ["agent_role"],
        unique=False,
    )
    op.create_index(
        "ix_agent_decisions_action", "agent_decisions", ["action"], unique=False
    )
    op.create_index(
        "ix_agent_decisions_decision_key",
        "agent_decisions",
        ["decision_key"],
        unique=False,
    )
    op.create_index(
        "ix_agent_decisions_outcome_grade",
        "agent_decisions",
        ["outcome_grade"],
        unique=False,
    )
    op.create_index(
        "idx_agent_decision_role_created",
        "agent_decisions",
        ["agent_role", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_agent_decision_grade_role",
        "agent_decisions",
        ["outcome_grade", "agent_role"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_agent_decision_grade_role", table_name="agent_decisions")
    op.drop_index("idx_agent_decision_role_created", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_outcome_grade", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_decision_key", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_action", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_agent_role", table_name="agent_decisions")
    op.drop_index("ix_agent_decisions_id", table_name="agent_decisions")
    op.drop_table("agent_decisions")
