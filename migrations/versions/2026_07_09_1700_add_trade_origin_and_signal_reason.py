"""add origin + signal_reason to trades (US-024)

The paper runner's live signal tick persists a Trade row on every
order placement and updates it on every close. The v0.6.1 runner
tags each row with `origin='live'` (reserved for v0.7.0 backtest-vs-
live overlay work) and `signal_reason` (the indicator condition that
triggered the entry — e.g. "SMA_20 > SMA_50" or "MACD ↑"). The
former is the placeholder for a future backtest-live overlay; the
latter is what the /paper/:id Trades table renders in the "Reason"
column per Wilson's UX answer #1.

Both columns are nullable to keep legacy backtest / dev trades
(pre-v0.6.1 rows) importable without a data backfill.

Revision ID: b3e5c7a1d842
Revises: 9a2f4b8c3e17
Create Date: 2026-07-09 17:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b3e5c7a1d842"
down_revision = "9a2f4b8c3e17"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "trades",
        sa.Column("origin", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("signal_reason", sa.String(length=200), nullable=True),
    )


def downgrade():
    op.drop_column("trades", "signal_reason")
    op.drop_column("trades", "origin")
