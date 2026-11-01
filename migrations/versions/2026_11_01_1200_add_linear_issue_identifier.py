"""add linear_issue_identifier to agent_workspace_runs

2026-11-01 Linear-driven PE dispatch rewrite. Each workspace run
now carries the Linear issue it's working on (e.g. "AFX-56") so
the retry gate can group attempts by issue and the `[NEEDS DESIGN]`
PR handoff can find its Linear ticket.

Nullable because historical rows don't have it; new rows always do.

Revision ID: a7e5b1d9c3f4
Revises: f1a2b3c4d5e6
Create Date: 2026-11-01 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a7e5b1d9c3f4"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_workspace_runs",
        sa.Column("linear_issue_identifier", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_agent_workspace_runs_linear_issue_identifier",
        "agent_workspace_runs",
        ["linear_issue_identifier"],
    )


def downgrade():
    op.drop_index(
        "ix_agent_workspace_runs_linear_issue_identifier",
        table_name="agent_workspace_runs",
    )
    op.drop_column("agent_workspace_runs", "linear_issue_identifier")
