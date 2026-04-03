"""Add timeframe column to optimization_jobs

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-03

Adds timeframe field to optimization_jobs table so users can select
which candle timeframe the backtest runs on (M1, M5, M10, M30, H1, H4, D).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "optimization_jobs",
        sa.Column("timeframe", sa.String(4), server_default="H1", nullable=False),
    )


def downgrade():
    op.drop_column("optimization_jobs", "timeframe")
