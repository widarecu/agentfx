"""Promoted-to-main Linear comment (AFX-68).

`scripts/fleet_promoted_to_main.py` comments on every `AFX-<n>` issue a PR
merged into `main` carries. These cases pin which commits count as "just
landed", where the ids come from, the comment shape, dedupe and the
never-fail paths (no ids, no Linear key, Linear down).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "fleet_promoted_to_main", _SCRIPTS / "fleet_promoted_to_main.py"
)
assert _spec is not None and _spec.loader is not None
pm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pm
_spec.loader.exec_module(pm)

_URL = "https://github.com/widarecu/agentfx/pull"


def _main_pr(n: int, title: str, head: str = "feat/agent-fleet", **kw) -> dict:
    pr = {
        "number": n,
        "title": title,
        "html_url": f"{_URL}/{n}",
        "merged_at": "2026-11-13T12:17:22Z",
        "merge_commit_sha": f"m{n}",
        "base": {"ref": "main"},
        "head": {"ref": head},
    }
    pr.update(kw)
    return pr


def _lander(n: int, title: str, sha: str, head: str = "agent/x", base=None) -> dict:
    return {
        "number": n,
        "title": title,
        "merged_at": "2026-11-13T00:00:00Z",
        "merge_commit_sha": sha,
        "base": {"ref": base or "feat/agent-fleet"},
        "head": {"ref": head},
    }


class FakeGitHub:
    """`commits/{sha}/pulls`, keyed by sha."""

    def __init__(self, pulls: dict[str, list[dict]] | None = None) -> None:
        self.pulls = pulls or {}
        self.calls: list[str] = []

    def __call__(self, sha: str) -> list[dict]:
        self.calls.append(sha)
        return self.pulls.get(sha, [])


class FakeLinear(pm.Linear):
    """In-memory Linear: issues with comment pages, records posts."""

    def __init__(self, comments: dict[str, list[str]] | None = None, page=2):
        super().__init__("key")
        self.store = comments if comments is not None else {}
        self.page = page
        self.posted: list[tuple[str, str]] = []
        self.down = False

    def _gql(self, query: str, variables: dict) -> dict:
        if self.down:
            raise RuntimeError("Linear 503")
        if "commentCreate" in query:
            self.store[variables["id"]].append(variables["body"])
            self.posted.append((variables["id"], variables["body"]))
            return {"commentCreate": {"success": True}}
        if "comments(" in query:
            bodies = self.store[variables["id"]]
            start = int(variables["after"] or 0)
            chunk = bodies[start : start + self.page]
            more = start + self.page < len(bodies)
            return {
                "issue": {
                    "comments": {
                        "nodes": [{"body": b} for b in chunk],
                        "pageInfo": {
                            "hasNextPage": more,
                            "endCursor": str(start + self.page),
                        },
                    }
                }
            }
        ident = variables["id"]
        if ident not in self.store:
            raise RuntimeError(f"issue {ident} not found")
        return {"issue": {"id": ident, "identifier": ident, "labels": {"nodes": []}}}


# ─────────────────────────────────────────────────────────────────────
# Comment shape
# ─────────────────────────────────────────────────────────────────────


def test_comment_matches_ticket_shape():
    body = pm.promoted_comment(
        _main_pr(116, "Promote"), deploys_url="https://railway.com/project/p"
    )
    assert body == (
        f"Promoted to `main` in [PR #116]({_URL}/116) at 2026-11-13T12:17:22Z.\n"
        "Staging deploy: https://railway.com/project/p.\n\n"
        "<!-- afx-68:promoted-to-main:116 -->\n"
        "_Posted by agent-fleet-promoted-to-main.yml (AFX-68)._"
    )


def test_default_deploy_link_is_the_staging_service():
    assert pm.RAILWAY_DEPLOYS_URL.startswith("https://railway.com/project/")
    assert "/service/" in pm.RAILWAY_DEPLOYS_URL


# ─────────────────────────────────────────────────────────────────────
# Which merged PR counts as the lander of a commit
# ─────────────────────────────────────────────────────────────────────


def test_lander_must_be_merged_under_that_sha():
    good = _lander(115, "AFX-87: x", "abc")
    other = _lander(114, "AFX-1: y", "zzz")
    unmerged = {**_lander(113, "AFX-2", "abc"), "merged_at": None}
    assert pm.landing_prs([other, unmerged, good], "abc") == [good]


def test_back_merge_pr_is_never_a_lander():
    assert pm.landing_prs([_lander(117, "Back-merge", "abc", head="main")], "abc") == []


# ─────────────────────────────────────────────────────────────────────
# AFX ids
# ─────────────────────────────────────────────────────────────────────


def test_single_afx_id_direct_merge():
    pr = _main_pr(94, "AFX-73: mount agentfx", head="devin/afx-73-fix")
    commits = [("m94", "AFX-73: mount agentfx (#94)")]
    assert pm.collect_ids(pr, commits, FakeGitHub()) == ["AFX-73"]


def test_multi_afx_ids_from_subjects_and_lander_titles_deduped():
    pr = _main_pr(116, "Promote feat/agent-fleet → main: 2026-11-13")
    commits = [
        ("m116", "Merge pull request #116 from widarecu/feat/agent-fleet"),
        ("c115", "Merge pull request #115 from widarecu/agent/AFX-87-tick"),
        ("c113", "Merge pull request #113 from widarecu/main"),
        ("c110", "afx-8: pool health (#110)"),
    ]
    gh = FakeGitHub(
        {
            "c115": [_lander(115, "AFX-87: signal tick waits; AFX-92", "c115")],
            "c113": [_lander(113, "Back-merge AFX-99", "c113", head="main")],
            "c110": [_lander(110, "AFX-8: pool health", "c110")],
        }
    )
    assert pm.collect_ids(pr, commits, gh) == ["AFX-87", "AFX-92", "AFX-8"]


def test_empty_merge_has_no_ids():
    pr = _main_pr(120, "chore: bump deps", head="wilson/deps")
    assert pm.collect_ids(pr, [("m120", "chore: bump deps (#120)")], FakeGitHub()) == []


# ─────────────────────────────────────────────────────────────────────
# Posting
# ─────────────────────────────────────────────────────────────────────


def _post(pr, ids, linear, **kw):
    return pm.post_comments(pr, ids, linear, deploys_url="https://railway.com/x", **kw)


def test_single_issue_gets_one_comment():
    linear = FakeLinear({"AFX-73": []})
    assert _post(_main_pr(94, "AFX-73"), ["AFX-73"], linear) == ["AFX-73"]
    ((ident, body),) = linear.posted
    assert ident == "AFX-73" and pm.promoted_marker(94) in body


def test_multi_issue_each_get_a_comment():
    linear = FakeLinear({"AFX-87": [], "AFX-8": []})
    assert _post(_main_pr(116, "P"), ["AFX-87", "AFX-8"], linear) == [
        "AFX-87",
        "AFX-8",
    ]


def test_duplicate_run_does_not_repost_even_past_page_one():
    linear = FakeLinear({"AFX-87": ["a", "b", "c", "d"]}, page=2)
    assert _post(_main_pr(116, "P"), ["AFX-87"], linear) == ["AFX-87"]
    # Marker now sits on page 3; a fresh client must still find it.
    rerun = FakeLinear(linear.store, page=2)
    assert _post(_main_pr(116, "P"), ["AFX-87"], rerun) == []
    assert rerun.posted == []


def test_back_to_back_merges_do_not_cross_contaminate():
    linear = FakeLinear({"AFX-87": [], "AFX-8": []})
    _post(_main_pr(116, "P"), ["AFX-87"], linear)
    _post(_main_pr(118, "P"), ["AFX-8"], linear)
    assert [i for i, _ in linear.posted] == ["AFX-87", "AFX-8"]
    assert pm.promoted_marker(116) in linear.store["AFX-87"][0]
    assert pm.promoted_marker(118) not in "".join(linear.store["AFX-87"])
    assert pm.promoted_marker(118) in linear.store["AFX-8"][0]


def test_new_promote_still_comments_on_issue_seen_in_an_earlier_one():
    linear = FakeLinear({"AFX-8": []})
    _post(_main_pr(116, "P"), ["AFX-8"], linear)
    assert _post(_main_pr(118, "P"), ["AFX-8"], linear) == ["AFX-8"]


def test_linear_outage_is_logged_and_skipped(capsys):
    linear = FakeLinear({"AFX-8": []})
    linear.down = True
    assert _post(_main_pr(116, "P"), ["AFX-8"], linear) == []
    assert "::warning ::" in capsys.readouterr().out


def test_unknown_issue_is_skipped_and_others_still_posted():
    linear = FakeLinear({"AFX-8": []})
    assert _post(_main_pr(116, "P"), ["AFX-404", "AFX-8"], linear) == ["AFX-8"]


def test_dry_run_prints_and_posts_nothing(capsys):
    linear = FakeLinear({"AFX-8": []})
    assert _post(_main_pr(116, "P"), ["AFX-8"], linear, dry_run=True) == []
    assert linear.posted == []
    assert pm.promoted_marker(116) in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────
# main(): never fails the workflow
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired(monkeypatch):
    """main() with GitHub, git and Linear faked out."""
    state = {"pr": _main_pr(116, "Promote"), "commits": [], "pulls": FakeGitHub()}
    state["linear"] = FakeLinear({"AFX-87": []})
    monkeypatch.setattr(pm, "fetch_pr", lambda repo, n: state["pr"])
    monkeypatch.setattr(pm, "landed_commits", lambda sha: state["commits"])
    monkeypatch.setattr(pm, "pulls_for_commit", lambda repo: state["pulls"])
    monkeypatch.setattr(pm, "Linear", lambda key: state["linear"])
    return state


def test_main_posts_on_a_promote(wired, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    wired["commits"] = [("c115", "Merge pull request #115 from x/agent/AFX-87-t")]
    assert pm.main(["--pr", "116"]) == 0
    assert [i for i, _ in wired["linear"].posted] == ["AFX-87"]


def test_main_without_linear_key_logs_and_exits_zero(wired, monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    wired["commits"] = [("c115", "AFX-87: x")]
    assert pm.main(["--pr", "116"]) == 0
    assert "LINEAR_API_KEY not set" in capsys.readouterr().out
    assert wired["linear"].posted == []


def test_main_no_ids_is_a_noop(wired, monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    wired["commits"] = [("m116", "chore: nothing tagged")]
    assert pm.main(["--pr", "116"]) == 0
    assert "no AFX ids" in capsys.readouterr().out
    assert wired["linear"].posted == []


@pytest.mark.parametrize(
    "override",
    [
        {"merged_at": None},
        {"base": {"ref": "feat/agent-fleet"}},
        {"head": {"ref": "main"}, "base": {"ref": "feat/agent-fleet"}},
    ],
)
def test_main_ignores_unmerged_and_non_main_prs(wired, monkeypatch, override):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    wired["pr"] = {**wired["pr"], **override}
    wired["commits"] = [("c115", "AFX-87: x")]
    assert pm.main(["--pr", "116"]) == 0
    assert wired["linear"].posted == []


# ─────────────────────────────────────────────────────────────────────
# Which commits just landed on main (real git repo)
# ─────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a").write_text("init")
    _git(tmp_path, "add", "a")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "branch", "feat/agent-fleet")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, branch: str, name: str, subject: str) -> str:
    _git(repo, "checkout", "-q", branch)
    (repo / name).write_text(subject)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", subject)
    return _git(repo, "rev-parse", "HEAD")


def _merge(repo: Path, into: str, src: str, subject: str) -> str:
    _git(repo, "checkout", "-q", into)
    _git(repo, "merge", "-q", "--no-ff", "-m", subject, src)
    return _git(repo, "rev-parse", "HEAD")


def _subjects(commits) -> list[str]:
    return [s for _, s in commits]


def test_promote_lands_fleet_first_parent_chain(repo):
    _git(repo, "checkout", "-q", "-b", "agent/afx-87", "feat/agent-fleet")
    _commit(repo, "agent/afx-87", "w1", "wip inside the agent PR AFX-999")
    _merge(repo, "feat/agent-fleet", "agent/afx-87", "Merge #115 from agent/AFX-87")
    _commit(repo, "feat/agent-fleet", "b", "AFX-8: pool (#110)")
    m = _merge(repo, "main", "feat/agent-fleet", "Merge #116 from feat/agent-fleet")
    assert _subjects(pm.landed_commits(m)) == [
        "Merge #116 from feat/agent-fleet",
        "AFX-8: pool (#110)",
        "Merge #115 from agent/AFX-87",
    ]


def test_squash_or_direct_merge_lands_one_commit(repo):
    sha = _commit(repo, "main", "h", "AFX-73: hotfix (#94)")
    assert _subjects(pm.landed_commits(sha)) == ["AFX-73: hotfix (#94)"]


def test_back_to_back_promotes_only_see_their_own_commits(repo):
    _commit(repo, "feat/agent-fleet", "b", "AFX-1: first")
    _merge(repo, "main", "feat/agent-fleet", "promote 1")
    _merge(repo, "feat/agent-fleet", "main", "back-merge")
    _commit(repo, "feat/agent-fleet", "c", "AFX-2: second")
    m2 = _merge(repo, "main", "feat/agent-fleet", "promote 2")
    assert _subjects(pm.landed_commits(m2)) == [
        "promote 2",
        "AFX-2: second",
        "back-merge",
    ]
