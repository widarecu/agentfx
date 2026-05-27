"""add cagr column to validation_folds

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-24 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "validation_folds",
        sa.Column("cagr", sa.Float(), nullable=True),
    )


def downgrade():
    op.drop_column("validation_folds", "cagr")
