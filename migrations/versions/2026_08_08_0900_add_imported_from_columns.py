"""add imported_from_email + imported_from_run_id to evolution_runs (v0.7.x)

Cross-user genome sharing (Wilson request 2026-08-02). When a user
imports a genome shared by another user, we create a shadow
EvolutionRun row scoped to the importer. These two columns preserve
attribution: who exported the genome, and from which of THEIR
evolution runs. Both nullable — native (non-imported) evolution runs
leave them NULL.

- imported_from_email (VARCHAR 200): the exporter's platform email
  at export time. Not verified/authenticated in v1; sufficient for
  the "shared with a friend" trust posture (see backlog v0.7.x
  spec, "Attribution + trust posture").
- imported_from_run_id (VARCHAR 50): the source EvolutionRun.id
  from the exporter's DB. Kept purely for provenance display —
  never dereferenced (it lives in the exporter's DB, not ours).

Non-indexed: we don't query by these columns hot-path. The
importer's approval queue filters via `evolution_runs.created_by ==
importer.user_id` (already indexed); the IMPORTED chip in the UI is
a per-row lookup off already-fetched rows.

Revision ID: b7f3d1a4c692
Revises: 9e4b7f2a8c31
Create Date: 2026-08-08 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b7f3d1a4c692"
down_revision = "9e4b7f2a8c31"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "evolution_runs",
        sa.Column("imported_from_email", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "evolution_runs",
        sa.Column("imported_from_run_id", sa.String(length=50), nullable=True),
    )


def downgrade():
    op.drop_column("evolution_runs", "imported_from_run_id")
    op.drop_column("evolution_runs", "imported_from_email")
