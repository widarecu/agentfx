"""relax decision_logs.run_id nullability (US-023)

The v0.6.0 user-action audit trail writes DecisionLog rows for actions
that don't attach to an evolution run — user-initiated paper-run
kills, credential updates, etc. The existing column is NOT NULL from
the initial evolution-tables migration. Relax it here.

Revision ID: 9a2f4b8c3e17
Revises: 8b7c4e2f9d15
Create Date: 2026-07-06 16:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "9a2f4b8c3e17"
down_revision = "8b7c4e2f9d15"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("decision_logs", "run_id", nullable=True)


def downgrade():
    # Not reversible without dropping any rows written with NULL
    # run_id under the paper-trading feature. Fail loudly instead of
    # corrupting the audit trail.
    op.execute("DELETE FROM decision_logs WHERE run_id IS NULL")
    op.alter_column("decision_logs", "run_id", nullable=False)
