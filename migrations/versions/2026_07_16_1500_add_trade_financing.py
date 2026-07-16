"""add trade financing + commission columns

Wilson UAT 2026-07-16 asked about financing (aka swap/carry) charges.
Answer was we don't capture them — pnl on the Trade row currently
holds only `orderFillTransaction.pl`, which is price movement only.
For any trade held across the 5pm NY rollover OANDA issues a
DAILY_FINANCING transaction with a per-trade interest charge or
credit; final close fills also carry `financing` and `commission`
fields we've been ignoring.

Columns:
  - trades.financing (Float) — accumulated financing charges on the
    trade (negative = we paid, positive = we received)
  - trades.commission (Float) — accumulated commission across all
    fills for this trade (0 for most retail accounts)

`net_pnl` is derived at read/render time as `pnl + financing - commission`
— storing it separately would risk drift when only one of the three
gets updated.

Revision ID: 9e4b7f2a8c31
Revises: 6d8a3c9e1f47
Create Date: 2026-07-16 15:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9e4b7f2a8c31"
down_revision = "6d8a3c9e1f47"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trades", sa.Column("financing", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("commission", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("trades", "commission")
    op.drop_column("trades", "financing")
