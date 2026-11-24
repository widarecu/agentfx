"""Deploy ticket sync (AFX-122).

`afx deploy-sync` moves the tickets of every PR merged between the last
successful `deployed/staging` SHA and the attempted SHA: Done on success or
docs-only, Blocked + `needs-wilson` + one actionable comment on any backend,
frontend or verify failure. GitHub and Linear are faked here; nothing in this
file talks to a real API.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agentfx.integrations import deploy_sync as ds
from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeLinear as LinearFake

BASE = "b" * 40
SHA = "a" * 40
RUN_URL = "https://github.com/widarecu/agentfx/actions/runs/42"
STAGING = "https://agentfx-staging.up.railway.app"


def _sha(n: int) -> str:
    return f"{n:040x}"


def _pr(number, *, title, head, merge_sha, merged=True):
    return {
        "number": number,
        "title": title,
        "head": {"ref": head},
        "merged_at": "2026-11-23T10:00:00Z" if merged else None,
        "merge_commit_sha": merge_sha,
        "html_url": f"https://github.com/widarecu/agentfx/pull/{number}",
    }


class FakeGitHub:
    def __init__(self, *, tags=None, commits=None, pulls=None, status="ahead"):
        self.tags: dict[str, str] = dict(tags or {})
        self.commits = commits or {}
        self.pulls = pulls or {}
        self.status = status
        self.created: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.fail = False

    def _check(self):
        if self.fail:
            raise ds.GitHubError("HTTP 503")

    def get_tag_sha(self, name):
        self._check()
        return self.tags.get(name)

    def create_tag_ref(self, name, sha):
        self._check()
        self.tags[name] = sha
        self.created.append((name, sha))

    def delete_tag_ref(self, name):
        self._check()
        self.tags.pop(name, None)
        self.deleted.append(name)

    def list_tag_refs(self, prefix):
        self._check()
        return {k: v for k, v in self.tags.items() if k.startswith(prefix)}

    def compare(self, base, head):
        self._check()
        return self.status, list(self.commits.get((base, head), []))

    def commit_pulls(self, sha):
        self._check()
        return list(self.pulls.get(sha, []))


class FakeLinear:
    """Same surface as `linear_delivery.LinearDelivery`, in memory."""

    def __init__(self, issues):
        self.issues = issues
        self.writes: list[tuple] = []
        self.fail_on: set[str] = set()

    def _check(self, ident):
        if ident in self.fail_on:
            raise ld.LinearError("linear graphql 503: upstream unavailable")

    def issue(self, ident):
        self._check(ident)
        return self.issues.get(ident)

    def comment_bodies(self, issue):
        return issue.setdefault("comments", [])

    def set_state(self, issue, name):
        self._check(issue["identifier"])
        if issue["state"]["name"] == name:
            return False
        kind = {"Done": "completed", "Blocked": "started"}[name]
        issue["state"] = {"name": name, "type": kind}
        self.writes.append(("state", issue["identifier"], name))
        return True

    def has_label(self, issue, name):
        return name in issue.setdefault("labels", [])

    def add_label(self, issue, name):
        if name in issue["labels"]:
            return False
        issue["labels"].append(name)
        self.writes.append(("add_label", issue["identifier"], name))
        return True

    def remove_label(self, issue, name):
        if name not in issue["labels"]:
            return False
        issue["labels"].remove(name)
        self.writes.append(("remove_label", issue["identifier"], name))
        return True

    def comment_once(self, issue, body, marker):
        self._check(issue["identifier"])
        comments = self.comment_bodies(issue)
        if any(marker in c for c in comments):
            return False
        comments.append(body)
        self.writes.append(("comment", issue["identifier"], marker))
        return True


def _issue(ident, state="In Staging", kind="started", labels=None, comments=None):
    return {
        "identifier": ident,
        "state": {"name": state, "type": kind},
        "labels": list(labels or []),
        "comments": list(comments or []),
    }


def _two_pr_range():
    c1, c2 = _sha(1), SHA
    gh = FakeGitHub(
        tags={ds.STAGING_TAG: BASE},
        commits={(BASE, SHA): [c1, c2]},
        pulls={
            c1: [_pr(10, title="AFX-10: fix", head="agent/afx-10-fix", merge_sha=c1)],
            c2: [_pr(11, title="AFX-11: more", head="agent/AFX-11-more", merge_sha=c2)],
        },
    )
    return gh


def _run(gh, linear, outcomes, *, apply=True, baseline=None, sha=SHA):
    return ds.sync(
        gh,
        linear,
        attempted=sha,
        verdict=ds.classify(outcomes),
        run_url=RUN_URL,
        staging_url=STAGING,
        apply=apply,
        baseline=baseline,
    )


OK = {"backend": "success", "frontend": "success", "verify": "success"}
FRONT_FAIL = {"backend": "success", "frontend": "failure", "verify": "skipped"}


# ── verdicts ─────────────────────────────────────────────────────────


def test_classify_success_docs_only_failure_and_cancelled():
    assert ds.classify(OK).kind == "success"
    docs_only = {"backend": "skipped", "frontend": "skipped", "verify": "skipped"}
    assert ds.classify(docs_only).kind == "success"
    v = ds.classify({"backend": "failure", "frontend": "failure", "verify": "success"})
    assert v.kind == "failure" and v.failed == ("backend", "frontend")
    assert ds.classify({**OK, "frontend": "cancelled"}).kind == "indeterminate"
    assert ds.classify({**OK, "verify": ""}).kind == "indeterminate"
    # A failure is reported even if another component was cancelled.
    assert ds.classify({**FRONT_FAIL, "verify": "cancelled"}).kind == "failure"


# ── PR → ticket association ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "head", "expected"),
    [
        ("AFX-12: thing", "agent/afx-12-thing", "AFX-12"),
        ("fix: thing", "agent/AFX-12-thing", "AFX-12"),
        ("AFX-12: thing", "devin/123-thing", "AFX-12"),
    ],
)
def test_ticket_for_pr_one_id_from_title_or_head(title, head, expected):
    pr = _pr(1, title=title, head=head, merge_sha=SHA)
    assert ds.ticket_for_pr(pr) == (expected, "")


def test_ticket_for_pr_fails_closed_on_ambiguous_or_missing_id():
    ambiguous = _pr(1, title="AFX-12: thing", head="agent/afx-13-x", merge_sha=SHA)
    ticket, reason = ds.ticket_for_pr(ambiguous)
    assert ticket is None and "AFX-12" in reason and "AFX-13" in reason
    assert (
        ds.ticket_for_pr(
            _pr(2, title="Promote", head="feat/agent-fleet", merge_sha=SHA)
        )[0]
        is None
    )


def test_body_references_are_ignored():
    pr = _pr(1, title="AFX-12: x", head="agent/afx-12", merge_sha=SHA)
    pr["body"] = "Related: AFX-94, AFX-95"
    assert ds.ticket_for_pr(pr) == ("AFX-12", "")


# ── range collection ─────────────────────────────────────────────────


def test_collect_tickets_multiple_prs_dedupes_and_skips_unmerged():
    c1, c2, c3 = _sha(1), _sha(2), SHA
    pr10 = _pr(10, title="AFX-10: a", head="agent/afx-10", merge_sha=c1)
    gh = FakeGitHub(
        pulls={
            c1: [pr10],
            # The same PR is reported for its later commit too.
            c2: [
                pr10,
                _pr(
                    12,
                    title="AFX-12: open",
                    head="agent/afx-12",
                    merge_sha=None,
                    merged=False,
                ),
            ],
            c3: [
                _pr(
                    13,
                    title="Promote feat/agent-fleet → main",
                    head="feat/agent-fleet",
                    merge_sha=c3,
                ),
                _pr(14, title="AFX-10: follow-up", head="agent/afx-10-b", merge_sha=c3),
                _pr(15, title="AFX-1 and AFX-2", head="agent/x", merge_sha=c3),
                # Merged elsewhere; its merge commit is outside the range.
                _pr(16, title="AFX-16: x", head="agent/afx-16", merge_sha=_sha(99)),
            ],
        }
    )
    tickets, skipped = ds.collect_tickets(gh, [c1, c2, c3])
    assert tickets == {"AFX-10": [10, 14]}
    assert any("#13" in s for s in skipped)
    assert any("#15" in s and "ambiguous" in s for s in skipped)
    assert not any("#12" in s or "#16" in s for s in skipped)


def test_empty_range_touches_nothing():
    gh = FakeGitHub(tags={ds.STAGING_TAG: BASE}, commits={(BASE, SHA): []})
    linear = FakeLinear({})
    report = _run(gh, linear, OK)
    assert report.exit_code == 0 and linear.writes == []
    assert "no merged PRs" in "\n".join(report.lines)


def test_stale_retry_of_older_sha_is_a_noop():
    gh = FakeGitHub(tags={ds.STAGING_TAG: BASE}, status="behind")
    linear = FakeLinear({})
    assert _run(gh, linear, FRONT_FAIL).exit_code == 0
    assert linear.writes == []


def test_diverged_range_fails_closed():
    gh = FakeGitHub(tags={ds.STAGING_TAG: BASE}, status="diverged")
    linear = FakeLinear({"AFX-10": _issue("AFX-10")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 1 and linear.writes == []


# ── success / failure paths ──────────────────────────────────────────


def test_success_moves_tickets_to_done_with_staging_link():
    gh = _two_pr_range()
    linear = FakeLinear(
        {"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11", "In Review")}
    )
    report = _run(gh, linear, OK)
    assert report.exit_code == 0
    for ident in ("AFX-10", "AFX-11"):
        issue = linear.issues[ident]
        assert issue["state"]["name"] == "Done"
        (body,) = issue["comments"]
        assert STAGING in body and ds.done_marker(SHA) in body


def test_docs_only_deploy_moves_tickets_to_done():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    docs_only = {"backend": "skipped", "frontend": "skipped", "verify": "skipped"}
    _run(gh, linear, docs_only)
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"


def test_failure_blocks_names_component_run_url_and_rerun():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    report = _run(gh, linear, FRONT_FAIL)
    assert report.exit_code == 0
    issue = linear.issues["AFX-10"]
    assert issue["state"]["name"] == "Blocked"
    assert issue["labels"] == ["needs-wilson"]
    (body,) = issue["comments"]
    assert "frontend (Vercel)" in body and "backend" not in body.split("Fix:")[0]
    assert RUN_URL in body and "Re-run failed jobs" in body
    assert ds.blocked_marker(SHA) in body and ds.LABEL_ADDED_MARKER in body


def test_duplicate_runs_are_idempotent_on_every_path():
    for outcomes in (OK, FRONT_FAIL):
        gh = _two_pr_range()
        linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
        _run(gh, linear, outcomes)
        first = list(linear.writes)
        _run(gh, linear, outcomes)
        assert linear.writes == first
        assert len(linear.issues["AFX-10"]["comments"]) == 1


def test_ticket_reopened_after_this_deploy_is_not_reclosed():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    _run(gh, linear, OK)
    linear.issues["AFX-10"]["state"] = {"name": "In Progress", "type": "started"}
    writes = len(linear.writes)
    report = _run(gh, linear, OK)
    assert linear.issues["AFX-10"]["state"]["name"] == "In Progress"
    assert len(linear.writes) == writes
    assert any("AFX-10: reopened" in line for line in report.lines)


def test_cancelled_deploy_changes_nothing():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10")})
    report = _run(gh, linear, {**OK, "backend": "cancelled"})
    assert report.exit_code == 0 and linear.writes == []


def test_canceled_done_and_unstarted_tickets_are_not_overridden():
    gh = _two_pr_range()
    linear = FakeLinear(
        {
            "AFX-10": _issue("AFX-10", "Canceled", "canceled"),
            "AFX-11": _issue("AFX-11", "Ready", "unstarted"),
        }
    )
    _run(gh, linear, OK)
    _run(gh, linear, FRONT_FAIL)
    assert linear.writes == []
    done = FakeLinear(
        {
            "AFX-10": _issue("AFX-10", "Done", "completed"),
            "AFX-11": _issue("AFX-11", "Done", "completed"),
        }
    )
    _run(_two_pr_range(), done, FRONT_FAIL)
    assert done.writes == []


def test_missing_linear_issue_is_skipped_not_fatal():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-11": _issue("AFX-11")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 0
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    assert any("AFX-10" in line and "not found" in line for line in report.lines)


def test_dry_run_reads_but_never_writes():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    report = _run(gh, linear, FRONT_FAIL, apply=False)
    assert linear.writes == [] and gh.created == [] and gh.deleted == []
    assert any("would" in line for line in report.lines)


# ── recovery ─────────────────────────────────────────────────────────


def test_rerun_after_failure_moves_blocked_tickets_to_done_and_clears_owned_label():
    gh = _two_pr_range()
    gh.tags[ds.record_tag(SHA)] = BASE
    linear = FakeLinear(
        {
            "AFX-10": _issue("AFX-10"),
            "AFX-11": _issue("AFX-11", labels=["needs-wilson"]),
        }
    )
    _run(gh, linear, FRONT_FAIL)
    assert gh.tags[ds.STAGING_TAG] == BASE  # baseline preserved
    # Re-run succeeds; Deploy moves the tag *after* ticket-sync's record.
    gh.tags[ds.STAGING_TAG] = SHA
    report = _run(gh, linear, OK)
    assert report.exit_code == 0
    a10, a11 = linear.issues["AFX-10"], linear.issues["AFX-11"]
    assert a10["state"]["name"] == a11["state"]["name"] == "Done"
    assert a10["labels"] == []  # added by this automation: cleared
    assert a11["labels"] == ["needs-wilson"]  # Wilson's own label: kept
    assert ds.LABEL_CLEARED_MARKER in a10["comments"][-1]


def test_later_success_includes_outstanding_failed_tickets():
    failed_sha, later = _sha(1), SHA
    gh = FakeGitHub(
        tags={ds.STAGING_TAG: BASE, ds.record_tag(failed_sha): BASE},
        commits={(BASE, failed_sha): [failed_sha], (BASE, later): [failed_sha, later]},
        pulls={
            failed_sha: [
                _pr(10, title="AFX-10: a", head="agent/afx-10", merge_sha=failed_sha)
            ],
            later: [_pr(11, title="AFX-11: b", head="agent/afx-11", merge_sha=later)],
        },
    )
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    _run(gh, linear, FRONT_FAIL, sha=failed_sha)
    assert linear.issues["AFX-10"]["state"]["name"] == "Blocked"
    ds.record(gh, later, apply=True)
    gh.tags[ds.STAGING_TAG] = later
    _run(gh, linear, OK, sha=later)
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    # The superseded failed attempt's record is cleaned up; this one is kept
    # so a duplicate run or reconcile can replay it.
    assert ds.record_tag(failed_sha) not in gh.tags
    assert gh.tags[ds.record_tag(later)] == BASE


def test_record_persists_baseline_once_and_never_overwrites():
    gh = FakeGitHub(tags={ds.STAGING_TAG: BASE})
    ds.record(gh, SHA, apply=True)
    assert gh.tags[ds.record_tag(SHA)] == BASE
    gh.tags[ds.STAGING_TAG] = SHA
    ds.record(gh, SHA, apply=True)  # a re-run after the tag moved
    assert gh.tags[ds.record_tag(SHA)] == BASE and len(gh.created) == 1


def test_record_dry_run_and_tag_already_at_sha_write_nothing():
    gh = FakeGitHub(tags={ds.STAGING_TAG: BASE})
    ds.record(gh, SHA, apply=False)
    gh2 = FakeGitHub(tags={ds.STAGING_TAG: SHA})
    ds.record(gh2, SHA, apply=True)
    assert gh.created == gh2.created == []


def test_missed_sync_after_tag_move_is_recovered_from_record():
    gh = _two_pr_range()
    ds.record(gh, SHA, apply=True)
    gh.tags[ds.STAGING_TAG] = SHA  # tag moved, ticket sync never ran
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    report = ds.recover(gh, linear, run_url=RUN_URL, staging_url=STAGING, apply=True)
    assert report.exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"


def test_tag_at_sha_without_record_fails_closed():
    gh = _two_pr_range()
    gh.tags[ds.STAGING_TAG] = SHA
    linear = FakeLinear({"AFX-10": _issue("AFX-10")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 1 and linear.writes == []
    assert "--baseline" in "\n".join(report.lines)
    # An explicit baseline recovers it.
    assert _run(gh, linear, OK, baseline=BASE).exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"


def test_dry_run_record_then_tag_move_then_sync_reports_the_range():
    """AFX-133: dry-run writes no record, the tag still moves, and sync
    uses the SHA the move replaced instead of failing the deploy."""
    gh = _two_pr_range()
    ds.record(gh, SHA, apply=False)
    assert gh.created == []
    previous = gh.tags[ds.STAGING_TAG]
    gh.tags[ds.STAGING_TAG] = SHA  # Record deployed/staging
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    report = ds.sync(
        gh,
        linear,
        attempted=SHA,
        verdict=ds.classify(OK),
        run_url=RUN_URL,
        staging_url=STAGING,
        apply=False,
        previous=previous,
    )
    out = "\n".join(report.lines)
    assert report.exit_code == 0 and linear.writes == [] and gh.created == []
    assert f"{BASE[:7]}..{SHA[:7]}" in out and "from this run's tag move" in out
    assert "AFX-10: would move In Staging -> Done" in out
    assert "AFX-11: would move In Staging -> Done" in out


def test_failed_record_in_apply_mode_falls_back_to_the_replaced_tag():
    gh = _two_pr_range()
    gh.tags[ds.STAGING_TAG] = SHA  # record job failed, tag moved anyway
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    report = ds.sync(
        gh,
        linear,
        attempted=SHA,
        verdict=ds.classify(OK),
        run_url=RUN_URL,
        staging_url=STAGING,
        apply=True,
        previous=BASE,
    )
    assert report.exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"


def test_recorded_baseline_wins_over_previous():
    gh = _two_pr_range()
    ds.record(gh, SHA, apply=True)
    gh.tags[ds.STAGING_TAG] = SHA
    base, source, _, _ = ds.resolve_baseline(gh, SHA, previous=_sha(99))
    assert base == BASE and source.startswith("recorded")


@pytest.mark.parametrize("previous", [None, "", SHA])
def test_tag_at_sha_without_usable_previous_still_fails_closed(previous):
    gh = _two_pr_range()
    gh.tags[ds.STAGING_TAG] = SHA
    with pytest.raises(ds.BaselineUnknownError):
        ds.resolve_baseline(gh, SHA, previous=previous)


def test_previous_is_ignored_while_the_tag_is_behind():
    gh = _two_pr_range()  # tag still at BASE (failed deploy, tag not moved)
    base, source, _, _ = ds.resolve_baseline(gh, SHA, previous=_sha(99))
    assert base == BASE and source == f"{ds.STAGING_TAG} tag"


def test_first_deploy_without_tag_syncs_only_the_attempted_commit():
    gh = FakeGitHub(
        pulls={SHA: [_pr(11, title="AFX-11: b", head="agent/afx-11", merge_sha=SHA)]},
    )
    linear = FakeLinear({"AFX-11": _issue("AFX-11")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 0
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    assert any("first deploy" in line for line in report.lines)
    ds.record(gh, SHA, apply=True)
    # Self-pointing record: "no earlier deploy", nothing guessed.
    assert gh.created == [(ds.record_tag(SHA), SHA)]


def test_first_tag_created_before_sync_still_syncs_own_pr():
    gh = FakeGitHub(
        pulls={SHA: [_pr(11, title="AFX-11: b", head="agent/afx-11", merge_sha=SHA)]},
    )
    ds.record(gh, SHA, apply=True)
    gh.tags[ds.STAGING_TAG] = SHA  # Deploy creates the first tag before sync
    linear = FakeLinear({"AFX-11": _issue("AFX-11")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 0
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    assert ds.recover(gh, linear, run_url=RUN_URL, apply=True).exit_code == 0


def _consecutive_deploys():
    s1, s2 = _sha(1), SHA
    gh = FakeGitHub(
        tags={ds.STAGING_TAG: BASE},
        commits={(BASE, s1): [s1], (s1, s2): [s2], (BASE, s2): [s1, s2]},
        pulls={
            s1: [_pr(10, title="AFX-10: a", head="agent/afx-10", merge_sha=s1)],
            s2: [_pr(11, title="AFX-11: b", head="agent/afx-11", merge_sha=s2)],
        },
    )
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    return gh, linear, s1, s2


def test_missed_sync_is_covered_by_the_next_deploy():
    gh, linear, s1, s2 = _consecutive_deploys()
    ds.record(gh, s1, apply=True)
    gh.tags[ds.STAGING_TAG] = s1  # s1 deployed, its ticket sync never ran
    ds.record(gh, s2, apply=True)
    gh.tags[ds.STAGING_TAG] = s2
    report = _run(gh, linear, OK, sha=s2)
    assert report.exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    # s1's record goes only now that a success covered it.
    assert ds.record_tag(s1) not in gh.tags
    assert gh.tags[ds.record_tag(s2)] == s1


def test_second_deploy_after_first_deploy_covers_both():
    gh, linear, s1, s2 = _consecutive_deploys()
    gh.tags.pop(ds.STAGING_TAG)
    ds.record(gh, s1, apply=True)  # first deploy: self-pointing record
    gh.tags[ds.STAGING_TAG] = s1  # its sync missed
    ds.record(gh, s2, apply=True)
    gh.tags[ds.STAGING_TAG] = s2
    assert _run(gh, linear, OK, sha=s2).exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    assert ds.record_tag(s1) not in gh.tags


def test_failure_after_missed_success_does_not_block_shipped_tickets():
    gh, linear, s1, s2 = _consecutive_deploys()
    ds.record(gh, s1, apply=True)
    gh.tags[ds.STAGING_TAG] = s1  # s1 shipped, its sync missed
    ds.record(gh, s2, apply=True)
    _run(gh, linear, FRONT_FAIL, sha=s2)
    assert linear.issues["AFX-10"]["state"]["name"] == "In Staging"
    assert linear.issues["AFX-11"]["state"]["name"] == "Blocked"
    assert ds.record_tag(s1) in gh.tags  # still owed a Done by the next success


def test_consecutive_deploys_do_not_recomment_done_tickets():
    gh, linear, s1, s2 = _consecutive_deploys()
    ds.record(gh, s1, apply=True)
    gh.tags[ds.STAGING_TAG] = s1
    _run(gh, linear, OK, sha=s1)
    before = len(linear.issues["AFX-10"]["comments"])
    ds.record(gh, s2, apply=True)
    gh.tags[ds.STAGING_TAG] = s2
    _run(gh, linear, OK, sha=s2)
    assert len(linear.issues["AFX-10"]["comments"]) == before
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"


# ── outages and partial failure ──────────────────────────────────────


def test_github_outage_writes_nothing_and_exits_nonzero():
    gh = _two_pr_range()
    gh.fail = True
    linear = FakeLinear({"AFX-10": _issue("AFX-10")})
    report = _run(gh, linear, OK)
    assert report.exit_code == 1 and linear.writes == []


def test_partial_linear_failure_finishes_others_and_rerun_completes():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    linear.fail_on = {"AFX-10"}
    report = _run(gh, linear, OK)
    assert report.exit_code == 1
    assert linear.issues["AFX-11"]["state"]["name"] == "Done"
    assert ds.record_tag(SHA) not in gh.deleted
    linear.fail_on = set()
    assert _run(gh, linear, OK).exit_code == 0
    assert linear.issues["AFX-10"]["state"]["name"] == "Done"
    assert len(linear.issues["AFX-11"]["comments"]) == 1


def test_failure_comment_posted_but_label_lost_is_repaired_on_rerun():
    gh = _two_pr_range()
    linear = FakeLinear({"AFX-10": _issue("AFX-10"), "AFX-11": _issue("AFX-11")})
    _run(gh, linear, FRONT_FAIL)
    linear.issues["AFX-10"]["labels"].clear()  # label write lost
    _run(gh, linear, FRONT_FAIL)
    assert linear.issues["AFX-10"]["labels"] == ["needs-wilson"]
    assert len(linear.issues["AFX-10"]["comments"]) == 1


# ── GitHub client (gh CLI) pagination ────────────────────────────────


def test_gh_compare_and_pulls_paginate():
    calls: list[list[str]] = []
    pages = {
        "compare/b...a?per_page=100&page=1": {
            "status": "ahead",
            "total_commits": 150,
            "commits": [{"sha": _sha(i)} for i in range(100)],
        },
        "compare/b...a?per_page=100&page=2": {
            "status": "ahead",
            "total_commits": 150,
            "commits": [{"sha": _sha(i)} for i in range(100, 150)],
        },
        "commits/x/pulls?per_page=100&page=1": [{"number": i} for i in range(100)],
        "commits/x/pulls?per_page=100&page=2": [{"number": 100}],
    }

    def runner(args):
        calls.append(args)
        path = args[-1].removeprefix("repos/o/r/")
        return json.dumps(pages[path])

    gh = ds.GhCli("o/r", runner=runner)
    status, commits = gh.compare("b", "a")
    assert status == "ahead" and len(commits) == 150 and commits[-1] == _sha(149)
    assert len(gh.commit_pulls("x")) == 101


def test_gh_missing_tag_is_none_and_annotated_tag_is_dereferenced():
    def runner(args):
        path = args[-1]
        if path.endswith("git/ref/tags/deployed/staging"):
            return json.dumps({"object": {"type": "tag", "sha": "t1"}})
        if path.endswith("git/tags/t1"):
            return json.dumps({"object": {"type": "commit", "sha": SHA}})
        raise ds.GitHubError("gh: Not Found (HTTP 404)", status=404)

    gh = ds.GhCli("o/r", runner=runner)
    assert gh.get_tag_sha(ds.STAGING_TAG) == SHA
    assert gh.get_tag_sha(ds.record_tag(SHA)) is None


# ── Linear adapter (real LinearClient over linear_fake) ──────────────


def _adapter(fake):
    return ld.DeployIssues(ld.LinearClient("k", opener=fake.opener))


def test_adapter_end_to_end_success_and_failure():
    fake = LinearFake(page_size=1)
    fake.add("AFX-10", "In Staging")
    fake.add("AFX-11", "Canceled")
    gh = _two_pr_range()
    assert _run(gh, _adapter(fake), FRONT_FAIL).exit_code == 0
    a10 = fake.issues["AFX-10"]
    assert a10.state == "Blocked" and a10.labels == ["needs-wilson"]
    assert ds.LABEL_ADDED_MARKER in a10.comments[-1]
    assert fake.issues["AFX-11"].state == "Canceled"  # Wilson's call stands

    before = list(fake.mutations)
    assert _run(gh, _adapter(fake), FRONT_FAIL).exit_code == 0
    assert fake.mutations == before  # duplicate run, comments paginated

    assert _run(gh, _adapter(fake), OK).exit_code == 0
    assert a10.state == "Done" and a10.labels == []
    assert ds.LABEL_CLEARED_MARKER in a10.comments[-1]


def test_code_failure_recovers_through_ready_and_a_fix_pr_on_the_same_ticket():
    """Blocked by a failed deploy -> Wilson moves it to Ready -> afx start ->
    a fix PR naming the same ticket -> its verified deploy closes it."""
    failed, fix = _sha(1), SHA
    gh = FakeGitHub(
        tags={ds.STAGING_TAG: BASE},
        commits={(BASE, failed): [failed], (BASE, fix): [failed, fix]},
        pulls={
            failed: [
                _pr(10, title="AFX-10: a", head="agent/AFX-10-a", merge_sha=failed)
            ],
            fix: [_pr(12, title="AFX-10: fix", head="agent/AFX-10-fix", merge_sha=fix)],
        },
    )
    fake = LinearFake()
    fake.add("AFX-10", "In Review")
    ds.record(gh, failed, apply=True)
    assert _run(gh, _adapter(fake), FRONT_FAIL, sha=failed).exit_code == 0
    a10 = fake.issues["AFX-10"]
    assert a10.state == "Blocked" and a10.labels == ["needs-wilson"]

    a10.labels.remove("needs-wilson")  # Wilson answers the block
    a10.state = "Ready"
    client = ld.LinearClient("k", opener=fake.opener)
    assert ld.start(client, "AFX-10").ok
    assert a10.state == "In Progress"
    a10.state = "In Review"  # the fix PR opens

    ds.record(gh, fix, apply=True)
    gh.tags[ds.STAGING_TAG] = fix
    report = _run(gh, _adapter(fake), OK, sha=fix)
    assert report.exit_code == 0
    assert a10.state == "Done" and a10.labels == []
    assert ds.LABEL_CLEARED_MARKER in a10.comments[-1]
    assert ds.record_tag(failed) not in gh.tags


def test_adapter_falls_back_to_workspace_label():
    fake = LinearFake(labels=[], workspace_labels=["needs-wilson"])
    fake.add("AFX-10", "In Staging")
    assert _run(_two_pr_range(), _adapter(fake), FRONT_FAIL).exit_code == 0
    assert fake.issues["AFX-10"].labels == ["needs-wilson"]


def test_adapter_missing_issue_is_none():
    assert _adapter(LinearFake()).issue("AFX-404") is None


def test_adapter_http_error_raises():
    fake = LinearFake()
    fake.http_error = (400, {"errors": [{"message": "boom"}]})
    with pytest.raises(ld.LinearError, match="boom"):
        _adapter(fake).issue("AFX-1")


def test_adapter_unknown_state_raises():
    fake = LinearFake(states=["Ready"])
    fake.add("AFX-1", "Ready")
    adapter = _adapter(fake)
    with pytest.raises(ld.NotFoundError, match="Blocked"):
        adapter.set_state(adapter.issue("AFX-1"), "Blocked")


def test_client_rejected_remove_label_raises():
    def opener(req, timeout=None):
        return io.BytesIO(
            json.dumps({"data": {"issueRemoveLabel": {"success": False}}}).encode()
        )

    with pytest.raises(ld.LinearError, match="issueRemoveLabel"):
        ld.LinearClient("k", opener=opener).remove_label("u1", "l1")
