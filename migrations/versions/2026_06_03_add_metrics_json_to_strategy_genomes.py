"""add metrics_json column to strategy_genomes

Holds the full per-fold fitness metrics + penalty breakdown for every
genome (not just validated top-K). The fold-consistency fitness
function (v0.5.0) already computes these — we just weren't persisting
them. Surfaces per-training-fold Sharpe / MaxDD / hit-rate / trades /
CAGR and the raw → penalty → final fitness chain in the portal, so
users can inspect WHY a genome got its fitness without having to read
container logs.

Revision ID: 06f3a8b9c1d2
Revises: f7a8b9c0d1e2
Create Date: 2026-06-03 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "06f3a8b9c1d2"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "strategy_genomes",
        sa.Column("metrics_json", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("strategy_genomes", "metrics_json")
