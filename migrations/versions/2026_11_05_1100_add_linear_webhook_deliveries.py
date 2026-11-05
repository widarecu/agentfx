"""add linear_webhook_deliveries table (AFX-66 Phase 1)

Table backs the Linear Agent webhook receiver — one row per delivery
Linear POSTs to us. `delivery_id` (the `Linear-Delivery` header, UUID
per delivery) is the primary key so retries hit an IntegrityError on
insert, which the handler maps to a 200 duplicate response. Phase 1
is log-only; Phase 2 adds the dispatch wiring that reads this table.

Revision ID: b4f8e2d1c7a9
Revises: f1a2b3c4d5e6
Create Date: 2026-11-05 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4f8e2d1c7a9"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linear_webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("event_action", sa.String(length=32), nullable=True),
        sa.Column("agent_session_id", sa.String(length=64), nullable=True),
        sa.Column("raw_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            server_default=sa.text("'logged'"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_linear_webhook_deliveries_event_action",
        "linear_webhook_deliveries",
        ["event_action"],
    )
    op.create_index(
        "ix_linear_webhook_deliveries_agent_session_id",
        "linear_webhook_deliveries",
        ["agent_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_linear_webhook_deliveries_agent_session_id",
        table_name="linear_webhook_deliveries",
    )
    op.drop_index(
        "ix_linear_webhook_deliveries_event_action",
        table_name="linear_webhook_deliveries",
    )
    op.drop_table("linear_webhook_deliveries")
