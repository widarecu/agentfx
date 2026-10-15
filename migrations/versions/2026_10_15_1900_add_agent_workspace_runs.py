"""add agent_workspace_runs table (v0.8.1 C.2 Phase 2)

Tracks PE Managed Agents workspace dispatches — one row per attempt
to hand a PE proposal to an autonomous code-authoring workspace.
Lifecycle: PENDING → RUNNING → (PR_OPEN | TESTS_FAILED |
WORKSPACE_FAILED) → MERGED | ABANDONED.

Schema design in src/agentfx/api/models/agent_workspace_run.py.

Revision ID: 8b3f94e2a7c1
Revises: c7f2e5a8d1b4
Create Date: 2026-10-15 19:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "8b3f94e2a7c1"
down_revision = "c7f2e5a8d1b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_workspace_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("pe_proposal_decision_key", sa.String(length=200), nullable=False),
        sa.Column(
            "target_branch",
            sa.String(length=80),
            nullable=False,
            server_default="feat/agent-fleet",
        ),
        sa.Column("agent_branch", sa.String(length=120), nullable=True),
        sa.Column(
            "adapter",
            sa.String(length=32),
            nullable=False,
            server_default="stub",
        ),
        sa.Column("provider_run_id", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("pr_url", sa.String(length=400), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("container_minutes", sa.Float(), nullable=True),
        sa.Column("container_usd", sa.Float(), nullable=True),
        sa.Column("llm_usd", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_workspace_runs_pe_proposal_decision_key",
        "agent_workspace_runs",
        ["pe_proposal_decision_key"],
    )
    op.create_index(
        "ix_agent_workspace_runs_provider_run_id",
        "agent_workspace_runs",
        ["provider_run_id"],
    )
    op.create_index(
        "ix_agent_workspace_runs_status", "agent_workspace_runs", ["status"]
    )
    op.create_index(
        "idx_agent_workspace_runs_created_status",
        "agent_workspace_runs",
        ["created_at", "status"],
    )


def downgrade():
    op.drop_index(
        "idx_agent_workspace_runs_created_status",
        table_name="agent_workspace_runs",
    )
    op.drop_index("ix_agent_workspace_runs_status", table_name="agent_workspace_runs")
    op.drop_index(
        "ix_agent_workspace_runs_provider_run_id",
        table_name="agent_workspace_runs",
    )
    op.drop_index(
        "ix_agent_workspace_runs_pe_proposal_decision_key",
        table_name="agent_workspace_runs",
    )
    op.drop_table("agent_workspace_runs")
