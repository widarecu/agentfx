"""Record sealed final holdout checks and their single use (AFX-151).

Revision ID: a7d1e94c3b25
Revises: f6c3b52ea929
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d1e94c3b25"
down_revision = "f6c3b52ea929"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_genomes", sa.Column("validation_holdout", sa.JSON()))
    op.create_table(
        "holdout_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("genome_id", sa.String(120), nullable=False),
        sa.Column("spec_key", sa.String(64), nullable=False),
        sa.Column("instrument", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(5), nullable=False),
        sa.Column("holdout_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holdout_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passed", sa.Boolean()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "genome_id",
            "instrument",
            "granularity",
            "holdout_start",
            "holdout_end",
            name="uq_holdout_evaluation_genome_window",
        ),
    )
    op.create_index(
        "ix_holdout_evaluations_genome_id", "holdout_evaluations", ["genome_id"]
    )
    op.create_index(
        "ix_holdout_evaluations_spec_key", "holdout_evaluations", ["spec_key"]
    )
    op.create_index(
        "ix_holdout_evaluations_window",
        "holdout_evaluations",
        ["instrument", "granularity", "holdout_start", "holdout_end"],
    )


def downgrade() -> None:
    op.drop_table("holdout_evaluations")
    op.drop_column("strategy_genomes", "validation_holdout")
