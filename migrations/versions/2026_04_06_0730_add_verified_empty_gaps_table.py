"""add verified_empty_gaps table

Revision ID: 9fe97c6f7eae
Revises: b8c9d0e1f2a3
Create Date: 2026-04-06 07:30:25.883503

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9fe97c6f7eae"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS verified_empty_gaps (
            id SERIAL PRIMARY KEY,
            exchange VARCHAR NOT NULL DEFAULT 'oanda',
            instrument VARCHAR NOT NULL,
            timeframe VARCHAR NOT NULL,
            gap_start TIMESTAMP WITH TIME ZONE NOT NULL,
            gap_end TIMESTAMP WITH TIME ZONE NOT NULL,
            gap_duration_seconds FLOAT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 1,
            first_verified_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            last_verified_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            reason VARCHAR
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_verified_gaps_lookup
        ON verified_empty_gaps (exchange, instrument, timeframe, gap_start, gap_end)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_verified_gaps_instrument_tf
        ON verified_empty_gaps (instrument, timeframe)
    """)


def downgrade():
    op.drop_index("idx_verified_gaps_instrument_tf", table_name="verified_empty_gaps")
    op.drop_index("idx_verified_gaps_lookup", table_name="verified_empty_gaps")
    op.drop_table("verified_empty_gaps")
