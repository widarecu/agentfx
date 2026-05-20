"""add typical spread columns to instruments

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-20 17:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "instruments",
        sa.Column("typical_spread_pips", sa.Float(), nullable=True),
    )
    op.add_column(
        "instruments",
        sa.Column(
            "typical_spread_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("instruments", "typical_spread_updated_at")
    op.drop_column("instruments", "typical_spread_pips")
