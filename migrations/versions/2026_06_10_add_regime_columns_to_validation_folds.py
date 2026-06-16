"""add regime_volatility + regime_trend columns to validation_folds

v0.4.0 rolling walk-forward tags each fold (training and OOS) with a
regime label computed from its candle slice: volatility quintile
(``"low"`` / ``"med"`` / ``"high"``) and trend strength (``"trending"``
/ ``"choppy"`` / ``"mixed"``). Stored as separate short-string columns
so the portal can render badges without parsing a combined label.

Legacy rows (v0.3.0 K=4 runs) keep ``NULL`` — the UI renders ``"—"``.

Revision ID: 17e4d2c9a3f1
Revises: 06f3a8b9c1d2
Create Date: 2026-06-10 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "17e4d2c9a3f1"
down_revision = "06f3a8b9c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "validation_folds",
        sa.Column("regime_volatility", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "validation_folds",
        sa.Column("regime_trend", sa.String(length=12), nullable=True),
    )


def downgrade():
    op.drop_column("validation_folds", "regime_trend")
    op.drop_column("validation_folds", "regime_volatility")
