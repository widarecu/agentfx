"""Add optimization tables for v0.2.0

Revision ID: a7b8c9d0e1f2
Revises: 29ec29defc56
Create Date: 2025-12-22

Creates tables for Bayesian parameter optimization:
- optimization_jobs: Parameter search jobs with configuration
- optimization_trials: Individual backtest trial results
- optimization_signals: Trade signals for chart visualization
- optimization_trades: Individual trades with financing breakdown
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "29ec29defc56"
branch_labels = None
depends_on = None


def upgrade():
    # Create job status enum
    job_status_enum = postgresql.ENUM(
        "queued", "running", "completed", "failed", name="jobstatus", create_type=False
    )
    job_status_enum.create(op.get_bind(), checkfirst=True)

    # Create strategy type enum
    strategy_type_enum = postgresql.ENUM(
        "ma", "rsi", "donchian", name="strategytype", create_type=False
    )
    strategy_type_enum.create(op.get_bind(), checkfirst=True)

    # Create optimization_jobs table
    op.create_table(
        "optimization_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "strategy",
            postgresql.ENUM(
                "ma", "rsi", "donchian", name="strategytype", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("pair", sa.String(10), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued",
                "running",
                "completed",
                "failed",
                name="jobstatus",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("progress", sa.Integer(), nullable=True, default=0),
        sa.Column("estimated_iterations", sa.Integer(), nullable=False),
        sa.Column("completed_iterations", sa.Integer(), nullable=True, default=0),
        sa.Column("best_sharpe", sa.Float(), nullable=True),
        sa.Column("best_trial_id", sa.String(), nullable=True),
        sa.Column("account_balance", sa.Float(), nullable=True, default=10000.0),
        sa.Column("position_size", sa.Integer(), nullable=True, default=10000),
        sa.Column("data_years", sa.Integer(), nullable=True, default=5),
        sa.Column("param_ranges", sa.JSON(), nullable=False),
        sa.Column("data_range_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_range_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_optimization_jobs_id"), "optimization_jobs", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_optimization_jobs_strategy"),
        "optimization_jobs",
        ["strategy"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_jobs_pair"), "optimization_jobs", ["pair"], unique=False
    )
    op.create_index(
        op.f("ix_optimization_jobs_status"),
        "optimization_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_jobs_created_by"),
        "optimization_jobs",
        ["created_by"],
        unique=False,
    )

    # Create optimization_trials table
    op.create_table(
        "optimization_trials",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        # Core metrics
        sa.Column("sharpe", sa.Float(), nullable=False),
        sa.Column("max_drawdown", sa.Float(), nullable=False),
        sa.Column("hit_rate", sa.Float(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=False),
        sa.Column("profit_factor", sa.Float(), nullable=False),
        # Extended metrics
        sa.Column("avg_win", sa.Float(), nullable=True),
        sa.Column("avg_loss", sa.Float(), nullable=True),
        sa.Column("max_consecutive_losses", sa.Integer(), nullable=True),
        sa.Column("avg_trade_duration", sa.Float(), nullable=True),
        # Pips-based metrics
        sa.Column("total_pips", sa.Float(), nullable=True),
        sa.Column("avg_pips_per_trade", sa.Float(), nullable=True),
        # Financing breakdown
        sa.Column("total_spread_cost_pips", sa.Float(), nullable=True),
        sa.Column("total_swap_cost_pips", sa.Float(), nullable=True),
        sa.Column("net_pips", sa.Float(), nullable=True),
        # Dollar-based metrics
        sa.Column("gross_pnl_dollars", sa.Float(), nullable=True),
        sa.Column("total_spread_cost_dollars", sa.Float(), nullable=True),
        sa.Column("total_swap_cost_dollars", sa.Float(), nullable=True),
        sa.Column("net_pnl_dollars", sa.Float(), nullable=True),
        # Gate B
        sa.Column("passes_gate_b", sa.Boolean(), nullable=True, default=False),
        sa.Column("gate_b_failures", sa.JSON(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["optimization_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_optimization_trials_id"), "optimization_trials", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_optimization_trials_job_id"),
        "optimization_trials",
        ["job_id"],
        unique=False,
    )

    # Create optimization_signals table
    op.create_table(
        "optimization_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trial_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.String(20), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("indicator_values", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["trial_id"], ["optimization_trials.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_optimization_signals_trial_id"),
        "optimization_signals",
        ["trial_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_signals_timestamp"),
        "optimization_signals",
        ["timestamp"],
        unique=False,
    )

    # Create optimization_trades table
    op.create_table(
        "optimization_trades",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("trial_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        # Pips
        sa.Column("pips", sa.Float(), nullable=False),
        sa.Column("spread_cost_pips", sa.Float(), nullable=False),
        sa.Column("swap_cost_pips", sa.Float(), nullable=False),
        sa.Column("net_pips", sa.Float(), nullable=False),
        # Dollars
        sa.Column("gross_pnl_dollars", sa.Float(), nullable=False),
        sa.Column("spread_cost_dollars", sa.Float(), nullable=False),
        sa.Column("swap_cost_dollars", sa.Float(), nullable=False),
        sa.Column("net_pnl_dollars", sa.Float(), nullable=False),
        # Metadata
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("position_size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trial_id"], ["optimization_trials.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_optimization_trades_id"), "optimization_trades", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_optimization_trades_trial_id"),
        "optimization_trades",
        ["trial_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_trades_entry_timestamp"),
        "optimization_trades",
        ["entry_timestamp"],
        unique=False,
    )


def downgrade():
    # Drop tables in reverse order (due to foreign keys)
    op.drop_index(
        op.f("ix_optimization_trades_entry_timestamp"), table_name="optimization_trades"
    )
    op.drop_index(
        op.f("ix_optimization_trades_trial_id"), table_name="optimization_trades"
    )
    op.drop_index(op.f("ix_optimization_trades_id"), table_name="optimization_trades")
    op.drop_table("optimization_trades")

    op.drop_index(
        op.f("ix_optimization_signals_timestamp"), table_name="optimization_signals"
    )
    op.drop_index(
        op.f("ix_optimization_signals_trial_id"), table_name="optimization_signals"
    )
    op.drop_table("optimization_signals")

    op.drop_index(
        op.f("ix_optimization_trials_job_id"), table_name="optimization_trials"
    )
    op.drop_index(op.f("ix_optimization_trials_id"), table_name="optimization_trials")
    op.drop_table("optimization_trials")

    op.drop_index(
        op.f("ix_optimization_jobs_created_by"), table_name="optimization_jobs"
    )
    op.drop_index(op.f("ix_optimization_jobs_status"), table_name="optimization_jobs")
    op.drop_index(op.f("ix_optimization_jobs_pair"), table_name="optimization_jobs")
    op.drop_index(op.f("ix_optimization_jobs_strategy"), table_name="optimization_jobs")
    op.drop_index(op.f("ix_optimization_jobs_id"), table_name="optimization_jobs")
    op.drop_table("optimization_jobs")

    # Drop enums
    sa.Enum(name="strategytype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="jobstatus").drop(op.get_bind(), checkfirst=True)
