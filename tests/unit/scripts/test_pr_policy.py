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
        self.ci: dict[str, str] = {}

    def add_pr(self, number: int = 7, **kw) -> dict:
        pr = {
            "number": number,
            "state": "open",
            "draft": False,
            "title": "Fix the thing",
            "base": {"ref": "main"},
            "auto_merge": None,
            "labels": [{"name": "agent-authored"}],
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

    def review(
        self,
        number: int,
        login: str,
        state: str,
        sha: str = HEAD,
        kind: str = "User",
    ) -> None:
        self.reviews[number].append(
            {"user": {"login": login, "type": kind}, "state": state, "commit_id": sha}
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
        elif m := re.fullmatch(
            r"/commits/([^/]+)/check-runs\?check_name=CI%20required&filter=latest",
            path,
        ):
            conclusion = self.ci.get(m[1], "success")
            runs = [] if conclusion == "missing" else [{"conclusion": conclusion}]
            body = {"check_runs": runs}
        elif m := re.fullmatch(r"/commits/([^/]+)/pulls", path):
            body = [
                p
                for p in self.pulls.values()
                if m[1] == p["head"]["sha"] or m[1] in p.get("commits", ())
            ]
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
            {
                "user": {"login": "widarecu", "type": "User"},
                "state": "APPROVED",
                "commit_id": HEAD,
            }
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    def test_login_match_is_case_insensitive(self):
        reviews = [
            {
                "user": {"login": "Widarecu", "type": "User"},
                "state": "APPROVED",
                "commit_id": HEAD,
            }
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    def test_comment_after_approval_keeps_it(self):
        reviews = [
            {
                "user": {"login": "widarecu", "type": "User"},
                "state": "APPROVED",
                "commit_id": HEAD,
            },
            {
                "user": {"login": "widarecu", "type": "User"},
                "state": "COMMENTED",
                "commit_id": HEAD,
            },
        ]
        assert pp.approved_by(reviews, "widarecu", HEAD)

    @pytest.mark.parametrize(
        "reviews",
        [
            [],
            [
                {
                    "user": {"login": "someone", "type": "User"},
                    "state": "APPROVED",
                    "commit_id": HEAD,
                }
            ],
            [
                {
                    "user": {"login": "widarecu", "type": "User"},
                    "state": "APPROVED",
                    "commit_id": OLD,
                }
            ],
            [
                {
                    "user": {"login": "widarecu", "type": "User"},
                    "state": "DISMISSED",
                    "commit_id": HEAD,
                }
            ],
            [
                {
                    "user": {"login": "widarecu", "type": "User"},
                    "state": "APPROVED",
                    "commit_id": HEAD,
                },
                {
                    "user": {"login": "widarecu", "type": "User"},
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

    def test_bot_account_never_counts(self):
        reviews = [
            {
                "user": {"login": "widarecu", "type": "Bot"},
                "state": "APPROVED",
                "commit_id": HEAD,
            }
        ]
        assert not pp.approved_by(reviews, "widarecu", HEAD)

    def test_review_without_a_type_does_not_count(self):
        reviews = [
            {"user": {"login": "widarecu"}, "state": "APPROVED", "commit_id": HEAD}
        ]
        assert not pp.approved_by(reviews, "widarecu", HEAD)


class TestApproverResolution:
    @staticmethod
    def _owners(tmp_path, text):
        path = tmp_path / "CODEOWNERS"
        path.write_text(text)
        return path

    def test_repo_codeowners_names_the_default(self):
        assert pp.codeowners_approver() == pp.DEFAULT_APPROVER

    def test_owner_from_codeowners(self, tmp_path):
        path = self._owners(tmp_path, "/scripts/pr_policy.py   @someone  # note\n")
        assert pp.resolve_approver(None, path) == "someone"

    def test_override_wins(self, tmp_path):
        path = self._owners(tmp_path, "/scripts/pr_policy.py @someone\n")
        assert pp.resolve_approver("other", path) == "other"

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "/scripts/afx.py @someone\n",
            "/scripts/pr_policy.py\n",
            "/scripts/pr_policy.py @a @b\n",
            "/scripts/pr_policy.py @org/team\n",
            "/scripts/pr_policy.py someone\n",
            "\x00\xff not text",
        ],
        ids=[
            "missing",
            "empty",
            "no-entry",
            "no-owner",
            "two-owners",
            "team",
            "no-at-sign",
            "garbage",
        ],
    )
    def test_falls_back_to_default(self, tmp_path, text):
        path = tmp_path / "CODEOWNERS"
        if text is not None:
            path.write_bytes(text.encode("latin-1"))
        assert pp.resolve_approver(None, path) == pp.DEFAULT_APPROVER


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
        assert _outputs(out) == {
            "eligible": "true",
            "pr": "7",
            "sha": HEAD,
            "disable": "",
        }

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
        assert _outputs(out) == {
            "eligible": "false",
            "pr": "",
            "sha": "",
            "disable": "",
        }

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
        assert _outputs(out) == {
            "eligible": "true",
            "pr": "7",
            "sha": HEAD,
            "disable": "",
        }

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

    def test_bot_approval_does_not_count(self, gh, lin):
        gh.review(7, "widarecu", "APPROVED", kind="Bot")
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED

    def test_agent_authored_pr_approved_by_the_approver_passes(self, gh, lin, tmp_path):
        gh.pulls[7]["user"] = {"login": "devin-ai-integration[bot]", "type": "Bot"}
        gh.review(7, "widarecu", "APPROVED")
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["eligible"] == "true"

    @pytest.mark.parametrize("author", ["widarecu", "Widarecu"])
    def test_author_is_approver_must_merge_by_hand(self, gh, lin, tmp_path, author):
        gh.pulls[7]["user"] = {"login": author, "type": "User"}
        gh.review(7, "widarecu", "APPROVED")
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_FAILED
        assert gh.last["state"] == "failure"
        assert "by hand" in gh.last["description"]
        assert "authored" in gh.last["description"]
        assert len(gh.last["description"]) <= 140
        assert _outputs(out)["eligible"] == "false"

    def test_failing_decision_is_never_auto_merge_eligible(self, gh):
        pr = gh.add_pr(user={"login": "widarecu", "type": "User"})
        decision = pp.Decision(False, "by hand", "AFX-1", money_path=True)
        assert not pp.auto_merge_eligible(pr, decision, REPO)
        action = pp.merge_action(pr, decision, REPO, ci_passed=True)
        assert not action.enable

    def test_approver_is_configurable(self, gh, lin):
        gh.review(7, "someone", "APPROVED")
        assert _run(gh, lin, "--pr", "7", "--approver", "someone") == pp.EXIT_OK

    def test_review_rerun_by_head_sha(self, gh, lin, tmp_path):
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_FAILED
        gh.review(7, "widarecu", "APPROVED")
        out = tmp_path / "out"
        assert _run(gh, lin, "--head-sha", HEAD, output=out) == pp.EXIT_OK
        assert [s["state"] for _, s in gh.statuses] == ["failure", "success"]
        assert _outputs(out) == {
            "eligible": "true",
            "pr": "7",
            "sha": HEAD,
            "disable": "",
        }

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

    @pytest.mark.parametrize("labels", [[], [{"name": "needs-wilson"}]])
    def test_handoff_or_human_pr_is_not_auto_merged(self, gh, lin, labels, tmp_path):
        """AFX-125: a `[NEEDS DESIGN]` PR names an active ticket and passes
        the check, but must not merge and let deploy sync close the ticket."""
        lin.add("AFX-1", "In Progress")
        gh.add_pr(title="[NEEDS DESIGN] AFX-1: which way?", labels=labels)
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
        ],
        ids=["fleet-pr", "closed"],
    )
    def test_out_of_scope_prs_are_left_alone(self, gh, lin, pr, tmp_path):
        gh.add_pr(**pr)
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert gh.statuses == []
        assert lin.calls == []
        assert _outputs(out)["eligible"] == "false"

    @pytest.mark.parametrize("head", ["feat/agent-fleet", "promote/2026-11-23"])
    def test_former_promote_heads_are_checked(self, gh, lin, head):
        """AFX-125: the promote exemption is gone with the fleet branch."""
        gh.add_pr(head={"ref": head})
        _run(gh, lin, "--pr", "7")
        assert gh.statuses

    def test_unknown_sha_is_a_no_op(self, gh, lin):
        assert _run(gh, lin, "--head-sha", "nope") == pp.EXIT_OK
        assert gh.statuses == []

    def test_repeated_event_is_idempotent(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr()
        assert _run(gh, lin, "--pr", "7") == _run(gh, lin, "--pr", "7") == pp.EXIT_OK
        assert gh.statuses[0] == gh.statuses[1]


class TestMergeTiming:
    """AFX-130: auto-merge follows a fresh pass on a green, pinned head."""

    @pytest.fixture(autouse=True)
    def _ticket(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr()

    @pytest.mark.parametrize("ci", ["failure", "missing", "in_progress"])
    def test_no_auto_merge_before_ci_passes(self, gh, lin, ci, tmp_path):
        gh.ci[HEAD] = ci
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert gh.last["state"] == "success"
        assert _outputs(out)["eligible"] == "false"

    def test_ci_completion_rerun_enables_pinned_to_head(self, gh, lin, tmp_path):
        out = tmp_path / "out"
        assert _run(gh, lin, "--head-sha", HEAD, output=out) == pp.EXIT_OK
        assert _outputs(out)["pr"] == "7" and _outputs(out)["sha"] == HEAD

    def test_failed_check_disables_auto_merge(self, gh, lin, tmp_path):
        gh.pulls[7]["auto_merge"] = {"merge_method": "squash"}
        gh.pulls[7]["title"] = "AFX-1 and AFX-2"
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_FAILED
        assert _outputs(out)["disable"] == "7"
        assert _outputs(out)["eligible"] == "false"

    @pytest.mark.parametrize("state", ["Blocked", "Ready", "Backlog"])
    def test_ticket_moved_after_a_pass_stops_the_merge(self, gh, lin, state, tmp_path):
        gh.ci[HEAD] = "in_progress"
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_OK
        lin.issues["AFX-1"].state = state
        gh.ci[HEAD] = "success"
        out = tmp_path / "out"
        assert _run(gh, lin, "--head-sha", HEAD, output=out) == pp.EXIT_FAILED
        assert gh.last["state"] == "failure"
        assert _outputs(out)["eligible"] == "false"

    def test_auto_merge_enabled_early_by_someone_else_is_turned_off(
        self, gh, lin, tmp_path
    ):
        gh.ci[HEAD] = "in_progress"
        gh.pulls[7]["auto_merge"] = {"merge_method": "merge"}
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["disable"] == "7"

    def test_already_enabled_after_green_is_left_alone(self, gh, lin, tmp_path):
        gh.pulls[7]["auto_merge"] = {"merge_method": "squash"}
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["eligible"] == "false"
        assert _outputs(out)["disable"] == ""

    def test_no_auto_merge_while_a_review_is_requested(self, gh, lin, tmp_path):
        gh.pulls[7]["requested_reviewers"] = [{"login": "widarecu"}]
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["eligible"] == "false"
        gh.pulls[7]["requested_reviewers"] = []
        lin.issues["AFX-1"].state = "Blocked"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_FAILED
        assert _outputs(out)["eligible"] == "false"
        lin.issues["AFX-1"].state = "In Review"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_OK
        assert _outputs(out)["pr"] == "7"

    def test_ci_not_queried_when_policy_fails(self, gh, lin):
        lin.issues["AFX-1"].state = "Blocked"
        _run(gh, lin, "--pr", "7")
        assert not any("check-runs" in path for _, path in gh.calls)


class TestSharedHead:
    def test_shared_head_fails_every_pr(self, gh, lin, tmp_path):
        lin.add("AFX-1", "In Progress")
        lin.add("AFX-2", "In Progress")
        gh.add_pr(7)
        gh.add_pr(8, head={"ref": "agent/AFX-2-other"})
        gh.pulls[8]["auto_merge"] = {"merge_method": "squash"}
        out = tmp_path / "out"
        assert _run(gh, lin, "--head-sha", HEAD, output=out) == pp.EXIT_FAILED
        assert [s["state"] for _, s in gh.statuses] == ["failure", "failure"]
        assert "also the head of #8" in gh.statuses[0][1]["description"]
        assert _outputs(out)["eligible"] == "false"
        assert _outputs(out)["disable"] == "8"

    def test_pr_stacked_on_this_head_is_not_a_shared_head(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        lin.add("AFX-2", "In Progress")
        gh.add_pr(7)
        gh.add_pr(8, head={"ref": "agent/AFX-2-next", "sha": "f" * 40})
        gh.pulls[8]["commits"] = [HEAD]
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_OK
        assert gh.last["state"] == "success"
        assert _run(gh, lin, "--head-sha", HEAD) == pp.EXIT_OK
        assert [sha for sha, _ in gh.statuses] == [HEAD, HEAD]


class TestFailsClosed:
    def test_linear_down_sets_error_status(self, gh, lin, tmp_path):
        lin.fail_with = urllib.error.URLError("down")
        gh.add_pr()
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_ERROR
        assert gh.last["state"] == "error"
        assert _outputs(out) == {
            "eligible": "false",
            "pr": "",
            "sha": "",
            "disable": "",
        }

    def test_github_down_exits_error(self, gh, lin):
        gh.add_pr()
        gh.fail_on = "/pulls/7"
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_ERROR

    def test_error_disables_armed_auto_merge(self, gh, lin, tmp_path):
        lin.fail_with = urllib.error.URLError("down")
        gh.add_pr()
        gh.pulls[7]["auto_merge"] = {"merge_method": "squash"}
        out = tmp_path / "out"
        assert _run(gh, lin, "--pr", "7", output=out) == pp.EXIT_ERROR
        assert gh.last["state"] == "error"
        assert _outputs(out)["disable"] == "7"
        assert _outputs(out)["eligible"] == "false"

    def test_ci_unreadable_sets_error_status(self, gh, lin):
        lin.add("AFX-1", "In Progress")
        gh.add_pr()
        gh.fail_on = "/check-runs"
        assert _run(gh, lin, "--pr", "7") == pp.EXIT_ERROR
        assert gh.last["state"] == "error"

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
