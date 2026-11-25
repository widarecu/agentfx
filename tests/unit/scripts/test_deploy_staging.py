"""Deploy plans, guards, verifies and records staging SHAs (AFX-121).

`scripts/deploy_staging.py` decides what the Deploy workflow deploys, refuses
stale deploys, verifies staging runs the commit and only then moves the
`deployed/staging` tag. Git runs for real against a throwaway repository;
every GitHub (`gh api`) and HTTP call is faked, so nothing is written to a
real API.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "deploy_staging.py"

_spec = importlib.util.spec_from_file_location("deploy_staging", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ds = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ds
_spec.loader.exec_module(ds)


# ─────────────────────────────────────────────────────────────────────
# Fixtures: a real git repo, fake HTTP and fake `gh api`
# ─────────────────────────────────────────────────────────────────────


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        self.git("remote", "add", "origin", str(path))
        self.initial = self.commit(
            {
                "src/agentfx/api/main.py": "app = 1\n",
                "portal/web/src/App.tsx": "export {}\n",
                "planning/ROADMAP.md": "# Roadmap\n",
                "tests/unit/test_x.py": "x = 1\n",
            },
            "initial",
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, files: dict[str, str], message: str = "change") -> str:
        for name, content in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, sha: str, name: str = ds.TAG) -> None:
        self.git("tag", "-f", name, sha)

    @property
    def g(self):
        return ds.Git(str(self.path))


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


class FakeHttp:
    """Replays one (status, body) per call for each URL; the last one repeats."""

    def __init__(self, responses: dict[str, list[tuple[int, str]]]) -> None:
        self.responses = {url: list(r) for url, r in responses.items()}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> tuple[int, str]:
        self.calls.append(url)
        queue = self.responses.get(url)
        if not queue:
            return 0, "connection refused"
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _health(sha: str | None) -> tuple[int, str]:
    return 200, json.dumps({"status": "healthy", "source_sha": sha})


_BACKEND = "https://backend.test"
_FRONTEND = "https://frontend.test"
_HEALTH = f"{_BACKEND}/health"


class FakeGh:
    """Passes git through to the real CLI, records `gh api` calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        if cmd[0] == "gh":
            self.calls.append(cmd)
            if self.fail:
                return subprocess.CompletedProcess(
                    cmd, 1, "", "HTTP 422: not a fast forward"
                )
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        return ds.run_cli(cmd)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line)


# ─────────────────────────────────────────────────────────────────────
# Change classification
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("planning/ROADMAP.md", set()),
        ("README.md", set()),
        ("src/agentfx/README.md", set()),
        ("portal/web/wireframes/home.html", set()),
        ("LICENSE", set()),
        ("tests/unit/test_x.py", set()),
        (".github/workflows/ci.yml", set()),
        ("src/agentfx/api/main.py", {"backend"}),
        ("migrations/versions/001.py", {"backend"}),
        ("requirements.txt", {"backend"}),
        ("Dockerfile", {"backend"}),
        (".railway/railway.ts", {"backend"}),
        ("config/financing_fallback.yaml", {"backend"}),
        ("scripts/seed_instruments.py", {"backend"}),
        ("scripts/pr_policy.py", set()),
        ("portal/web/src/App.tsx", {"frontend"}),
        ("portal/web/vercel.json", {"frontend"}),
        (".github/workflows/deploy.yml", {"backend", "frontend"}),
        ("scripts/deploy_staging.py", {"backend", "frontend"}),
        ("scripts/railway_deploy.py", {"backend", "frontend"}),
        ("src/agentfx/integrations/github_refs.py", {"backend", "frontend"}),
    ],
)
def test_components_for(path, expected):
    assert ds.components_for(path) == expected


# ─────────────────────────────────────────────────────────────────────
# plan
# ─────────────────────────────────────────────────────────────────────


def test_first_deployment_without_a_tag_deploys_both(repo):
    plan = ds.make_plan(repo.g, repo.initial, None)
    assert (plan.kind, plan.backend, plan.frontend, plan.base_sha) == (
        "first",
        True,
        True,
        "",
    )


def test_docs_only_deploys_nothing_but_is_planned(repo):
    sha = repo.commit({"planning/ROADMAP.md": "# Roadmap v2\n", "README.md": "x\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("docs-only", False, False)
    assert plan.outputs()["stale"] == "false"


def test_backend_only_change(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("backend", True, False)


def test_frontend_only_change(repo):
    sha = repo.commit({"portal/web/src/App.tsx": "export const a = 1\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("frontend", False, True)


def test_shared_deploy_file_change_deploys_both(repo):
    sha = repo.commit({".github/workflows/deploy.yml": "name: Deploy\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("both", True, True)


def test_changes_since_tag_accumulate_across_commits(repo):
    repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    sha = repo.commit({"portal/web/src/App.tsx": "export const a = 1\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("both", True, True)
    assert set(plan.files) == {"src/agentfx/api/main.py", "portal/web/src/App.tsx"}


def test_tests_only_change_needs_no_deploy(repo):
    sha = repo.commit({"tests/unit/test_x.py": "x = 2\n"})
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == (
        "no-runtime-change",
        False,
        False,
    )


def test_rerun_of_the_tagged_sha_deploys_nothing(repo):
    plan = ds.make_plan(repo.g, repo.initial, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == (
        "already-deployed",
        False,
        False,
    )


def test_stale_sha_older_than_the_tag_deploys_nothing(repo):
    old = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    new = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    plan = ds.make_plan(repo.g, old, new)
    assert (plan.kind, plan.backend, plan.frontend, plan.stale) == (
        "stale",
        False,
        False,
        True,
    )


def test_unrelated_tag_history_deploys_both(repo):
    repo.git("checkout", "-q", "--orphan", "other")
    orphan = repo.commit({"x.txt": "x\n"}, "orphan")
    repo.git("checkout", "-q", "main")
    plan = ds.make_plan(repo.g, repo.initial, orphan)
    assert (plan.kind, plan.backend, plan.frontend) == ("unrelated", True, True)


def test_revert_after_a_partial_deploy_still_redeploys_the_component(repo):
    """The live backend may run the reverted code; no net diff is not enough."""
    repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    sha = repo.commit({"src/agentfx/api/main.py": "app = 1\n"}, "revert")
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert (plan.kind, plan.backend, plan.frontend) == ("backend", True, False)


def test_moving_a_file_out_of_a_component_deploys_it(repo):
    repo.git("mv", "src/agentfx/api/main.py", "planning/main.py")
    sha = repo.commit({}, "move")
    plan = ds.make_plan(repo.g, sha, repo.initial)
    assert plan.backend is True
    assert "src/agentfx/api/main.py" in plan.files


def _diverged(repo):
    """Tag on a main-only hotfix; target a fleet commit without it."""
    repo.git("checkout", "-q", "-b", "fleet")
    fleet = repo.commit({"src/agentfx/api/main.py": "fleet\n"}, "fleet")
    repo.git("checkout", "-q", "main")
    hotfix = repo.commit({"portal/web/src/App.tsx": "hotfix\n"}, "hotfix")
    repo.git("merge", "-q", "--no-edit", "fleet")
    return fleet, hotfix


def test_diverged_target_cannot_drop_a_deployed_hotfix(repo):
    fleet, hotfix = _diverged(repo)
    assert ds.relation(repo.g, fleet, hotfix) == "diverged"
    with pytest.raises(ds.DeployError, match="does not contain"):
        ds.make_plan(repo.g, fleet, hotfix)
    assert "does not contain" in ds.guard(
        repo.g, fleet, "frontend", hotfix, fetch=FakeHttp({})
    )
    gh = FakeGh()
    with pytest.raises(ds.DeployError, match="does not contain"):
        ds.record(repo.g, fleet, hotfix, None, repo="o/r", runner=gh)
    assert gh.calls == []


def test_plan_cli_fails_for_a_diverged_target(repo, tmp_path, monkeypatch, capsys):
    fleet, hotfix = _diverged(repo)
    repo.tag(hotfix)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    code = ds.main(
        ["--repo-dir", str(repo.path), "plan", "--sha", fleet, "--main-ref", "main"]
    )
    assert code == 1
    assert "does not contain" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_target_must_be_on_main(repo):
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit({"src/agentfx/api/main.py": "side\n"})
    repo.git("checkout", "-q", "main")
    with pytest.raises(ds.DeployError, match="not on main"):
        ds.resolve_target(repo.g, side, "main")
    with pytest.raises(ds.DeployError, match="not a commit"):
        ds.resolve_target(repo.g, "deadbeef" * 5, "main")
    assert ds.resolve_target(repo.g, "main", "main") == repo.initial


def test_plan_cli_reads_the_remote_tag_and_writes_outputs(repo, tmp_path, monkeypatch):
    repo.tag(repo.initial)
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = ds.main(
        ["--repo-dir", str(repo.path), "plan", "--sha", sha, "--main-ref", "main"]
    )
    assert code == 0
    assert _outputs(out) == {
        "sha": sha,
        "base_sha": repo.initial,
        "relation": "newer",
        "kind": "backend",
        "backend": "true",
        "frontend": "false",
        "stale": "false",
    }


def test_plan_cli_rejects_a_sha_not_on_main(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    code = ds.main(
        ["--repo-dir", str(repo.path), "plan", "--sha", "nope", "--main-ref", "main"]
    )
    assert code == 1
    assert "::error::" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────
# guard
# ─────────────────────────────────────────────────────────────────────


def test_guard_allows_a_newer_commit(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    http = FakeHttp({_HEALTH: [_health(repo.initial)]})
    assert (
        ds.guard(repo.g, sha, "backend", repo.initial, fetch=http, backend_url=_BACKEND)
        == ""
    )


def test_guard_refuses_a_stale_retry_behind_the_tag(repo):
    old = repo.commit({"portal/web/src/App.tsx": "1\n"})
    new = repo.commit({"portal/web/src/App.tsx": "2\n"})
    problem = ds.guard(repo.g, old, "frontend", new, fetch=FakeHttp({}))
    assert "regress" in problem


def test_guard_refuses_when_the_live_backend_is_newer(repo):
    """A partial newer deploy left the tag behind; the live backend is the truth."""
    old = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    new = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    http = FakeHttp({_HEALTH: [_health(new)]})
    problem = ds.guard(
        repo.g, old, "backend", repo.initial, fetch=http, backend_url=_BACKEND
    )
    assert "already runs" in problem


def test_guard_allows_backend_when_health_is_unreachable(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    assert (
        ds.guard(repo.g, sha, "backend", None, fetch=FakeHttp({}), backend_url=_BACKEND)
        == ""
    )


def test_guard_cli_exits_non_zero_for_stale(repo, capsys):
    old = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    new = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    repo.tag(new)
    code = ds.main(
        [
            "--repo-dir",
            str(repo.path),
            "guard",
            "--sha",
            old,
            "--component",
            "frontend",
        ],
        fetch=FakeHttp({}),
    )
    assert code == 1
    assert "::error::Not deploying the frontend" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────
# verify
# ─────────────────────────────────────────────────────────────────────


def _verify_backend(repo, sha, deployed, http, timeout=60):
    clock = Clock()
    return ds.verify_backend(
        repo.g,
        sha,
        deployed,
        fetch=http,
        backend_url=_BACKEND,
        timeout=timeout,
        interval=10,
        clock=clock,
        sleep=clock.sleep,
    )


def test_verify_deployed_backend_reports_the_target(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    check = _verify_backend(repo, sha, True, FakeHttp({_HEALTH: [_health(sha)]}))
    assert check.ok and check.live_sha == sha and check.expected == sha


def test_verify_waits_for_traffic_to_switch(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    http = FakeHttp({_HEALTH: [(503, ""), _health(repo.initial), _health(sha)]})
    assert _verify_backend(repo, sha, True, http).ok
    assert len(http.calls) == 3


def test_verify_health_sha_mismatch_fails(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    check = _verify_backend(
        repo, sha, True, FakeHttp({_HEALTH: [_health(repo.initial)]})
    )
    assert not check.ok
    assert f"expected {sha[:12]}" in check.detail


@pytest.mark.parametrize(
    "response",
    [(503, "down"), (0, "timeout"), (200, "not json"), _health(None)],
)
def test_verify_backend_fails_without_a_reported_sha(repo, response):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    assert not _verify_backend(repo, sha, True, FakeHttp({_HEALTH: [response]})).ok


def test_verify_unchanged_backend_accepts_its_last_applicable_sha(repo):
    """Docs-only: the backend still runs the earlier build, which is correct."""
    backend_sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    docs_sha = repo.commit({"planning/ROADMAP.md": "# v3\n"})
    check = _verify_backend(
        repo, docs_sha, False, FakeHttp({_HEALTH: [_health(backend_sha)]})
    )
    assert check.ok
    assert check.expected == backend_sha
    assert "unchanged since" in check.detail


def test_verify_unchanged_backend_rejects_a_different_backend_source(repo):
    stale_build = repo.initial
    repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    docs_sha = repo.commit({"planning/ROADMAP.md": "# v3\n"})
    check = _verify_backend(
        repo, docs_sha, False, FakeHttp({_HEALTH: [_health(stale_build)]})
    )
    assert not check.ok
    assert "backend source differs" in check.detail


def test_verify_unchanged_backend_rejects_an_unknown_sha(repo):
    docs_sha = repo.commit({"planning/ROADMAP.md": "# v3\n"})
    check = _verify_backend(
        repo, docs_sha, False, FakeHttp({_HEALTH: [_health("ab" * 20)]})
    )
    assert not check.ok


def _verify_frontend(repo, sha, deployed, http):
    clock = Clock()
    return ds.verify_frontend(
        repo.g,
        sha,
        deployed,
        fetch=http,
        frontend_url=_FRONTEND,
        timeout=30,
        interval=10,
        clock=clock,
        sleep=clock.sleep,
    )


def test_verify_frontend_url_serves(repo):
    sha = repo.commit({"portal/web/src/App.tsx": "1\n"})
    check = _verify_frontend(repo, sha, True, FakeHttp({_FRONTEND: [(200, "<html>")]}))
    assert check.ok and check.expected == sha


def test_verify_unchanged_frontend_names_its_last_applicable_sha(repo):
    fe_sha = repo.commit({"portal/web/src/App.tsx": "1\n"})
    be_sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    check = _verify_frontend(
        repo, be_sha, False, FakeHttp({_FRONTEND: [(200, "<html>")]})
    )
    assert check.ok and check.expected == fe_sha
    assert f"unchanged since {fe_sha[:12]}" in check.detail


def test_verify_frontend_failure(repo):
    check = _verify_frontend(
        repo, repo.initial, True, FakeHttp({_FRONTEND: [(404, "")]})
    )
    assert not check.ok and "HTTP 404" in check.detail


def test_verify_cli_writes_outcomes_and_fails_on_mismatch(repo, tmp_path, monkeypatch):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    http = FakeHttp({_HEALTH: [_health(repo.initial)], _FRONTEND: [(200, "<html>")]})
    clock = Clock()
    argv = [
        "--repo-dir",
        str(repo.path),
        "verify",
        "--sha",
        sha,
        "--backend-deployed",
        "true",
        "--backend-url",
        _BACKEND,
        "--frontend-url",
        _FRONTEND,
        "--timeout",
        "30",
    ]
    assert ds.main(argv, fetch=http, clock=clock, sleep=clock.sleep) == 1
    outputs = _outputs(out)
    assert outputs["backend_check"] == "failed"
    assert outputs["frontend_check"] == "passed"
    assert outputs["backend_live_sha"] == repo.initial


# ─────────────────────────────────────────────────────────────────────
# record
# ─────────────────────────────────────────────────────────────────────


def _gh_paths(gh: FakeGh) -> list[tuple[str, str, list[str]]]:
    return [(c[3], c[4], c[5:]) for c in gh.calls]


def test_record_first_deployment_creates_the_tag(repo):
    gh = FakeGh()
    result = ds.record(repo.g, repo.initial, None, None, repo="o/r", runner=gh)
    assert result.recorded and result.previous_sha == ""
    assert _gh_paths(gh) == [
        (
            "POST",
            "repos/o/r/git/refs",
            ["-f", f"ref={ds.TAG_REF}", "-f", f"sha={repo.initial}"],
        )
    ]


def test_record_preserves_previous_sha_before_fast_forwarding(repo):
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    gh = FakeGh()
    result = ds.record(repo.g, sha, repo.initial, None, repo="o/r", runner=gh)
    assert result.recorded and result.previous_sha == repo.initial
    assert _gh_paths(gh) == [
        (
            "POST",
            "repos/o/r/git/refs",
            ["-f", f"ref={ds.PREVIOUS_TAG_REF}", "-f", f"sha={repo.initial}"],
        ),
        (
            "PATCH",
            f"repos/o/r/git/refs/tags/{ds.TAG}",
            ["-f", f"sha={sha}", "-F", "force=false"],
        ),
    ]


def test_record_updates_an_existing_previous_tag(repo):
    mid = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    sha = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    gh = FakeGh()
    ds.record(repo.g, sha, mid, repo.initial, repo="o/r", runner=gh)
    assert _gh_paths(gh)[0] == (
        "PATCH",
        f"repos/o/r/git/refs/tags/{ds.PREVIOUS_TAG}",
        ["-f", f"sha={mid}", "-F", "force=true"],
    )


@pytest.mark.parametrize("outcome", ["retry", "applied", "changed", "persistent"])
def test_record_cli_reconciles_ambiguous_staging_tag_write(
    repo, tmp_path, monkeypatch, capsys, outcome
):
    target = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    other = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    repo.tag(repo.initial)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gh = FakeGh()
    staging_writes = 0
    delays = []
    monkeypatch.setattr(ds.time, "sleep", delays.append)
    staging_write = (
        "PATCH",
        f"repos/o/r/git/refs/tags/{ds.TAG}",
        ["-f", f"sha={target}", "-F", "force=false"],
    )
    staging_read = ("GET", f"repos/o/r/git/ref/tags/{ds.TAG}", [])

    def run(cmd):
        nonlocal staging_writes
        result = gh(cmd)
        if cmd[1:5] == ["api", "-X", *staging_write[:2]]:
            staging_writes += 1
            if staging_writes == 1 or outcome == "persistent":
                if outcome == "applied":
                    repo.tag(target)
                elif outcome == "changed":
                    repo.tag(other)
                return subprocess.CompletedProcess(
                    cmd, 1, "", "unexpected end of JSON input"
                )
            repo.tag(target)
        if cmd[1:] == ["api", "-X", *staging_read[:2]]:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"object": {"sha": repo.g.resolve(ds.TAG)}}), ""
            )
        return result

    code = ds.main(
        ["--repo-dir", str(repo.path), "record", "--sha", target, "--repo", "o/r"],
        runner=run,
    )

    attempts = [staging_write, staging_read]
    if outcome == "retry":
        attempts.append(staging_write)
    elif outcome == "persistent":
        attempts *= 3
    assert _gh_paths(gh) == [
        (
            "POST",
            "repos/o/r/git/refs",
            ["-f", f"ref={ds.PREVIOUS_TAG_REF}", "-f", f"sha={repo.initial}"],
        ),
        *attempts,
    ]
    assert delays == ([1, 2, 4] if outcome == "persistent" else [1])
    if outcome in ("retry", "applied"):
        assert code == 0
        assert _outputs(out) == {"recorded": "true", "previous_sha": repo.initial}
        assert repo.g.resolve(ds.TAG) == target
    else:
        assert code == 1
        assert not out.exists()
        diagnostic = capsys.readouterr().out
        assert ds.TAG_REF in diagnostic
        if outcome == "changed":
            assert "refusing to retry" in diagnostic
            assert repo.g.resolve(ds.TAG) == other
        else:
            assert "unexpected end of JSON input" in diagnostic
            assert repo.g.resolve(ds.TAG) == repo.initial


def test_record_is_a_noop_when_already_recorded(repo):
    gh = FakeGh()
    result = ds.record(
        repo.g, repo.initial, repo.initial, "ab" * 20, repo="o/r", runner=gh
    )
    assert not result.recorded and result.previous_sha == "ab" * 20
    assert gh.calls == []


def test_record_never_moves_the_tag_backwards(repo):
    old = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    new = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    gh = FakeGh()
    result = ds.record(repo.g, old, new, repo.initial, repo="o/r", runner=gh)
    assert not result.recorded and "left unchanged" in result.detail
    assert gh.calls == []


def test_record_cli_api_failure_exits_non_zero(repo, tmp_path, monkeypatch, capsys):
    repo.tag(repo.initial)
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gh = FakeGh(fail=True)
    code = ds.main(["--repo-dir", str(repo.path), "record", "--sha", sha], runner=gh)
    assert code == 1
    assert "not a fast forward" in capsys.readouterr().out
    assert not out.exists()


def test_record_cli_reads_both_remote_tags(repo, tmp_path, monkeypatch):
    mid = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    sha = repo.commit({"portal/web/src/App.tsx": "1\n"})
    repo.tag(mid)
    repo.tag(repo.initial, ds.PREVIOUS_TAG)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gh = FakeGh()
    code = ds.main(
        ["--repo-dir", str(repo.path), "record", "--sha", sha, "--repo", "o/r"],
        runner=gh,
    )
    assert code == 0
    assert _outputs(out) == {"recorded": "true", "previous_sha": mid}
    assert [c[3] for c in gh.calls] == ["PATCH", "PATCH"]


# ─────────────────────────────────────────────────────────────────────
# summary
# ─────────────────────────────────────────────────────────────────────

_A = "a" * 40
_B = "b" * 40


def _summary(**overrides):
    values = dict(
        sha=_B,
        plan_result="success",
        kind="both",
        backend_planned=True,
        frontend_planned=True,
        backend_result="success",
        frontend_result="success",
        verify_result="success",
        record_result="success",
        recorded=True,
        base_sha=_A,
        previous_sha=_A,
        stale_now=False,
    )
    values.update(overrides)
    return ds.summarize(**values)


def test_summary_full_success_moves_the_tag():
    result = _summary()
    assert (
        result["outcome"],
        result["backend"],
        result["frontend"],
        result["verify"],
    ) == (
        "success",
        "deployed",
        "deployed",
        "passed",
    )
    assert (result["tag"], result["previous_sha"], result["deployed_sha"]) == (
        "moved",
        _A,
        _B,
    )


def test_summary_docs_only_is_an_explicit_success():
    result = _summary(
        kind="docs-only",
        backend_planned=False,
        frontend_planned=False,
        backend_result="skipped",
        frontend_result="skipped",
    )
    assert (result["outcome"], result["backend"], result["frontend"]) == (
        "success",
        "unchanged",
        "unchanged",
    )


def test_summary_partial_deploy_leaves_the_tag_unchanged():
    result = _summary(
        frontend_result="failure",
        verify_result="skipped",
        record_result="skipped",
        recorded=False,
        previous_sha="",
    )
    assert (
        result["outcome"],
        result["backend"],
        result["frontend"],
        result["verify"],
    ) == (
        "failure",
        "deployed",
        "failed",
        "not-run",
    )
    assert (result["tag"], result["previous_sha"], result["deployed_sha"]) == (
        "unchanged",
        _A,
        _A,
    )
    assert "Re-run failed jobs" in ds.summary_markdown(result)


def test_summary_verify_failure_leaves_the_tag_unchanged():
    result = _summary(
        verify_result="failure",
        record_result="skipped",
        recorded=False,
        previous_sha="",
    )
    assert (result["outcome"], result["verify"], result["tag"]) == (
        "failure",
        "failed",
        "unchanged",
    )


def test_summary_record_api_failure_is_a_failure():
    result = _summary(record_result="failure", recorded=False, previous_sha="")
    assert (result["outcome"], result["tag"], result["deployed_sha"]) == (
        "failure",
        "unchanged",
        _A,
    )


def test_summary_rerun_of_failed_jobs_recovers():
    """Second attempt: the frontend job re-ran green, verify and record ran."""
    assert _summary(frontend_result="success")["outcome"] == "success"


def test_summary_plan_failure_reports_nothing_run():
    result = _summary(
        plan_result="failure",
        kind="",
        backend_result="skipped",
        frontend_result="skipped",
        verify_result="skipped",
        record_result="skipped",
        recorded=False,
        base_sha="",
    )
    assert (
        result["outcome"],
        result["backend"],
        result["frontend"],
        result["verify"],
    ) == (
        "failure",
        "not-run",
        "not-run",
        "not-run",
    )


def test_summary_stale_sha_is_not_a_failure_and_keeps_the_tag():
    result = _summary(
        kind="stale",
        backend_planned=False,
        frontend_planned=False,
        verify_result="skipped",
        record_result="skipped",
        recorded=False,
    )
    assert (result["outcome"], result["tag"]) == ("stale", "unchanged")


def test_summary_cli_writes_json_and_fails_the_run(repo, tmp_path, monkeypatch):
    repo.tag(repo.initial)
    sha = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    out, page, result_file = tmp_path / "out", tmp_path / "page.md", tmp_path / "r.json"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    argv = [
        "--repo-dir",
        str(repo.path),
        "summary",
        "--sha",
        sha,
        "--plan-result",
        "success",
        "--kind",
        "backend",
        "--base-sha",
        repo.initial,
        "--backend-planned",
        "true",
        "--backend-result",
        "failure",
        "--frontend-result",
        "skipped",
        "--verify-result",
        "skipped",
        "--record-result",
        "skipped",
        "--json-out",
        str(result_file),
    ]
    assert ds.main(argv) == 1
    result = json.loads(result_file.read_text())
    assert (result["outcome"], result["backend"], result["frontend"]) == (
        "failure",
        "failed",
        "unchanged",
    )
    assert _outputs(out)["deployed_sha"] == repo.initial
    assert "Re-run failed jobs" in page.read_text()


def test_summary_cli_detects_a_stale_rerun_against_the_live_tag(
    repo, tmp_path, monkeypatch
):
    """Re-running an old run's failed job after a newer deploy succeeded."""
    old = repo.commit({"src/agentfx/api/main.py": "app = 2\n"})
    new = repo.commit({"src/agentfx/api/main.py": "app = 3\n"})
    repo.tag(new)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    argv = [
        "--repo-dir",
        str(repo.path),
        "summary",
        "--sha",
        old,
        "--plan-result",
        "success",
        "--kind",
        "backend",
        "--backend-planned",
        "true",
        "--backend-result",
        "failure",
        "--json-out",
        str(tmp_path / "r.json"),
    ]
    assert ds.main(argv) == 0
    assert json.loads((tmp_path / "r.json").read_text())["outcome"] == "stale"


# ─────────────────────────────────────────────────────────────────────
# Retry scenario end to end
# ─────────────────────────────────────────────────────────────────────


def test_manual_sha_retry_after_partial_failure_recovers(repo):
    """Attempt 1 at B: backend deployed, frontend failed, tag stays at A.

    Retrying B replans from A (both components), the backend redeploy is
    idempotent, and record moves A → B keeping A as the previous SHA.
    """
    a = repo.initial
    b = repo.commit(
        {"src/agentfx/api/main.py": "app = 2\n", "portal/web/src/App.tsx": "1\n"}
    )
    first = _summary(
        sha=b,
        base_sha=a,
        frontend_result="failure",
        verify_result="skipped",
        record_result="skipped",
        recorded=False,
        previous_sha="",
    )
    assert first["outcome"] == "failure" and first["deployed_sha"] == a

    retry = ds.make_plan(repo.g, b, a)
    assert (retry.backend, retry.frontend) == (True, True)
    http = FakeHttp({_HEALTH: [_health(b)]})
    assert ds.guard(repo.g, b, "backend", a, fetch=http, backend_url=_BACKEND) == ""
    assert _verify_backend(repo, b, True, http).ok
    gh = FakeGh()
    recorded = ds.record(repo.g, b, a, None, repo="o/r", runner=gh)
    assert recorded.recorded and recorded.previous_sha == a
