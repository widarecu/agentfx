"""add paper-trading columns + statuses to runs (US-021 / US-023)

Extends the legacy `runs` table with everything the v0.6.0 paper
runner needs. Per Wilson's answer #4 the schema also gains
`oanda_account_id` (which sub-account this run trades in) and
`snapshot_balance` (captured from OANDA on first tick — replaces the
user-typed "initial balance" in the deploy modal).

Adds three new values to the existing `runstatus` enum:
- `awaiting_approval`
- `approved`
- `halted_manual`

`strategy_id` is relaxed to nullable — paper runs are backed by a
StrategyGenome, not a Strategy row.

Revision ID: 8b7c4e2f9d15
Revises: 5c1d9e0b6a3f
Create Date: 2026-07-06 15:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8b7c4e2f9d15"
down_revision = "5c1d9e0b6a3f"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Extend the runstatus enum. Postgres requires ADD VALUE outside
    #    a transaction — Alembic gives us the raw connection.
    # Postgres enum labels match the Python `RunStatus.name` (UPPERCASE)
    # — the initial migration created the type by name, and SQLAlchemy
    # writes `enum.name` for str-Enum subclasses. If a lowercase label
    # was pre-added by an earlier draft of this migration on a shared
    # DB, we leave it in place (unused) — dropping enum labels is a
    # Postgres CREATE TYPE dance we don't need to do here.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'AWAITING_APPROVAL'")
        op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'APPROVED'")
        op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'HALTED_MANUAL'")

    # 2. Relax `strategy_id` for paper runs.
    op.alter_column("runs", "strategy_id", nullable=True)

    # 3. Paper-trading columns.
    op.add_column(
        "runs",
        sa.Column(
            "strategy_genome_id",
            sa.String(length=120),
            sa.ForeignKey("strategy_genomes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_runs_strategy_genome_id",
        "runs",
        ["strategy_genome_id"],
    )

    op.add_column(
        "runs",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_runs_user_id", "runs", ["user_id"])

    op.add_column(
        "runs",
        sa.Column("oanda_account_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_runs_oanda_account_id", "runs", ["oanda_account_id"])

    # The oandaenvironment enum was created by the 5c1d9e0b6a3f migration
    # (user_oanda_credentials); re-use it via `create_type=False`.
    op.add_column(
        "runs",
        sa.Column(
            "oanda_environment",
            sa.Enum(
                "practice",
                "live",
                name="oandaenvironment",
                create_type=False,
            ),
            nullable=True,
        ),
    )

    op.add_column("runs", sa.Column("snapshot_balance", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("stop_reason", sa.String(length=40), nullable=True))
    op.add_column(
        "runs",
        sa.Column("kill_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "runs",
        sa.Column(
            "approved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("runs", sa.Column("risk_config", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("process_pid", sa.Integer(), nullable=True))

    # Compound index for the /paper-runs list query pattern:
    #   WHERE user_id = ? AND environment = 'paper' AND status = ?
    op.create_index(
        "ix_runs_user_env_status",
        "runs",
        ["user_id", "environment", "status"],
    )


def downgrade():
    op.drop_index("ix_runs_user_env_status", table_name="runs")
    op.drop_column("runs", "process_pid")
    op.drop_column("runs", "risk_config")
    op.drop_column("runs", "approved_by_user_id")
    op.drop_column("runs", "approved_at")
    op.drop_column("runs", "kill_requested_at")
    op.drop_column("runs", "stop_reason")
    op.drop_column("runs", "snapshot_balance")
    op.drop_column("runs", "oanda_environment")
    op.drop_index("ix_runs_oanda_account_id", table_name="runs")
    op.drop_column("runs", "oanda_account_id")
    op.drop_index("ix_runs_user_id", table_name="runs")
    op.drop_column("runs", "user_id")
    op.drop_index("ix_runs_strategy_genome_id", table_name="runs")
    op.drop_column("runs", "strategy_genome_id")

    # Cannot remove enum values in Postgres without recreating the
    # type — leave the three new values in place. Rows using them
    # would have been deleted by the paper-trading feature rollback.
    op.alter_column("runs", "strategy_id", nullable=False)
