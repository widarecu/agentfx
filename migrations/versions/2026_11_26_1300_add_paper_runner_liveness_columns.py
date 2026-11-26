"""Add paper runner heartbeat and loop-progress columns (AFX-99).

Revision ID: 3c9e5d07a1f4
Revises: a7d1e94c3b25
"""

import sqlalchemy as sa
from alembic import op

revision = "3c9e5d07a1f4"
down_revision = "a7d1e94c3b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column(
        "runs", sa.Column("last_loop_progress_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("runs", "last_loop_progress_at")
    op.drop_column("runs", "last_heartbeat_at")
