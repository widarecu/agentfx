"""The backend container runs `alembic upgrade head` on start-up, which
refuses to run when the migrations have more than one head. Two PRs that
each add a migration on the same parent pass CI on their own but leave
`main` with two heads, and the next deploy never starts (AFX-153)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migrations_have_a_single_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, (
        f"Alembic has {len(heads)} heads {sorted(heads)}; "
        "`alembic upgrade head` will fail on deploy. Add a merge migration "
        "whose down_revision is a tuple of these heads."
    )
