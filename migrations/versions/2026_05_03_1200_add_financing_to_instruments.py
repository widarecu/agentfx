"""add financing rate columns to instruments

Revision ID: a1b2c3d4e5f6
Revises: 9fe97c6f7eae
Create Date: 2026-05-03 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9fe97c6f7eae"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "instruments",
        sa.Column("financing_long_rate", sa.Float(), nullable=True),
    )
    op.add_column(
        "instruments",
        sa.Column("financing_short_rate", sa.Float(), nullable=True),
    )
    op.add_column(
        "instruments",
        sa.Column("financing_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("instruments", "financing_updated_at")
    op.drop_column("instruments", "financing_short_rate")
    op.drop_column("instruments", "financing_long_rate")
