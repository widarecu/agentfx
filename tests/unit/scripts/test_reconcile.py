"""`afx reconcile` (AFX-123): missed deploy-sync replay plus one drift
digest. Linear is the real `LinearClient` on `FakeLinear`; GitHub is the
deploy-sync fake plus PR states. Nothing talks to a real API."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentfx.integrations import delivery_reconcile as dr
from agentfx.integrations import deploy_sync as ds
from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeLinear
from tests.unit.scripts.test_deploy_sync import FakeGitHub as SyncGitHub
from tests.unit.scripts.test_deploy_sync import _pr

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "afx.py"
_spec = importlib.util.spec_from_file_location("afx_cli_reconcile", _SCRIPT)
assert _spec is not None and _spec.loader is not None
afx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = afx
_spec.loader.exec_module(afx)

REPO = "widarecu/agentfx"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
OLD = "2026-11-10T00:00:00.000Z"
RECENT = "2026-11-22T09:00:00.000Z"
WILSON = dr.DEFAULT_WILSON_USER_ID
BASE = "b" * 40
SHA = "a" * 40
RUN = "https://github.com/widarecu/agentfx/actions/runs/7"


def _url(n: int) -> str:
    return f"https://github.com/{REPO}/pull/{n}"


class FakeGitHub(SyncGitHub):
    def __init__(self, *, pr_states=None, **kw):
        super().__init__(**kw)
        self.pr_states: dict[int, str] = dict(pr_states or {})
        self.merged: list[dict] = []
        self.open: list[dict] = []
        self.statuses: dict[str, list[dict]] = {}

    def open_pulls(self):
        self._check()
        return list(self.open)

    def commit_statuses(self, sha):
        self._check()
        return list(self.statuses.get(sha, []))

    def merged_pulls(self, since):
        self._check()
        return list(self.merged)

    def pull_state(self, number):
        self._check()
        if number not in self.pr_states:
            raise ds.GitHubError(f"PR #{number} not found", 404)
        return self.pr_states[number]


def _move(
    to,
    *,
    frm="Triage",
    at=RECENT,
    email="pe@bot",
    name="PE bot",
    user_id=None,
    bot=None,
):
    actor = (
        None
        if email is None and user_id is None
        else {"id": user_id or f"u-{email}", "name": name, "email": email}
    )
    return {
        "createdAt": at,
        "actor": actor,
        "botActor": bot,
        "fromState": {"name": frm},
        "toState": {"name": to},
    }


@pytest.fixture
def lin() -> FakeLinear:
    return FakeLinear()


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


def _run(gh, lin, *, apply=False, previous=None, window_days=7):
    return dr.reconcile(
        gh,
        ld.LinearClient("k", opener=lin.opener),
        repo=REPO,
        now=NOW,
        window_days=window_days,
        wilson_user_id=WILSON,
        run_url=RUN,
        apply=apply,
        previous=previous,
    )


def _kinds(result) -> list[tuple[str, str]]:
    return [(f.kind, f.ident) for f in result.findings]


class TestNoDrift:
    def test_empty_board_is_one_line(self, gh, lin):
        result = _run(gh, lin)
        assert result.findings == []
        assert result.exit_code == 0
        assert "No delivery drift since 2026-11-16T12:00:00Z." in result.summary
        assert "## Delivery drift digest" not in result.summary
        assert result.state["count"] == 0
        assert lin.mutations == []

    def test_healthy_tickets_raise_nothing(self, gh, lin):
        gh.pr_states = {9: "open"}
        lin.add("AFX-1", "In Progress", started_at=OLD, attachments=[_url(8)])
        lin.add("AFX-2", "In Progress", started_at=RECENT)
        lin.add("AFX-3", "In Review", attachments=[_url(9)])
        lin.add(
            "AFX-4",
            "Ready",
            updated_at=RECENT,
            history=[_move("Ready", user_id=WILSON, email=None, name="Wilson")],
        )
        assert _run(gh, lin).findings == []


class TestDriftClasses:
    def test_stale_in_progress_without_pr(self, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.STALE, "AFX-1")]
        assert "13 days" in result.findings[0].detail

    def test_pr_from_another_repo_does_not_count(self, gh, lin):
        lin.add(
            "AFX-1",
            "In Progress",
            started_at=OLD,
            attachments=["https://github.com/other/repo/pull/3"],
        )
        assert _kinds(_run(gh, lin)) == [(dr.STALE, "AFX-1")]

    @pytest.mark.parametrize("state", ["closed", "merged"])
    def test_in_review_with_no_open_pr(self, gh, lin, state):
        gh.pr_states = {9: state}
        lin.add("AFX-3", "In Review", attachments=[_url(9)])
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.REVIEW, "AFX-3")]
        assert result.findings[0].detail == f"#9 {state}"

    def test_in_review_with_one_open_pr_is_fine(self, gh, lin):
        gh.pr_states = {9: "closed", 10: "open"}
        lin.add("AFX-3", "In Review", attachments=[_url(9), _url(10)])
        assert _run(gh, lin).findings == []

    def test_in_review_without_pr(self, gh, lin):
        lin.add("AFX-3", "In Review")
        assert _kinds(_run(gh, lin)) == [(dr.REVIEW, "AFX-3")]

    def test_done_without_deploy_marker(self, gh, lin):
        lin.add("AFX-5", "Done", completed_at=RECENT, updated_at=RECENT)
        assert _kinds(_run(gh, lin)) == [(dr.UNMARKED, "AFX-5")]

    @pytest.mark.parametrize(
        "marker",
        [ds.done_marker(SHA), "<!-- afx-70:promoted-to-main:abc1234 -->"],
        ids=["deploy-sync", "legacy-promote"],
    )
    def test_done_with_deploy_marker(self, gh, lin, marker):
        lin.add(
            "AFX-5",
            "Done",
            completed_at=RECENT,
            updated_at=RECENT,
            comments=[f"{marker}\nOn staging."],
        )
        assert _run(gh, lin).findings == []

    def test_done_before_window_is_ignored(self, gh, lin):
        lin.add("AFX-5", "Done", completed_at=OLD, updated_at=OLD)
        assert _run(gh, lin).findings == []

    def test_ready_set_by_someone_else(self, gh, lin):
        lin.add(
            "AFX-6", "Ready", updated_at=RECENT, history=[_move("Ready", frm="Backlog")]
        )
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.TRANSITION, "AFX-6")]
        assert "Backlog -> Ready by PE bot" in result.findings[0].detail

    def test_unknown_actor_is_flagged(self, gh, lin):
        lin.add(
            "AFX-6",
            "Backlog",
            updated_at=RECENT,
            history=[_move("Backlog", email=None)],
        )
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.TRANSITION, "AFX-6")]
        assert "unknown actor" in result.findings[0].detail

    def test_moves_before_window_and_other_states_ignored(self, gh, lin):
        lin.add(
            "AFX-6",
            "In Review",
            updated_at=RECENT,
            attachments=[_url(9)],
            history=[_move("Ready", at=OLD), _move("In Review")],
        )
        gh.pr_states = {9: "open"}
        assert _run(gh, lin).findings == []

    def test_history_unavailable_is_reported(self, gh, lin, monkeypatch):
        lin.add("AFX-6", "Ready", updated_at=RECENT)

        def boom(self, issue_id):
            raise ld.LinearError("linear http 503")

        monkeypatch.setattr(ld.LinearClient, "state_moves", boom)
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.UNCHECKED, "AFX-6")]
        assert "history unavailable" in result.findings[0].detail

    def test_pr_state_unavailable_is_reported(self, gh, lin):
        lin.add("AFX-3", "In Review", attachments=[_url(404)])
        assert _kinds(_run(gh, lin)) == [(dr.UNCHECKED, "AFX-3")]

    def test_every_page_of_issues_is_read(self, gh):
        lin = FakeLinear(page_size=2)
        for n in range(1, 6):
            lin.add(f"AFX-{n}", "In Progress", started_at=OLD)
        result = _run(gh, lin)
        assert [f.ident for f in result.findings] == [f"AFX-{n}" for n in range(1, 6)]
        assert sum("issues(" in q for q in lin.calls) == 3

    def test_drift_never_moves_statuses(self, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        lin.add("AFX-6", "Ready", updated_at=RECENT, history=[_move("Ready")])
        lin.add("AFX-5", "Done", completed_at=RECENT, updated_at=RECENT)
        _run(gh, lin, apply=True)
        assert lin.mutations == []
        assert [i.state for i in lin.issues.values()] == [
            "In Progress",
            "Ready",
            "Done",
        ]


def _merged(n, merged_by, *, author="agent-author", title=None):
    return {
        "number": n,
        "title": title or f"AFX-{n}: change",
        "author": author,
        "merged_by": merged_by,
        "merged_at": "2026-11-22T10:00:00Z",
    }


class TestByHandMerges:
    def test_only_non_bot_merges_are_listed(self, gh, lin):
        gh.merged = [
            _merged(20, "agentfx-fleet-bot[bot]"),
            _merged(21, "agentfx-fleet-bot"),
            _merged(22, "wilson", author="widarecu", title="AFX-22: by hand"),
        ]
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.BY_HAND, "PR #22")]
        detail = result.findings[0].detail
        assert "AFX-22: by hand" in detail
        assert "author widarecu" in detail
        assert "merged by wilson" in detail
        assert "## Delivery drift digest" in result.summary

    def test_same_by_hand_merge_is_not_repeated(self, gh, lin):
        gh.merged = [_merged(22, "wilson")]
        first = _run(gh, lin)
        second = _run(gh, lin, previous=first.state)
        assert "not repeated" in second.summary

    def test_unreadable_merge_data_is_unchecked(self, gh, lin):
        gh.fail = True
        result = _run(gh, lin)
        assert (dr.UNCHECKED, "merges") in _kinds(result)


def _open(n, *, mergeable=False, author="agentfx-pe[bot]", sha=None):
    return {
        "number": n,
        "title": f"AFX-{n}: change",
        "author": author,
        "head_sha": sha or f"sha{n}",
        "mergeable": mergeable,
    }


def _status(at, description, *, context="pr-policy", state="failure"):
    return {
        "context": context,
        "state": state,
        "description": description,
        "created_at": at,
    }


_CONFLICT = (
    "AFX-30 has merge conflicts with main: the PR's author merges main in "
    "and fixes them, then @widarecu approves the new commit"
)


class TestConflictedPulls:
    """AFX-162: open PRs into `main` in conflict for over a day, so a PR
    whose author's session is gone doesn't sit there unseen."""

    def test_conflict_over_a_day_is_listed_with_its_author(self, gh, lin):
        gh.open = [_open(30)]
        gh.statuses["sha30"] = [
            _status("2026-11-23T09:00:00Z", _CONFLICT),
            _status("2026-11-22T09:42:30Z", _CONFLICT),
            _status("2026-11-21T12:37:48Z", "AFX-30 is Money-path: needs approval"),
        ]
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.CONFLICTED, "PR #30")]
        detail = result.findings[0].detail
        assert "author agentfx-pe[bot]" in detail
        assert "AFX-30: change" in detail
        assert "since 2026-11-22T09:42:30Z" in detail
        assert "Open PR in conflict with main for over a day" in result.summary

    def test_conflict_under_a_day_is_not_listed(self, gh, lin):
        gh.open = [_open(30)]
        gh.statuses["sha30"] = [_status("2026-11-22T12:30:00Z", _CONFLICT)]
        assert _run(gh, lin).findings == []

    @pytest.mark.parametrize("mergeable", [True, None], ids=["clean", "unknown"])
    def test_clean_or_undecided_pr_is_not_listed(self, gh, lin, mergeable):
        gh.open = [_open(30, mergeable=mergeable)]
        gh.statuses["sha30"] = [_status("2026-11-20T09:00:00Z", _CONFLICT)]
        assert _run(gh, lin).findings == []

    def test_other_contexts_are_ignored_and_older_conflicts_do_not_count(
        self, gh, lin
    ):
        gh.open = [_open(30)]
        gh.statuses["sha30"] = [
            _status("2026-11-23T11:00:00Z", "deploy ok", context="other"),
            _status("2026-11-23T10:00:00Z", _CONFLICT),
            _status("2026-11-21T10:00:00Z", "AFX-30 is In Review", state="success"),
            _status("2026-11-20T10:00:00Z", _CONFLICT),
        ]
        assert _run(gh, lin).findings == []

    def test_conflict_pr_policy_has_not_reported_is_unchecked(self, gh, lin):
        gh.open = [_open(30)]
        gh.statuses["sha30"] = [_status("2026-11-20T10:00:00Z", "AFX-30 is In Review")]
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.UNCHECKED, "PR #30")]
        assert "not reported" in result.findings[0].detail

    def test_same_conflict_next_day_is_not_repeated(self, gh, lin):
        gh.open = [_open(30)]
        gh.statuses["sha30"] = [_status("2026-11-21T09:42:30Z", _CONFLICT)]
        first = _run(gh, lin)
        assert _kinds(first) == [(dr.CONFLICTED, "PR #30")]
        next_day = dr.reconcile(
            gh,
            ld.LinearClient("k", opener=lin.opener),
            repo=REPO,
            now=NOW + timedelta(days=1),
            wilson_user_id=WILSON,
            run_url=RUN,
            apply=False,
            previous=first.state,
        )
        assert "not repeated" in next_day.summary


class TestActors:
    def test_app_user_move_is_flagged_and_named(self, gh, lin):
        lin.add(
            "AFX-6",
            "Ready",
            updated_at=RECENT,
            history=[_move("Ready", email=None, bot={"id": "b1", "name": "Devin"})],
        )
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.TRANSITION, "AFX-6")]
        assert "by Devin" in result.findings[0].detail

    def test_app_user_actor_with_name_is_named(self, gh, lin):
        lin.add(
            "AFX-6",
            "Ready",
            updated_at=RECENT,
            history=[_move("Ready", email=None, name="AgentFX Fleet", user_id="app1")],
        )
        assert "by AgentFX Fleet" in _run(gh, lin).findings[0].detail

    def test_wilson_user_id_is_not_flagged_without_email(self, gh, lin):
        lin.add(
            "AFX-6",
            "Ready",
            updated_at=RECENT,
            history=[_move("Ready", user_id=WILSON, email=None, name="Wilson")],
        )
        assert _run(gh, lin).findings == []

    def test_other_human_is_flagged(self, gh, lin):
        lin.add(
            "AFX-6",
            "Ready",
            updated_at=RECENT,
            history=[_move("Ready", email="sam@example.com", name="Sam")],
        )
        result = _run(gh, lin)
        assert _kinds(result) == [(dr.TRANSITION, "AFX-6")]
        assert "by Sam" in result.findings[0].detail


class TestReopened:
    def test_replay_leaves_a_reopened_ticket_alone(self, lin):
        lin.add("AFX-9", "In Progress", comments=[ds.done_marker(SHA)])
        gh = FakeGitHub(
            tags={ds.STAGING_TAG: SHA, ds.record_tag(SHA): BASE},
            commits={(BASE, SHA): [SHA]},
            pulls={
                SHA: [_pr(50, title="AFX-9: fix", head="agent/AFX-9", merge_sha=SHA)]
            },
        )
        _run(gh, lin, apply=True)
        assert lin.issues["AFX-9"].state == "In Progress"
        assert lin.mutations == []


class TestMissedSync:
    def _missed(self, lin):
        lin.add("AFX-9", "In Staging")
        return FakeGitHub(
            tags={ds.STAGING_TAG: SHA, ds.record_tag(SHA): BASE},
            commits={(BASE, SHA): [SHA]},
            pulls={
                SHA: [_pr(50, title="AFX-9: fix", head="agent/AFX-9", merge_sha=SHA)]
            },
        )

    def test_apply_replays_the_sync(self, lin):
        gh = self._missed(lin)
        result = _run(gh, lin, apply=True)
        assert lin.issues["AFX-9"].state == "Done"
        assert any("AFX-9: In Staging -> Done" in line for line in result.sync_lines)
        assert "### Deploy sync replay" in result.summary

    def test_dry_run_changes_nothing(self, lin):
        gh = self._missed(lin)
        result = _run(gh, lin)
        assert lin.mutations == [] and gh.created == [] and gh.deleted == []
        assert lin.issues["AFX-9"].state == "In Staging"
        assert any("would move" in line for line in result.sync_lines)

    def test_repeated_apply_is_idempotent(self, lin):
        gh = self._missed(lin)
        _run(gh, lin, apply=True)
        writes = len(lin.mutations)
        _run(gh, lin, apply=True)
        assert len(lin.mutations) == writes

    def test_github_down_still_publishes_digest(self, gh, lin):
        gh.fail = True
        lin.add("AFX-1", "In Progress", started_at=OLD)
        result = _run(gh, lin)
        assert result.exit_code == 1
        assert _kinds(result) == [
            (dr.STALE, "AFX-1"),
            (dr.UNCHECKED, "merges"),
            (dr.UNCHECKED, "open PRs"),
        ]
        assert "GitHub API failed" in result.summary


class TestDedupe:
    def test_same_findings_are_not_repeated(self, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        first = _run(gh, lin)
        assert "## Delivery drift digest" in first.summary
        second = _run(gh, lin, previous={**first.state, "run_url": "prev-run"})
        assert "Same 1 finding(s) as the last digest (prev-run)" in second.summary
        assert "## Delivery drift digest" not in second.summary
        assert second.state["run_url"] == "prev-run"

    def test_new_findings_publish_a_new_digest(self, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        first = _run(gh, lin)
        lin.add("AFX-3", "In Review")
        second = _run(gh, lin, previous=first.state)
        assert "## Delivery drift digest" in second.summary
        assert second.state["fingerprint"] != first.state["fingerprint"]

    def test_a_ticket_ageing_a_day_is_not_new_drift(self, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        first = _run(gh, lin)
        next_day = dr.reconcile(
            gh,
            ld.LinearClient("k", opener=lin.opener),
            repo=REPO,
            now=NOW + timedelta(days=1),
            wilson_user_id=WILSON,
            run_url=RUN,
            apply=False,
            previous=first.state,
        )
        assert "14 days" in next_day.findings[0].detail
        assert "not repeated" in next_day.summary

    def test_changing_error_text_is_not_new_drift(self, gh, lin):
        lin.add("AFX-3", "In Review", attachments=[_url(404)])
        first = _run(gh, lin)
        gh.pr_states = {}
        lin.issues["AFX-3"].attachments = [_url(404)]
        a = dr.Finding(
            dr.UNCHECKED, "AFX-3", "PR #404 state unavailable: x", "pr-state #404"
        )
        assert first.findings[0].fingerprint_key == a.fingerprint_key
        lin.issues["AFX-3"].attachments = [_url(405)]
        assert _run(gh, lin).state["fingerprint"] != first.state["fingerprint"]

    def test_fingerprint_is_order_independent(self):
        a = dr.Finding(dr.STALE, "AFX-1", "x")
        b = dr.Finding(dr.REVIEW, "AFX-2", "y")
        assert dr.fingerprint([a, b]) == dr.fingerprint([b, a])

    def test_window_marker_is_in_every_summary(self, gh, lin):
        result = _run(gh, lin, window_days=1)
        assert result.summary.startswith(
            "<!-- afx-reconcile:window 2026-11-22T12:00:00Z..2026-11-23T12:00:00Z fp="
        )
        assert result.state["window"] == {
            "since": "2026-11-22T12:00:00Z",
            "until": "2026-11-23T12:00:00Z",
        }


class TestErrors:
    def test_linear_down_gives_no_digest(self, gh, lin):
        lin.fail_with = urllib.error.URLError("down")
        result = _run(gh, lin)
        assert result.exit_code == 1
        assert result.state == {}
        assert "Linear unavailable" in result.summary


class TestCli:
    def _args(self, tmp_path, *extra):
        return afx._parser().parse_args(
            [
                "reconcile",
                "--repo",
                REPO,
                "--previous",
                str(tmp_path / "missing.json"),
                "--state-out",
                str(tmp_path / "digest.json"),
                "--summary",
                str(tmp_path / "summary.md"),
                *extra,
            ]
        )

    def test_writes_summary_and_state(self, tmp_path, gh, lin, capsys):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        client = ld.LinearClient("k", opener=lin.opener)
        code = afx.reconcile(self._args(tmp_path), gh=gh, client=client, now=NOW)
        assert code == afx.EXIT_OK
        assert "AFX-1" in (tmp_path / "summary.md").read_text()
        state = json.loads((tmp_path / "digest.json").read_text())
        assert state["count"] == 1
        assert "dry-run" in capsys.readouterr().out

    def test_reads_previous_digest(self, tmp_path, gh, lin):
        lin.add("AFX-1", "In Progress", started_at=OLD)
        client = ld.LinearClient("k", opener=lin.opener)
        args = self._args(tmp_path)
        afx.reconcile(args, gh=gh, client=client, now=NOW)
        args.previous = str(tmp_path / "digest.json")
        args.summary = str(tmp_path / "second.md")
        afx.reconcile(args, gh=gh, client=client, now=NOW)
        assert "not repeated" in (tmp_path / "second.md").read_text()

    def test_corrupt_previous_digest_is_ignored(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert afx._read_previous(str(bad)) is None
        assert afx._read_previous(None) is None

    def test_missing_linear_key_is_an_error(self, monkeypatch, capsys):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        assert afx.main(["reconcile"]) == afx.EXIT_ERROR
        assert "LINEAR_API_KEY" in capsys.readouterr().err


class TestGhCliPullState:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"state": "open"}, "open"),
            ({"state": "closed", "merged_at": "2026-11-22T00:00:00Z"}, "merged"),
            ({"state": "closed", "merged_at": None}, "closed"),
        ],
    )
    def test_states(self, body, expected):
        cli = ds.GhCli(REPO, runner=lambda args: json.dumps(body))
        assert cli.pull_state(9) == expected

    def test_missing_pr_raises(self):
        def runner(args):
            raise ds.GitHubError("HTTP 404", 404)

        with pytest.raises(ds.GitHubError):
            ds.GhCli(REPO, runner=runner).pull_state(9)


class TestGhCliOpenPulls:
    def test_lists_open_prs_into_main_with_mergeability(self):
        calls: list[str] = []

        def runner(args):
            calls.append(args[1])
            if "pulls?" in args[1]:
                return json.dumps(
                    [
                        {
                            "number": 30,
                            "title": "AFX-30: change",
                            "user": {"login": "agentfx-pe[bot]"},
                            "head": {"sha": "sha30"},
                        }
                    ]
                )
            if args[1].endswith("/statuses?per_page=100"):
                return json.dumps([_status("2026-11-22T09:00:00Z", _CONFLICT)])
            return json.dumps({"mergeable": False})

        cli = ds.GhCli(REPO, runner=runner)
        assert cli.open_pulls() == [
            {
                "number": 30,
                "title": "AFX-30: change",
                "author": "agentfx-pe[bot]",
                "head_sha": "sha30",
                "mergeable": False,
            }
        ]
        assert "state=open" in calls[0] and "base=main" in calls[0]
        assert calls[1] == f"repos/{REPO}/pulls/30"
        assert cli.commit_statuses("sha30")[0]["description"] == _CONFLICT


class TestGhCliMergedPulls:
    """The real client, not the fake: the daily workflow calls this one."""

    def test_keeps_in_window_merges_and_reads_merged_by(self):
        listing = [
            {
                "number": 12,
                "title": "AFX-2: by hand",
                "user": {"login": "widarecu"},
                "merged_at": "2026-11-22T09:00:00Z",
                "updated_at": "2026-11-22T09:00:00Z",
            },
            {
                "number": 11,
                "title": "closed, never merged",
                "user": {"login": "widarecu"},
                "merged_at": None,
                "updated_at": "2026-11-21T09:00:00Z",
            },
            {
                "number": 10,
                "title": "AFX-1: before the window",
                "user": {"login": "devin-ai-integration[bot]"},
                "merged_at": "2026-11-01T09:00:00Z",
                "updated_at": "2026-11-20T09:00:00Z",
            },
        ]
        calls: list[str] = []

        def runner(args):
            calls.append(args[1])
            if "pulls?" in args[1]:
                return json.dumps(listing)
            return json.dumps({"merged_by": {"login": "widarecu"}})

        pulls = ds.GhCli(REPO, runner=runner).merged_pulls("2026-11-16T12:00:00Z")

        assert pulls == [
            {
                "number": 12,
                "title": "AFX-2: by hand",
                "author": "widarecu",
                "merged_by": "widarecu",
                "merged_at": "2026-11-22T09:00:00Z",
            }
        ]
        assert calls[-1] == f"repos/{REPO}/pulls/12"
        assert "base=main" in calls[0] and "state=closed" in calls[0]

    def test_api_failure_raises_github_error(self):
        def runner(args):
            raise ds.GitHubError("HTTP 502", 502)

        with pytest.raises(ds.GitHubError):
            ds.GhCli(REPO, runner=runner).merged_pulls("2026-11-16T12:00:00Z")
