"""Record the ±10% parameter robustness check on each genome (AFX-153).

Revision ID: b4e8c21f6d39
Revises: a7d1e94c3b25
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e8c21f6d39"
down_revision = "a7d1e94c3b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_genomes", sa.Column("validation_robustness", sa.JSON()))


def downgrade() -> None:
    op.drop_column("strategy_genomes", "validation_robustness")
