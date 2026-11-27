"""Merge the paper-runner liveness (AFX-99) and parameter robustness (AFX-153) heads.

Both migrations were added on top of a7d1e94c3b25 in PRs that merged
minutes apart, leaving two heads, so `alembic upgrade head` refused to run.
This merge changes no schema.

Revision ID: d5f2a8c6e913
Revises: 3c9e5d07a1f4, b4e8c21f6d39
"""

revision = "d5f2a8c6e913"
down_revision = ("3c9e5d07a1f4", "b4e8c21f6d39")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
