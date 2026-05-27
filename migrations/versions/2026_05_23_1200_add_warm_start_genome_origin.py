"""add warm_start to genomeorigin enum

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-23 12:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres requires ALTER TYPE outside a transaction for ADD VALUE,
    # but Alembic wraps each migration in one. The autocommit_block
    # escape hatch lets us do it cleanly.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE genomeorigin ADD VALUE IF NOT EXISTS 'warm_start'")


def downgrade():
    # Postgres doesn't support DROP VALUE on an enum without recreating
    # the type, which would require rewriting every column that uses it.
    # Leaving the value in place on downgrade is the safe choice.
    pass
