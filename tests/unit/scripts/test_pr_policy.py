"""`scripts/pr_policy.py` (AFX-120). No network: GitHub is `FakeGitHub`
below and Linear is the real `LinearClient` wired to `FakeLinear`."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeLinear

_REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "pr_policy", _REPO / "scripts" / "pr_policy.py"
)
assert _spec is not None and _spec.loader is not None
pp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pp
_spec.loader.exec_module(pp)

REPO = "widarecu/agentfx"
HEAD = "head-sha-2"
OLD = "head-sha-1"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGitHub:
    def __init__(self) -> None:
        self.pulls: dict[int, dict] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.statuses: list[tuple[str, dict]] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_on: str | None = None

    def add_pr(self, number: int = 7, **kw) -> dict:
        pr = {
            "number": number,
            "state": "open",
            "draft": False,
            "title": "Fix the thing",
            "base": {"ref": "main"},
            "head": {
                "ref": "agent/AFX-1-fix",
                "sha": HEAD,
                "repo": {"full_name": REPO},
            },
        }
        head = kw.pop("head", {})
        pr.update(kw)
        pr["head"].update(head)
        self.pulls[number] = pr
        self.reviews.setdefault(number, [])
        return pr

    def review(self, number: int, login: str, state: str, sha: str = HEAD) -> None:
        self.reviews[number].append(
            {"user": {"login": login}, "state": state, "commit_id": sha}
        )

    def opener(self, req, timeout=None):
        path = req.full_url.split(f"/repos/{REPO}", 1)[1]
        method = req.get_method()
        self.calls.append((method, path))
        if self.fail_on and self.fail_on in path:
            raise urllib.error.HTTPError(req.full_url, 502, "bad", {}, io.BytesIO())
        if m := re.fullmatch(r"/pulls/(\d+)/reviews\?per_page=100&page=(\d+)", path):
            items = self.reviews[int(m[1])]
            page = int(m[2])
            body = items[(page - 1) * 100 : page * 100]
        elif m := re.fullmatch(r"/pulls/(\d+)", path):
            body = self.pulls[int(m[1])]
        elif m := re.fullmatch(r"/commits/([^/]+)/pulls", path):
            body = [p for p in self.pulls.values() if p["head"]["sha"] == m[1]]
        elif m := re.fullmatch(r"/statuses/([^/]+)", path):
            self.statuses.append((m[1], json.loads(req.data)))
            body = {}
        else:
            raise AssertionError(f"unexpected call {method} {path}")
        return _Resp(json.dumps(body).encode())

    @property
    def last(self) -> dict:
        return self.statuses[-1][1]


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def lin() -> FakeLinear:
    return FakeLinear(labels=["needs-wilson", "agent-filed", "Money-path"])


def _run(gh: FakeGitHub, lin: FakeLinear, *argv: str, output: Path | None = None):
    args = ["--repo", REPO, *argv]
    if output is not None:
        args += ["--output", str(output)]
    return pp.main(
        args,
        github=pp.GitHub("t", REPO, opener=gh.opener),
        linear=ld.LinearClient("k", opener=lin.opener),
    )


def _outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


class TestTicketIds:
    @pytest.mark.parametrize(
        ("title", "branch", "expected"),
        [
            ("Fix it", "agent/AFX-12-fix", ["AFX-12"]),
            ("AFX-12: fix it", "agent/AFX-12-fix", ["AFX-12"]),
            ("afx-012 fix", "wilson/AFX-12", ["AFX-12"]),
            ("AFX-12 and AFX-12 again", "", ["AFX-12"]),
            ("AFX-12", "agent/AFX-13-x", ["AFX-12", "AFX-13"]),
            ("No ticket", "feature/thing", []),
            ("XAFX-12 fix", "fix", []),
            ("AFX-123", "agent/AFX-123-x", ["AFX-123"]),
        ],
    )
    def test_distinct_ids(self, title, branch, expected):
        assert pp.ticket_ids(title, branch) == expected


class TestApproval:
    def test_latest_verdict_must_approve_head(self):
        reviews = [
            {"user": {"login": "widarecu"}, "state": "APPROVED", "commit_id": HEAD}
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    def test_login_match_is_case_insensitive(self):
        reviews = [
            {"user": {"login": "Widarecu"}, "state": "APPROVED", "commit_id": HEAD}
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    def test_comment_after_approval_keeps_it(self):
        reviews = [
            {"user": {"login": "widarecu"}, "state": "APPROVED", "commit_id": HEAD},
            {"user": {"login": "widarecu"}, "state": "COMMENTED", "commit_id": HEAD},
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    @pytest.mark.parametrize(
        "reviews",
        [
            [],
            [{"user": {"login": "someone"}, "state": "APPROVED", "commit_id": HEAD}],
            [{"user": {"login": "widarecu"}, "state": "APPROVED", "commit_id": OLD}],
            [{"user": {"login": "widarecu"}, "state": "DISMISSED", "commit_id": HEAD}],
            [
                {"user": {"login": "widarecu"}, "state": "APPROVED", "commit_id": HEAD},
                {
                    "user": {"login": "widarecu"},
                    "state": "CHANGES_REQUESTED",
                    "commit_id": HEAD,
                },
            ],
            [{"user": None, "state": "APPROVED", "commit_id": HEAD}],
        ],
        ids=[
            "none",
            "other-reviewer",
            "stale",
            "dismissed",
            "changes-requested",
            "ghost",
        ],
    )
    def test_not_approved(self, reviews):
        assert not pp.approved_by(reviews, "widarecu", HEAD)


class TestPolicy:
    @pytest.mark.parametrize("state", ["In Progress", "In Review"])
    def test_active_ticket_passes(self, gh, lin, state, tmp_path):
        lin.add("AFX-1", state)
        gh.add_pr()
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert gh.statuses == [
            (
                HEAD,
                {
                    "state": "success",
                    "context": "pr-policy",
                    "description": f"AFX-1 is {state}",
                },
            )
        ]
        assert _outputs(out) == {"eligible": "true", "pr": "7"}

    @pytest.mark.parametrize(
        "state",
        ["Triage", "Backlog", "Ready", "Blocked", "Done", "Canceled", "In Staging"],
    )
    def test_inactive_ticket_fails(self, gh, lin, state, tmp_path):
        lin.add("AFX-1", state)
        gh.add_pr()
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_FAILED
        assert gh.last["state"] == "failure"
        assert state in gh.last["description"]
        assert _outputs(out) == {"eligible": "false", "pr": ""}

    def test_missing_id_fails(self, gh, lin):
        gh.add_pr(title="Tidy", head={"ref": "agent/tidy"})
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED
        assert "no AFX ticket ID" in gh.last["description"]
        assert lin.calls == []

    def test_mismatched_ids_fail(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        lin.add("AFX-2", "In Progress")
        gh.add_pr(title="AFX-2: fix")
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED
        assert gh.last["description"] == "more than one ticket ID: AFX-1, AFX-2"

    def test_same_id_in_title_and_branch_is_one(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr(title="AFX-1: fix (afx-1)")
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_OK

    def test_unknown_ticket_fails(self, gh, lin):
        gh.add_pr()
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED
        assert gh.last["description"] == "AFX-1 not found in Linear"

    def test_no_linear_writes(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr()
        _run(gh, lin, "--pr", "7")
        assert lin.mutations == []
        assert not any(q.startswith("mutation") for q in lin.calls)


class TestMoneyPath:
    @pytest.fixture(autouse=True)
    def _money(self, gh, lin):
        lin.add("AFX-1", "In Review", labels=["Money-path"])
        gh.add_pr()

    def test_red_without_approval(self, gh, lin, tmp_path):
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_FAILED
        assert "Money-path" in gh.last["description"]
        assert "@widarecu" in gh.last["description"]
        assert _outputs(out)["eligible"] == "false"

    def test_green_with_current_approval(self, gh, lin, tmp_path):
        gh.review(7, "widarecu", "APPROVED")
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert gh.last["state"] == "success"
        assert _outputs(out) == {"eligible": "true", "pr": "7"}

    @pytest.mark.parametrize(
        ("login", "state", "sha"),
        [
            ("devin-ai-integration[bot]", "APPROVED", HEAD),
            ("widarecu", "APPROVED", OLD),
            ("widarecu", "DISMISSED", HEAD),
        ],
        ids=["other-reviewer", "stale", "dismissed"],
    )
    def test_red_with_invalid_approval(self, gh, lin, login, state, sha):
        gh.review(7, login, state, sha)
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED

    def test_approver_is_configurable(self, gh, lin):
        gh.review(7, "someone", "APPROVED")
        assert _run(gh, lin, "--pr", "7", "--approver", "someone") == pp.EXIT_OK

    def test_review_rerun_by_head_sha(self, gh, lin, tmp_path):
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED
        gh.review(7, "widarecu", "APPROVED")
        out = tmp_path / "out"
        assert _run(gh, lin, "--head-sha", HEAD, output=out) == pp.EXIT_OK
        assert [s["state"] for _, s in gh.statuses] == ["failure", "success"]
        assert _outputs(out) == {"eligible": "true", "pr": "7"}

    def test_reviews_are_paginated(self, gh, lin):
        for _ in range(100):
            gh.review(7, "someone", "COMMENTED")
        gh.review(7, "widarecu", "APPROVED")
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_OK
        assert ("GET", "/pulls/7/reviews?per_page=100&page=2") in gh.calls

    def test_non_money_path_ignores_reviews(self, gh, lin):
        lin.issues["AFX-1"].labels = []
        gh.review(7, "widarecu", "CHANGES_REQUESTED")
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_OK


class TestEligibilityAndScope:
    def test_draft_passes_but_is_not_auto_merged(self, gh, lin, tmp_path):
        lin.add("AFX-1", "In Progress")
        gh.add_pr(draft=True)
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["eligible"] == "false"

    def test_fork_is_not_auto_merged(self, gh, lin, tmp_path):
        lin.add("AFX-1", "In Progress")
        gh.add_pr(head={"repo": {"full_name": "someone/agentfx"}})
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["eligible"] == "false"

    @pytest.mark.parametrize(
        "pr",
        [
            {"base": {"ref": "feat/agent-fleet"}},
            {"state": "closed"},
            {"head": {"ref": "feat/agent-fleet"}},
            {"head": {"ref": "promote/2026-11-23"}},
        ],
        ids=["fleet-pr", "closed", "promote-pr", "frozen-promote"],
    )
    def test_out_of_scope_prs_are_left_alone(self, gh, lin, pr, tmp_path):
        gh.add_pr(**pr)
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert gh.statuses == []
        assert lin.calls == []
        assert _outputs(out)["eligible"] == "false"

    def test_unknown_sha_is_a_no_op(self, gh, lin):
        assert _run(gh, lin, "--head-sha", "nope") == pp.EXIT_OK
        assert gh.statuses == []

    def test_repeated_event_is_idempotent(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr()
        assert _run(gh, lin, "--pr", "7") == _run(gh, lin, "--pr", "7") == pp.EXIT_OK
        assert gh.statuses[0] == gh.statuses[1]


class TestFailsClosed:
    def test_linear_down_sets_error_status(self, gh, lin, tmp_path):
        lin.fail_with = urllib.error.URLError("down")
        gh.add_pr()
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_ERROR
        assert gh.last["state"] == "error"
        assert _outputs(out) == {"eligible": "false", "pr": ""}

    def test_github_down_exits_error(self, gh, lin):
        gh.add_pr()
        gh.fail_on = "/pulls/7"
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_ERROR

    def test_reviews_unreadable_sets_error_status(self, gh, lin):
        lin.add("AFX-1", "In Progress", labels=["Money-path"])
        gh.add_pr()
        gh.fail_on = "/reviews"
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_ERROR
        assert gh.last["state"] == "error"

    def test_missing_tokens_exit_error(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        assert pp.main(["--repo", REPO, "--pr", "7"]) == pp.EXIT_ERROR

    def test_missing_repo_exits_error(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        assert pp.main(["--pr", "7"]) == pp.EXIT_ERROR

    def test_status_description_is_truncated(self, gh):
        client = pp.GitHub("t", REPO, opener=gh.opener)
        client.set_status(HEAD, "failure", "x" * 500)
        assert len(gh.last["description"]) == 140
