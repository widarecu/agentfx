"""Promote-PR renderer (AFX-69).

`scripts/fleet_promote_pr.py` opens / refreshes the `feat/agent-fleet` ->
`main` promote PR. These cases pin the pure parts: which merged PRs are
listed, risk resolution, body layout (Money-path first, size cap), marker
replacement, open-PR selection and the Linear comment dedupe marker.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "fleet_promote_pr.py"

_spec = importlib.util.spec_from_file_location("fleet_promote_pr", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)
# dataclasses resolve string annotations through sys.modules.
sys.modules[_spec.name] = fp
_spec.loader.exec_module(fp)

_NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
_SHA = "b23d879" + "0" * 33


def _pr(n: int, title: str, risk: str = "Normal", files=None) -> fp.MergedPR:
    return fp.MergedPR(
        number=n,
        title=title,
        url=f"https://github.com/widarecu/agentfx/pull/{n}",
        files=files if files is not None else ["src/a.py"],
        risk=risk,
    )


def _render(prs, direct=(), **kw) -> str:
    return fp.render_section(
        list(prs), list(direct), repo="widarecu/agentfx", head_sha=_SHA, now=_NOW, **kw
    )


# ─────────────────────────────────────────────────────────────────────
# Which merged PRs are listed
# ─────────────────────────────────────────────────────────────────────


def _gh_pr(n, head, base="feat/agent-fleet", sha="abc", merged=True):
    return {
        "number": n,
        "merged_at": "2026-11-11T00:00:00Z" if merged else None,
        "base": {"ref": base},
        "head": {"ref": head},
        "merge_commit_sha": sha,
    }


def test_agent_pr_landing_the_commit_is_listed():
    pr = _gh_pr(101, "agent/afx-8")
    assert fp.pick_merged_pr([pr], "abc") is pr


def test_back_merge_pr_is_skipped():
    assert fp.pick_merged_pr([_gh_pr(104, "main")], "abc") is None


def test_pr_into_main_or_unmerged_is_not_the_lander():
    into_main = _gh_pr(103, "promote/x", base="main")
    unmerged = _gh_pr(99, "agent/afx-51", merged=False)
    assert fp.pick_merged_pr([into_main, unmerged], "abc") is None


def test_pr_merged_under_a_different_sha_is_not_the_lander():
    assert fp.pick_merged_pr([_gh_pr(96, "agent/x", sha="zzz")], "abc") is None


# ─────────────────────────────────────────────────────────────────────
# AFX ids + risk
# ─────────────────────────────────────────────────────────────────────


def test_afx_ids_are_upper_cased_and_deduped():
    assert fp.afx_ids("afx-66 promote: AFX-66 + AFX-7 (#88)") == ["AFX-66", "AFX-7"]
    assert fp.afx_ids("Back-merge main → feat/agent-fleet") == []


def test_money_path_on_any_linear_issue_wins():
    assert fp.pick_risk(["Normal", "Money-path"], []) == "Money-path"


def test_linear_normal_beats_github_fallback():
    assert fp.pick_risk([None, "Normal"], ["needs-wilson"]) == "Normal"


def test_needs_wilson_label_is_money_path_without_linear():
    risk = fp.pick_risk([], ["agent-authored", "needs-wilson"])
    assert fp.is_money_path(risk)
    assert "inferred" in risk


def test_unknown_when_nothing_says():
    assert fp.pick_risk([None], ["agent-authored"]) == "unknown"


# ─────────────────────────────────────────────────────────────────────
# Body layout
# ─────────────────────────────────────────────────────────────────────


def test_line_format_matches_ticket():
    line = fp.render_line(_pr(101, "AFX-8: pool health", files=["a.py", "b.py"]))
    assert line == "- #101 AFX-8: pool health — risk: Normal — files: `a.py`, `b.py`"


def test_files_truncate_with_count():
    files = [f"f{i}.py" for i in range(11)]
    assert fp.format_files(files).endswith("+3 more")
    assert fp.format_files([]) == "(none)"


def test_money_path_prs_surface_at_the_top():
    body = _render(
        [_pr(100, "AFX-72: hygiene"), _pr(101, "AFX-8: sizing", "Money-path")]
    )
    assert body.index("#101") < body.index("#100")
    assert body.index("Money-path — review first") < body.index("Merged PRs")


def test_merge_order_kept_within_a_group():
    body = _render([_pr(96, "AFX-48"), _pr(100, "AFX-72"), _pr(102, "AFX-55")])
    assert body.index("#96") < body.index("#100") < body.index("#102")


def test_body_is_wrapped_in_markers_and_says_merge_commit():
    body = _render([_pr(1, "x")])
    assert body.startswith(fp.START) and body.endswith(fp.END)
    assert "Create a merge commit" in body
    assert "compare/main...feat/agent-fleet" in body
    assert "`b23d879`" in body


def test_direct_commits_are_listed():
    body = _render([], [fp.DirectCommit("1234567abc", "hotfix: thing")])
    assert "- `1234567` hotfix: thing" in body


def test_oversized_body_is_truncated_visibly():
    prs = [_pr(n, "x" * 200) for n in range(500)]
    body = _render(prs, max_len=5_000)
    assert len(body) <= 5_000
    assert "more lines not shown" in body
    assert body.endswith(fp.END)


# ─────────────────────────────────────────────────────────────────────
# Body merge
# ─────────────────────────────────────────────────────────────────────


def test_marked_block_is_replaced_and_notes_kept():
    old = (
        f"Wilson: hold until Friday.\n\n{fp.START}\nstale-list\n{fp.END}\n\nmore notes"
    )
    new = fp.merge_body(old, f"{fp.START}\nnew\n{fp.END}")
    assert "stale-list" not in new
    assert new.startswith("Wilson: hold until Friday.")
    assert new.endswith("more notes")
    assert new.count(fp.START) == 1


def test_block_appended_when_markers_missing():
    assert fp.merge_body("hand-written", "S") == "hand-written\n\nS"
    assert fp.merge_body(None, "S") == "S"


# ─────────────────────────────────────────────────────────────────────
# Open promote PR selection
# ─────────────────────────────────────────────────────────────────────


def test_fleet_headed_pr_is_managed():
    prs = [{"headRefName": "devin/hotfix"}, {"headRefName": "feat/agent-fleet"}]
    assert fp.select_open_promote(prs) == (prs[1], "auto")


def test_frozen_promote_takes_over_from_the_live_pr():
    auto = {"headRefName": "feat/agent-fleet", "labels": [{"name": "promote"}]}
    frozen = {"headRefName": "promote/release", "labels": []}
    assert fp.select_open_promote([auto, frozen]) == (frozen, "manual")
    assert fp.find_auto_promote([auto, frozen]) is auto


def test_hand_cut_promote_pr_blocks_a_duplicate():
    by_branch = {"headRefName": "promote/afx-80", "labels": []}
    by_label = {"headRefName": "wilson/x", "labels": [{"name": "promote"}]}
    assert fp.select_open_promote([by_branch]) == (by_branch, "manual")
    assert fp.select_open_promote([by_label]) == (by_label, "manual")


def test_unrelated_pr_into_main_is_ignored():
    assert fp.select_open_promote([{"headRefName": "devin/hotfix", "labels": []}]) == (
        None,
        "",
    )


def test_title_uses_date():
    assert fp.promote_title(_NOW) == "Promote feat/agent-fleet → main: 2026-11-11"


# ─────────────────────────────────────────────────────────────────────
# Linear comment
# ─────────────────────────────────────────────────────────────────────


def test_linear_comment_links_promote_pr_and_carries_marker():
    body = fp.linear_comment(
        _pr(101, "AFX-8: x"),
        promote_url="https://github.com/widarecu/agentfx/pull/110",
        promote_number=110,
    )
    assert "pull/110" in body and "#101" in body
    assert fp.linear_marker(110) in body


def test_linear_comment_posted_once_per_promote_pr():
    calls: list[dict] = []
    linear = fp.Linear("key")
    linear._issues["AFX-8"] = {"id": "uuid", "labels": {"nodes": []}}

    def gql(q, v):
        calls.append(v)
        if "commentCreate" in q:
            return {}
        return {
            "issue": {"comments": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        }

    linear._gql = gql
    assert linear.comment_once("AFX-8", "hi <!-- m -->", "<!-- m -->") is True
    assert linear.comment_once("AFX-8", "hi <!-- m -->", "<!-- m -->") is False
    assert [c["id"] for c in calls] == ["uuid", "uuid"]  # one page read, one post


def test_linear_marker_found_on_a_later_comment_page():
    pages = {
        None: {
            "nodes": [{"body": "x"}] * 100,
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
        },
        "c1": {
            "nodes": [{"body": "old <!-- m -->"}],
            "pageInfo": {"hasNextPage": False},
        },
    }
    posted: list[dict] = []
    linear = fp.Linear("key")
    linear._issues["AFX-8"] = {"id": "uuid", "labels": {"nodes": []}}

    def gql(q, v):
        if "commentCreate" in q:
            posted.append(v)
            return {}
        return {"issue": {"comments": pages[v["after"]]}}

    linear._gql = gql
    assert linear.comment_once("AFX-8", "hi <!-- m -->", "<!-- m -->") is False
    assert posted == []


def test_linear_risk_reads_risk_label():
    linear = fp.Linear("key")
    linear._issues["AFX-9"] = {
        "id": "u",
        "labels": {"nodes": [{"name": "Feature"}, {"name": "Money-path"}]},
    }
    assert linear.risk("AFX-9") == "Money-path"


# ─────────────────────────────────────────────────────────────────────
# Is there anything to promote? (real git repo)
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
    (tmp_path / "a").write_text("1\n")
    _git(tmp_path, "add", "a")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "branch", "feat/agent-fleet")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, branch: str, name: str, text: str) -> None:
    _git(repo, "checkout", "-q", branch)
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", name)


def _publish(repo: Path) -> None:
    for b in ("main", "feat/agent-fleet"):
        _git(repo, "update-ref", f"refs/remotes/origin/{b}", b)


def test_identical_branches_have_nothing_to_promote(repo):
    _publish(repo)
    assert fp.fleet_has_new_content() is False


def test_agent_work_on_fleet_is_promotable(repo):
    _commit(repo, "feat/agent-fleet", "b", "agent\n")
    _publish(repo)
    assert fp.fleet_has_new_content() is True


def test_hotfix_on_main_alone_is_not_promotable(repo):
    _commit(repo, "main", "hotfix", "fix\n")
    _publish(repo)
    assert fp.fleet_has_new_content() is False


def test_hotfix_after_a_back_merge_is_not_promotable(repo):
    _commit(repo, "feat/agent-fleet", "b", "agent\n")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "promote", "feat/agent-fleet")
    _git(repo, "checkout", "-q", "feat/agent-fleet")
    _git(repo, "merge", "-q", "--no-ff", "-m", "back-merge", "main")
    _commit(repo, "main", "hotfix", "fix\n")
    _publish(repo)
    assert fp.fleet_has_new_content() is False


def test_conflicting_fleet_work_is_promotable(repo):
    _commit(repo, "main", "a", "main\n")
    _commit(repo, "feat/agent-fleet", "a", "fleet\n")
    _publish(repo)
    assert fp.fleet_has_new_content() is True
