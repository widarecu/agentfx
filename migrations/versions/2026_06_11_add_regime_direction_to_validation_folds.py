"""add regime_direction column to validation_folds

Adds the third coordinate of the v0.4.0 regime tag — net-move direction
(up/down/flat). Surfaced after the 2026-06-10 catastrophic-OOS-fold
analysis where adjacent low-vol-choppy folds had +1.36% vs -1.13% net
moves but were collapsed into the same regime label. Direction
distinguishes them and the orchestrator's auto-stop check will now
correctly flag the train/OOS mismatch.

Legacy rows (pre-v0.4.0 K=4 runs OR v0.4.0 runs written before this
migration) keep ``NULL`` — the UI renders ``"—"``.

Revision ID: 2e95b1c47a8d
Revises: 17e4d2c9a3f1
Create Date: 2026-06-11 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2e95b1c47a8d"
down_revision = "17e4d2c9a3f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "validation_folds",
        sa.Column("regime_direction", sa.String(length=8), nullable=True),
    )


def downgrade():
    op.drop_column("validation_folds", "regime_direction")
