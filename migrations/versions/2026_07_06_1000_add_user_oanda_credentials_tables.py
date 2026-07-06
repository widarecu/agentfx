"""add user_oanda_credentials + user_oanda_accounts tables (US-019)

Per-user OANDA credential storage with Fernet-encrypted API keys at
rest, plus a cache of the sub-accounts discovered from OANDA's
`/v3/accounts` endpoint at last verify. Balance is NOT stored — always
queried live per the v0.6.0 UX plan.

Legacy note: nothing existed before this migration; nothing to
back-fill.

Revision ID: 5c1d9e0b6a3f
Revises: 3f8a2b9c1d5e
Create Date: 2026-07-06 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5c1d9e0b6a3f"
down_revision = "3f8a2b9c1d5e"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres ENUM for the environment. Let the column's Enum handle
    # `CREATE TYPE` (idempotent-safe via `checkfirst` on the dialect);
    # a naked `sa.Enum(...).create()` call preceded the column
    # definition in an earlier draft and Postgres emitted the CREATE
    # twice, blowing up with "type already exists" on the second one.
    op.create_table(
        "user_oanda_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("oanda_api_key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "oanda_environment",
            sa.Enum("practice", "live", name="oandaenvironment"),
            nullable=False,
            server_default="practice",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verify_failure_reason", sa.String(length=500), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
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
        ),
    )
    op.create_index(
        "ix_user_oanda_credentials_user_id",
        "user_oanda_credentials",
        ["user_id"],
    )

    op.create_table(
        "user_oanda_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_credentials_id",
            sa.String(length=36),
            sa.ForeignKey("user_oanda_credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("oanda_account_id", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_credentials_id",
            "oanda_account_id",
            name="uq_user_oanda_account_per_credentials",
        ),
    )
    op.create_index(
        "ix_user_oanda_accounts_user_credentials_id",
        "user_oanda_accounts",
        ["user_credentials_id"],
    )


def downgrade():
    op.drop_index(
        "ix_user_oanda_accounts_user_credentials_id",
        table_name="user_oanda_accounts",
    )
    op.drop_table("user_oanda_accounts")
    op.drop_index(
        "ix_user_oanda_credentials_user_id",
        table_name="user_oanda_credentials",
    )
    op.drop_table("user_oanda_credentials")

    sa.Enum(name="oandaenvironment").drop(op.get_bind(), checkfirst=True)
