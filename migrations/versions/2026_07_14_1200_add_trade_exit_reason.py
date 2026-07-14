"""add exit_reason to trades

Structured taxonomy so the /paper/:id chart can render exit markers
with the reason at a glance (parity with backtest's exit_reasons_by_ts
pipeline). Runner writes one of:

  - 'stop'          — broker-side stop-loss fill (price ≈ entry - stop_pips)
  - 'target'        — broker-side take-profit fill (price ≈ entry + target_pips)
  - 'max_hold'      — runner-enforced max_hold_bars expiry
  - 'opposite_flip' — fresh opposite entry cross with opposite_signal=true
  - 'user_kill'     — kill switch triggered close
  - 'broker_other'  — broker-side close we couldn't classify (rare)

Nullable so legacy Trade rows (pre-v0.6.1 and any paper closes written
before this migration lands) don't blow up.

Revision ID: 4c9e2a5d8b16
Revises: 7d1a8f4c9e63
Create Date: 2026-07-14 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4c9e2a5d8b16"
down_revision = "7d1a8f4c9e63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "trades",
        sa.Column("exit_reason", sa.String(length=20), nullable=True),
    )


def downgrade():
    op.drop_column("trades", "exit_reason")
