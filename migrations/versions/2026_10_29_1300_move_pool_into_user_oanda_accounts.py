"""collapse oanda_account_pool into user_oanda_accounts (AFX-7 pivot)

Replaces the fleet-managed `oanda_account_pool` table with pool-state
columns on `user_oanda_accounts`. OANDA's `/v3/accounts` already
enumerates every sub-account under a user's single API token, and the
portal already syncs those on credential verify — a separate table +
admin surface was redundant architecture.

Changes
- `user_oanda_credentials` gains `allow_fleet_pool_use` (bool, default
  false). Opt-in explicit: adding OANDA creds for portal use never
  silently conscripts sub-accounts into fleet experiments.
- `user_oanda_accounts` gains pool-state columns: `pool_status`,
  `current_run_id`, `in_use_since`, `lease_expires_at`,
  `cooling_until`, `last_run_id`, `last_run_ended_at`,
  `cumulative_pnl_14d`.
- Drops `oanda_account_pool` table + its four indexes.

Non-destructive from the operator's perspective: pool table was
unused in production (seed step never ran) so dropping it loses
nothing. If this migration ever runs against an environment that
seeded the pool, those credentials are discarded — matched to the
new path by having the user re-add their OANDA creds in Settings.

Revision ID: f1a2b3c4d5e6
Revises: 8b3f94e2a7c1
Create Date: 2026-10-29 13:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "8b3f94e2a7c1"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Opt-in flag on the credentials row.
    op.add_column(
        "user_oanda_credentials",
        sa.Column(
            "allow_fleet_pool_use",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # 2. Pool-state columns on each sub-account.
    op.add_column(
        "user_oanda_accounts",
        sa.Column(
            "pool_status",
            sa.String(length=16),
            nullable=False,
            server_default="free",
        ),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("current_run_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("in_use_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("cooling_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("last_run_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column("last_run_ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_oanda_accounts",
        sa.Column(
            "cumulative_pnl_14d",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )
    op.create_index(
        "ix_user_oanda_account_pool_status",
        "user_oanda_accounts",
        ["pool_status"],
        unique=False,
    )
    op.create_index(
        "ix_user_oanda_account_current_run_id",
        "user_oanda_accounts",
        ["current_run_id"],
        unique=False,
    )

    # 3. Drop the legacy fleet pool table.
    op.drop_index("idx_oanda_pool_status_created", table_name="oanda_account_pool")
    op.drop_index(
        "ix_oanda_account_pool_current_run_id", table_name="oanda_account_pool"
    )
    op.drop_index("ix_oanda_account_pool_status", table_name="oanda_account_pool")
    op.drop_index("ix_oanda_account_pool_account_id", table_name="oanda_account_pool")
    op.drop_table("oanda_account_pool")


def downgrade():
    # Recreate the legacy pool table exactly as it was before this
    # migration ran. Data from the new path is NOT backfilled — a
    # downgrade is only for emergency rollback, not steady state.
    op.create_table(
        "oanda_account_pool",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("nickname", sa.String(length=60), nullable=True),
        sa.Column("account_id", sa.String(length=50), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="free",
        ),
        sa.Column("current_run_id", sa.String(length=120), nullable=True),
        sa.Column("in_use_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooling_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.String(length=120), nullable=True),
        sa.Column("last_run_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cumulative_pnl_14d",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_oanda_account_pool_account_id",
        "oanda_account_pool",
        ["account_id"],
        unique=True,
    )
    op.create_index(
        "ix_oanda_account_pool_status",
        "oanda_account_pool",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_oanda_account_pool_current_run_id",
        "oanda_account_pool",
        ["current_run_id"],
        unique=False,
    )
    op.create_index(
        "idx_oanda_pool_status_created",
        "oanda_account_pool",
        ["status", "created_at"],
        unique=False,
    )

    # Drop the new columns + indexes from user_oanda_accounts.
    op.drop_index(
        "ix_user_oanda_account_current_run_id", table_name="user_oanda_accounts"
    )
    op.drop_index("ix_user_oanda_account_pool_status", table_name="user_oanda_accounts")
    op.drop_column("user_oanda_accounts", "cumulative_pnl_14d")
    op.drop_column("user_oanda_accounts", "last_run_ended_at")
    op.drop_column("user_oanda_accounts", "last_run_id")
    op.drop_column("user_oanda_accounts", "cooling_until")
    op.drop_column("user_oanda_accounts", "lease_expires_at")
    op.drop_column("user_oanda_accounts", "in_use_since")
    op.drop_column("user_oanda_accounts", "current_run_id")
    op.drop_column("user_oanda_accounts", "pool_status")

    op.drop_column("user_oanda_credentials", "allow_fleet_pool_use")
