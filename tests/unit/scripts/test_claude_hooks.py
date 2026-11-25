"""Exercise the Claude Code hook protocol without executing guarded commands."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOOKS = ROOT / ".claude" / "hooks"
CONTINUATION = "\\" + "\n"


def run_hook(hook, root, payload=None, **environment):
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    env.pop("CLAUDE_CODE_REMOTE", None)
    env.update(CLAUDE_PROJECT_DIR=str(root), **environment)
    return subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload or {}),
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("git push origin main", "Never push to `main`"),
        ("git push origin HEAD:main", "Never push to `main`"),
        ("git push origin HEAD:refs/heads/main", "Never push to `main`"),
        ("git push --force origin feat/agent-fleet", "Never force-push"),
        ("git push --force-with-lease origin feat/agent-fleet", "Never force-push"),
        ("docker compose --profile uat down -v", "deletes database volumes"),
        ("docker compose --profile dev down --volumes", "deletes database volumes"),
        ("docker compose down -v", "deletes database volumes"),
        ("docker volume prune", "would delete"),
        ("docker volume rm agentfx_postgres_data", "Refusing to remove"),
        ("docker volume rm agentfx_postgres_uat_data", "Refusing to remove"),
        (
            "echo safe # trailing comment\ndocker volume rm agentfx_postgres_uat_data",
            "Refusing to remove",
        ),
        ("cat <<'EOF'\nIt's text\nEOF\ngit push origin main", "Never push to `main`"),
        ("cat <<'EOF'\nno terminator", "cannot inspect the Bash command"),
        ("git push origin main; echo 'unterminated", "cannot inspect the Bash command"),
        ("make e2e PROFILE=uat", "may only run"),
        ("E2E_PROFILE=dev make e2e", "may only run"),
        ("export E2E_PROFILE=uat; ./scripts/run-e2e-tests.sh", "may only run"),
        (".venv/bin/pytest tests/unit", "Bare pytest"),
        ("python3 -m pytest", "Bare pytest"),
        ("npx vitest", "skips the parity"),
        ("gh pr create --base main", "specify the ticket branch"),
        ("gh pr create -Bmain -Hmain", "specify the ticket branch"),
        (
            "gh pr create --base main --body '--head=agent/AFX-101-hooks'",
            "specify the ticket branch",
        ),
        ("gh pr merge", "Do not merge PRs manually"),
        ("gh pr merge 42 --auto", "Do not merge PRs manually"),
        ("gh --repo widarecu/agentfx pr merge 42", "Do not merge PRs manually"),
        ("gh pr -Rwidarecu/agentfx merge 42", "Do not merge PRs manually"),
        ("uv run pytest", "Bare pytest"),
        ("uv run --no-sync python -m pytest", "Bare pytest"),
        ("echo ENVIRONMENT=test && pytest", "Bare pytest"),
        ("cd portal/web && npm test", "skips the parity"),
        ("cd portal/web\nnpm run test --silent", "skips the parity"),
        ('docker exec agentfx-postgres psql -c "DROP TABLE candles;"', "Refusing DROP"),
        (
            'docker exec -i agentfx-postgres-uat psql -c "SELECT 1;\ntruncate candles;"',
            "Refusing DROP",
        ),
        (
            f"docker exec agentfx-postgres-uat {CONTINUATION} psql -c 'DROP TABLE candles'",
            "Refusing DROP",
        ),
        (
            "docker exec -i agentfx-postgres-uat psql <<'SQL'\nTRUNCATE candles;\nSQL",
            "Refusing DROP",
        ),
        (
            'docker compose exec postgres-uat psql --command="DROP TABLE candles"',
            "Refusing DROP",
        ),
        ("ENVIRONMENT=uat alembic downgrade -1", "ENVIRONMENT=test"),
        ("env ENVIRONMENT=uat alembic downgrade -1", "ENVIRONMENT=test"),
        (
            "ENVIRONMENT=development .venv/bin/alembic downgrade base",
            "ENVIRONMENT=test",
        ),
        ("alembic downgrade -1", "ENVIRONMENT=test"),
        ("ENVIRONMENT=test echo harmless && alembic downgrade -1", "ENVIRONMENT=test"),
    ],
)
def test_bash_guard_refuses(command, reason):
    result = run_hook("guard-bash.py", ROOT, {"tool_input": {"command": command}})
    assert result.returncode == 2, result.stderr
    assert reason in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "make test-unit",
        "make test-integration",
        "make test-file FILE=tests/unit/scripts/test_claude_hooks.py",
        "git push origin agent/AFX-101-hooks",
        "git push --force-with-lease origin agent/AFX-101-hooks",
        "git status && git diff --stat",
        "echo '# literal' # docker volume prune",
        "printf '%s' foo#bar; git status",
        "cat <<'EOF'\nIt's text containing docker volume prune\nEOF\ngit status",
        "cat <<-'A' <<B\n\tE2E_PROFILE=uat\n\tA\npytest\nB\ngit status",
        "printf '%s' '<<' '#'; git status",
        "docker compose --profile test down -v",
        "docker compose --profile dev down",
        "docker volume rm agentfx_postgres_test_data",
        "make e2e",
        "make e2e PROFILE=test",
        "E2E_PROFILE=test ./scripts/run-e2e-tests.sh",
        "ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest tests/unit",
        "docker compose --profile test exec backend-test pytest",
        "gh pr create --base main --head agent/AFX-101-hooks",
        f"gh pr create --base main {CONTINUATION} --head agent/AFX-101-hooks",
        "gh pr create -B main -Hagent/AFX-101-hooks",
        "gh pr view 42",
        "gh pr checks 42",
        'docker exec agentfx-postgres-test psql -c "TRUNCATE TABLE candles;"',
        'docker exec agentfx-postgres-uat psql -c "SELECT 1; SELECT 2;"',
        "ENVIRONMENT=test alembic downgrade -1",
        "alembic upgrade head",
        "cd portal/web && npm run test:watch",
    ],
)
def test_bash_guard_allows(command):
    result = run_hook("guard-bash.py", ROOT, {"tool_input": {"command": command}})
    assert result.returncode == 0, result.stderr
    assert not result.stderr


@pytest.fixture
def ruff_project(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ruff").symlink_to(Path(sys.executable).with_name("ruff"))
    return tmp_path


def edit_python(root, path):
    return run_hook("lint-on-edit.py", root, {"tool_input": {"file_path": str(path)}})


def test_lint_hook_formats_edited_python(ruff_project):
    path = ruff_project / "example.py"
    path.write_text("value=[1,2]\n")
    result = edit_python(ruff_project, path)
    assert result.returncode == 0, result.stderr
    assert path.read_text() == "value = [1, 2]\n"


def test_lint_hook_still_reports_lint_errors(ruff_project):
    path = ruff_project / "example.py"
    path.write_text("import sys\n")
    result = edit_python(ruff_project, path)
    assert result.returncode == 2, result.stderr
    assert "F401" in result.stderr


def test_lint_hook_does_not_format_outside_project(ruff_project):
    root = ruff_project / "checkout"
    root.mkdir()
    (root / ".venv").symlink_to(ruff_project / ".venv", target_is_directory=True)
    path = ruff_project / "outside.py"
    path.write_text("value=1\n")
    link = root / "inside.py"
    link.symlink_to(path)
    assert edit_python(root, link).returncode == 0
    assert path.read_text() == "value=1\n"


def test_local_start_warns_once_instead_of_warning_on_each_edit(tmp_path):
    start = run_hook("session-start.py", tmp_path)
    assert start.returncode == 0, start.stderr
    assert "make dev" in json.loads(start.stdout)["systemMessage"]
    path = tmp_path / "example.py"
    path.write_text("value=1\n")
    result = edit_python(tmp_path, path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert path.read_text() == "value=1\n"


@pytest.fixture
def cloud_make(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    make = tools / "make"
    make.write_text(
        "#!/bin/sh\n"
        '[ "$1" = dev ] || exit 1\n'
        'echo "$1" >> make-calls\n'
        "mkdir -p .venv/bin && touch .venv/bin/ruff\n"
    )
    make.chmod(0o755)
    return {"CLAUDE_CODE_REMOTE": "true", "PATH": f"{tools}:{os.environ['PATH']}"}


def test_cloud_start_bootstraps_missing_venv_once(tmp_path, cloud_make):
    for _ in range(2):
        result = run_hook("session-start.py", tmp_path, **cloud_make)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert (tmp_path / "make-calls").read_text() == "dev\n"


def test_cloud_start_reports_bootstrap_failure(tmp_path, cloud_make):
    (tmp_path / "tools" / "make").write_text("#!/bin/sh\nexit 2\n")
    result = run_hook("session-start.py", tmp_path, **cloud_make)
    assert result.returncode == 0, result.stderr
    assert "Cloud setup could not finish" in json.loads(result.stdout)["systemMessage"]
