"""add broker transaction ids for T1 reconciliation

Wilson UAT 2026-07-15/16: we had NO way to reconcile our Trade rows
against OANDA's authoritative transaction log. When the broker's SL
fired between our polls we couldn't correlate the ORDER_FILL back to
our specific Trade row, so silent mis-classifications went unnoticed
for hours.

Adds:
  - runs.last_transaction_id — high-water mark for
    `GET /transactions/sinceid` polling
  - trades.broker_trade_id — OANDA's tradeID from the fill that
    opened this position (join key for reconciliation)
  - trades.broker_open_transaction_id — the ORDER_FILL transaction
    that opened the trade (audit)
  - trades.broker_close_transaction_id — the ORDER_FILL transaction
    that closed the trade (audit; populated by the reconciler when
    a broker-side close fires)

All nullable so legacy rows written before this migration import
cleanly.

Revision ID: 6d8a3c9e1f47
Revises: 4c9e2a5d8b16
Create Date: 2026-07-16 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "6d8a3c9e1f47"
down_revision = "4c9e2a5d8b16"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runs",
        sa.Column("last_transaction_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("broker_trade_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("broker_open_transaction_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("broker_close_transaction_id", sa.String(length=40), nullable=True),
    )
    # Index so the reconciler's `WHERE broker_trade_id = ...` lookup
    # is O(log n) not O(n). We hit this on every transaction that
    # references a trade.
    op.create_index(
        "ix_trades_broker_trade_id",
        "trades",
        ["broker_trade_id"],
    )


def downgrade():
    op.drop_index("ix_trades_broker_trade_id", table_name="trades")
    op.drop_column("trades", "broker_close_transaction_id")
    op.drop_column("trades", "broker_open_transaction_id")
    op.drop_column("trades", "broker_trade_id")
    op.drop_column("runs", "last_transaction_id")
