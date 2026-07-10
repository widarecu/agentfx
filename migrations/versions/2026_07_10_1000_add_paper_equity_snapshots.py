"""add paper_equity_snapshots table (US-026)

Persisted equity samples for the live paper-run equity chart. The
runner appends one row per signal tick (not per health tick — that
would multiply the write volume 12× for no gain). The v0.6.0
`LivePnLChart` accumulated samples only while the browser tab was
open; this table backs `GET /paper-runs/{id}/equity` so the chart
survives a page reload with its full history intact.

Runner-driven: only PAPER_RUNNER writes here — no user-initiated
writes. `run_id` FKs to `runs.id` (not `evolution_runs.id`) since
paper runs live in the legacy Run table.

Revision ID: 7d1a8f4c9e63
Revises: b3e5c7a1d842
Create Date: 2026-07-10 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7d1a8f4c9e63"
down_revision = "b3e5c7a1d842"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_equity_snapshots",
        # UUID-hex strings via `uuid.uuid4().hex` — same convention as
        # `decision_logs.id` and `trades.id`. Postgres UUID type is too
        # strict on formatting (e.g. rejects the "snap_0"-style test
        # fixtures) and offers no query benefit over an indexed String
        # for our access pattern.
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=120),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "unrealized_pnl", sa.Numeric(18, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_paper_equity_snapshots_run_timestamp",
        "paper_equity_snapshots",
        ["run_id", "timestamp"],
    )


def downgrade():
    op.drop_index(
        "ix_paper_equity_snapshots_run_timestamp",
        table_name="paper_equity_snapshots",
    )
    op.drop_table("paper_equity_snapshots")
