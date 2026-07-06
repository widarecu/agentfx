"""add user_id column to decision_logs (US-018)

Attaches an audit trail to human-initiated agent actions (create /
delete / stop run, approve / reject genome, deploy / kill paper,
update credentials). Filled only when ``agent_type = 'user'``; every
other row keeps NULL.

Legacy rows (pre-v0.6.0) always have NULL user_id — they were written
by autonomous agents (orchestrator / ga / llm / validator) so no user
was involved.

Revision ID: 3f8a2b9c1d5e
Revises: 2e95b1c47a8d
Create Date: 2026-07-05 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3f8a2b9c1d5e"
down_revision = "2e95b1c47a8d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "decision_logs",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_decision_logs_user_id_users",
        "decision_logs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_decision_logs_user_id",
        "decision_logs",
        ["user_id"],
    )


def downgrade():
    op.drop_index("ix_decision_logs_user_id", table_name="decision_logs")
    op.drop_constraint(
        "fk_decision_logs_user_id_users", "decision_logs", type_="foreignkey"
    )
    op.drop_column("decision_logs", "user_id")
