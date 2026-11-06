"""add linear_agent_session_id + linear_webhook_delivery_id to
agent_workspace_runs (AFX-66 Phase 2)

Links each workspace dispatch back to the Linear AgentSession that
triggered it and the specific webhook delivery that drove the
transition. Both nullable so legacy rows (dispatched via the scan
loop before Phase 2 landed) stay valid.

- `linear_agent_session_id`: the UUID from `agentSession.id` in
  the Linear webhook body. Indexed because Phase 2's resume path
  (action=prompted → look up the open session → pipe the message
  into the running Managed Agents session) queries by it.
- `linear_webhook_delivery_id`: pointer to the triggering
  `linear_webhook_deliveries.delivery_id`. Not enforced as a real
  FK because webhook rows can be purged for retention while the
  dispatch keeps living, and we'd rather lose the pointer than
  cascade-delete the workspace run.

Revision ID: c8d4f3e2b1a7
Revises: b4f8e2d1c7a9
Create Date: 2026-11-06 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c8d4f3e2b1a7"
down_revision = "b4f8e2d1c7a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_workspace_runs",
        sa.Column("linear_agent_session_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_workspace_runs",
        sa.Column("linear_webhook_delivery_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_workspace_runs_linear_agent_session_id",
        "agent_workspace_runs",
        ["linear_agent_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_workspace_runs_linear_agent_session_id",
        table_name="agent_workspace_runs",
    )
    op.drop_column("agent_workspace_runs", "linear_webhook_delivery_id")
    op.drop_column("agent_workspace_runs", "linear_agent_session_id")
