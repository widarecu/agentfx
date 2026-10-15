"""add oanda_account_pool table (v0.8.1 C.3)

Fleet-managed pool of OANDA accounts that PM allocates from for
parallel paper experiments. Separate from per-user credentials
(user_oanda_credentials / user_oanda_accounts) — pool accounts
belong to the fleet, not a specific portal user.

Schema design in src/agentfx/api/models/oanda_account_pool.py.
API key stored as Fernet ciphertext in `api_key_encrypted`.

Indexes:
- `ix_oanda_pool_account_id` (unique) — enforces one row per OANDA
  account; prevents the pool from double-booking.
- `ix_oanda_pool_status` — primary allocation query.
- `ix_oanda_pool_current_run_id` — reverse lookup (which pool slot
  is this paper run holding?) for the paper_runner release path.
- `idx_oanda_pool_status_created` — composite index for the
  "give me a FREE account, oldest first" query the allocator uses.

Revision ID: c7f2e5a8d1b4
Revises: a1c4e7f9b2d3
Create Date: 2026-10-15 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c7f2e5a8d1b4"
down_revision = "a1c4e7f9b2d3"
branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    op.drop_index("idx_oanda_pool_status_created", table_name="oanda_account_pool")
    op.drop_index(
        "ix_oanda_account_pool_current_run_id", table_name="oanda_account_pool"
    )
    op.drop_index("ix_oanda_account_pool_status", table_name="oanda_account_pool")
    op.drop_index("ix_oanda_account_pool_account_id", table_name="oanda_account_pool")
    op.drop_table("oanda_account_pool")
