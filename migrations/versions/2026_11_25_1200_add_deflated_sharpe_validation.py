"""Record deflated Sharpe evidence and cumulative validation attempts.

Revision ID: f6c3b52ea929
Revises: c8d4f3e2b1a7
"""

import sqlalchemy as sa
from alembic import op

revision = "f6c3b52ea929"
down_revision = "c8d4f3e2b1a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_genomes", sa.Column("validation_dsr", sa.JSON()))
    op.create_table(
        "validation_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("genome_id", sa.String(120), nullable=False),
        sa.Column("instrument", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(5), nullable=False),
        sa.Column("oos_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("oos_end", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_validation_attempts_window",
        "validation_attempts",
        ["instrument", "granularity", "oos_start", "oos_end"],
    )
    op.execute(
        sa.text("""
        INSERT INTO validation_attempts
            (genome_id, instrument, granularity, oos_start, oos_end)
        SELECT f.genome_id, r.instrument, r.granularity,
               MIN(f.fold_start), MAX(f.fold_end)
        FROM validation_folds f
        JOIN strategy_genomes sg ON sg.id = f.genome_id
        JOIN generations g ON g.id = sg.generation_id
        JOIN evolution_runs r ON r.id = g.run_id
        WHERE f.is_oos AND r.imported_from_run_id IS NULL
        GROUP BY f.genome_id, r.instrument, r.granularity
    """)
    )


def downgrade() -> None:
    op.drop_table("validation_attempts")
    op.drop_column("strategy_genomes", "validation_dsr")
