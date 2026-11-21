"""State-transition workflow (AFX-70 Phase 1).

`scripts/fleet_state_on_promote.py` moves Linear tickets to `Done` or
`Blocked` based on the main-merge + deploy outcome. These cases pin the
pure parts: AFX-ID extraction, marker shape, success/failure comment
bodies, state-transition guards (no backwards moves, idempotent re-runs),
Linear-API failure as no-op.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "fleet_state_on_promote.py"

_spec = importlib.util.spec_from_file_location("fleet_state_on_promote", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fsop = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fsop
_spec.loader.exec_module(fsop)

_NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
_SHA = "eb746c6" + "0" * 33


# ─────────────────────────────────────────────────────────────────────
# AFX-ID extraction
# ─────────────────────────────────────────────────────────────────────


def test_afx_ids_are_upper_cased_and_deduped():
    assert fsop.afx_ids("afx-70 fix: AFX-70 + AFX-87 (#116)") == ["AFX-70", "AFX-87"]
    assert fsop.afx_ids("Merge back-merge main → feat/agent-fleet") == []


def test_collect_afx_ids_across_commits_dedupes():
    commits = [
        {"commit": {"message": "AFX-70: state machine\n\nPhase 1 shipped."}},
        {"commit": {"message": "AFX-87: candle race follow-up"}},
        {"commit": {"message": "AFX-70: fix test fixture"}},  # duplicate
        {
            "commit": {
                "message": "Merge pull request #116 from widarecu/feat/agent-fleet"
            }
        },
    ]
    assert fsop.collect_afx_ids_from_commits(commits) == ["AFX-70", "AFX-87"]


def test_collect_afx_ids_empty_when_no_tags():
    commits = [{"commit": {"message": "Merge pull request #117 from widarecu/main"}}]
    assert fsop.collect_afx_ids_from_commits(commits) == []


def test_collect_afx_ids_ignores_body_only_references():
    """Agents reference related tickets in commit BODIES as context ("split
    out into AFX-94 and AFX-95"); those must not be treated as tickets
    this merge closes. Subject-only scan guards against false positives.
    This was observed live on 2026-11-18: PR #118's commit referenced
    AFX-94/AFX-95 in the body, and the first version of the script picked
    them up and tried to transition them — a bug caught before the
    mutation reached Linear (because the 400 bail happened first).
    """
    commits = [
        {
            "commit": {
                "message": (
                    "AFX-70 Phase 1: Linear state honesty\n\n"
                    "Phases 2 and 3 are split into AFX-94 and AFX-95 — "
                    "this PR does not touch them."
                )
            }
        },
    ]
    assert fsop.collect_afx_ids_from_commits(commits) == ["AFX-70"]


def _pr(number, title, head, *, merged=True):
    return {
        "number": number,
        "title": title,
        "head": {"ref": head},
        "merged_at": "2026-11-20T11:42:50Z" if merged else None,
    }


def test_collect_afx_ids_from_prs_reads_title_and_branch():
    """AFX-106 regression: #137 was a single-commit squash, so its commit
    subject on main lacked the ID its PR title carried, and #139/#141 named
    AFX-106 only in their branches. Linear linked all three (In Staging);
    the Done step must see the same IDs."""
    prs = [
        _pr(
            137,
            "fix(paper,backtest): AFX-106 wick-mode broker SL/TP",
            "agent/AFX-106-wick-stop-fidelity",
        ),
        _pr(139, "fix(paper): address #137 review", "agent/AFX-106-review-fixes"),
        _pr(140, "fix(paper): one order per signal bar (AFX-50)", "agent/afx-50-x"),
        _pr(114, "fix: gate B drawdown", "agent/2026-11-12-afx-63-bug-gate-b"),
    ]
    assert fsop.collect_afx_ids_from_prs(prs) == ["AFX-106", "AFX-50", "AFX-63"]


def test_collect_afx_ids_from_prs_skips_unmerged_promote_and_backmerge():
    prs = [
        _pr(143, "Promote feat/agent-fleet → main (AFX-1)", "feat/agent-fleet"),
        _pr(144, "Back-merge main → feat/agent-fleet (AFX-2)", "main"),
        _pr(150, "AFX-3 still open", "agent/AFX-3-wip", merged=False),
    ]
    assert fsop.collect_afx_ids_from_prs(prs) == []


def test_fetch_merged_prs_dedupes_and_survives_lookup_failure(monkeypatch):
    calls = []

    def fake_gh_json(*args):
        calls.append(args[1])
        if args[1].endswith("/bad/pulls"):
            raise fsop.subprocess.CalledProcessError(1, "gh")
        return [_pr(137, "t", "agent/AFX-106-x")]

    monkeypatch.setattr(fsop, "_gh_json", fake_gh_json)
    commits = [{"sha": "aaa"}, {"sha": "bad"}, {"sha": "bbb"}, {}]
    prs = fsop.fetch_merged_prs("w/r", commits)
    assert [p["number"] for p in prs] == [137]
    assert calls == [
        "repos/w/r/commits/aaa/pulls",
        "repos/w/r/commits/bad/pulls",
        "repos/w/r/commits/bbb/pulls",
    ]


def test_merge_afx_ids_keeps_first_seen_order():
    assert fsop.merge_afx_ids(["AFX-104", "AFX-50"], ["AFX-106", "AFX-50"]) == [
        "AFX-104",
        "AFX-50",
        "AFX-106",
    ]


# ─────────────────────────────────────────────────────────────────────
# Markers + comment bodies
# ─────────────────────────────────────────────────────────────────────


def test_markers_are_sha_scoped_and_distinct():
    sm = fsop.success_marker(_SHA)
    fm = fsop.failed_marker(_SHA)
    assert "eb746c6" in sm and "eb746c6" in fm
    assert sm != fm
    # New SHA → new marker, so a re-deploy at a different commit doesn't
    # dedup against the previous one.
    assert fsop.success_marker("deadbeef" + "0" * 33) != sm


def test_success_comment_carries_staging_link_and_marker():
    body = fsop.success_comment(_SHA, now=_NOW)
    assert "eb746c6" in body
    assert "agentfx-staging.up.railway.app" in body
    assert fsop.success_marker(_SHA) in body
    assert "AFX-70" in body  # posted-by footer


def test_failed_comment_has_run_link_marker_and_block_message():
    body = fsop.failed_comment(_SHA, run_url="https://github.com/w/r/actions/runs/1")
    assert "actions/runs/1" in body
    assert fsop.failed_marker(_SHA) in body
    assert "Blocked" in body
    assert "do not re-attempt" in body.lower()


# ─────────────────────────────────────────────────────────────────────
# Transition semantics
# ─────────────────────────────────────────────────────────────────────


def _issue(state_name: str, labels=()):
    return {
        "id": "u",
        "identifier": "AFX-70",
        "state": {"id": f"state-{state_name}", "name": state_name},
        "labels": {"nodes": [{"id": f"lbl-{n}", "name": n} for n in labels]},
    }


def _linear_with_gql(call_log, canned_responses=None):
    """Build a Linear with a stub _gql that records calls.

    `canned_responses` is a list of return values; each call consumes one.
    When exhausted, _gql returns {}.
    """
    linear = fsop.Linear("key")
    responses = list(canned_responses or [])

    def gql(query, variables):
        call_log.append({"query": query, "variables": variables})
        if responses:
            return responses.pop(0)
        return {}

    linear._gql = gql
    return linear


def test_transition_moves_in_staging_to_done():
    calls: list = []
    linear = _linear_with_gql(calls, canned_responses=[{}])  # issueUpdate return
    linear._issues["AFX-70"] = _issue("In Staging")

    old = linear.transition("AFX-70", "Done", allowed_from=fsop.TRANSITIONABLE)
    assert old == "In Staging"
    assert len(calls) == 1
    assert "issueUpdate" in calls[0]["query"]
    assert calls[0]["variables"]["stateId"] == fsop.STATE_IDS["Done"]
    # Cached state reflects the move so a follow-up is a no-op.
    assert linear._issues["AFX-70"]["state"]["name"] == "Done"


def test_transition_from_done_to_done_skipped_by_allowed_from_guard():
    """A Done ticket is not allowed to be re-transitioned (anti-loop guard)."""
    calls: list = []
    linear = _linear_with_gql(calls)
    linear._issues["AFX-70"] = _issue("Done")

    old = linear.transition("AFX-70", "Done", allowed_from=fsop.TRANSITIONABLE)
    # Done is not in TRANSITIONABLE → skipped BEFORE the idempotent check.
    assert old is None
    assert calls == []


def test_transition_idempotent_when_target_equals_current_in_transitionable():
    """Already In Staging, moving to In Staging again is a no-GQL-call no-op."""
    calls: list = []
    linear = _linear_with_gql(calls)
    linear._issues["AFX-70"] = _issue("In Staging")

    old = linear.transition("AFX-70", "In Staging", allowed_from=fsop.TRANSITIONABLE)
    assert old == "In Staging"
    # No issueUpdate call because current == target.
    assert calls == []


def test_transition_refuses_to_move_a_done_ticket_backwards():
    calls: list = []
    linear = _linear_with_gql(calls)
    linear._issues["AFX-70"] = _issue("Done")

    old = linear.transition("AFX-70", "Blocked", allowed_from=fsop.TRANSITIONABLE)
    assert old is None
    assert calls == []


def test_transition_from_in_progress_to_blocked_on_failed_deploy():
    calls: list = []
    linear = _linear_with_gql(calls, canned_responses=[{}])
    linear._issues["AFX-70"] = _issue("In Progress")

    old = linear.transition("AFX-70", "Blocked", allowed_from=fsop.TRANSITIONABLE)
    assert old == "In Progress"
    assert calls[0]["variables"]["stateId"] == fsop.STATE_IDS["Blocked"]


def test_transition_skips_cancelled_tickets():
    linear = _linear_with_gql([])
    linear._issues["AFX-70"] = _issue("Canceled")
    assert linear.transition("AFX-70", "Done", allowed_from=fsop.TRANSITIONABLE) is None


def test_transition_unknown_target_state_is_a_no_op():
    linear = _linear_with_gql([])
    linear._issues["AFX-70"] = _issue("In Staging")
    assert linear.transition("AFX-70", "Nonsense") is None


def test_transition_handles_linear_api_error_gracefully():
    linear = fsop.Linear("key")
    linear._issues["AFX-70"] = _issue("In Staging")

    def gql_raising(q, v):
        raise RuntimeError("Linear 503")

    linear._gql = gql_raising
    assert linear.transition("AFX-70", "Done", allowed_from=fsop.TRANSITIONABLE) is None
    # Cached state must not have been updated by the failed call.
    assert linear._issues["AFX-70"]["state"]["name"] == "In Staging"


def test_gql_surfaces_linear_400_error_body(monkeypatch):
    """Linear returns HTTP 400 with the real GraphQL error inside the
    response body. `_gql` must read the body and raise a RuntimeError that
    carries the actual message, so the caller's `warn()` shows what went
    wrong instead of just 'HTTP Error 400: Bad Request'. Pattern mirrors
    src/agentfx/integrations/linear_client.py:_gql. Observed live on
    2026-11-18: AFX-70's first run returned 400 for every ticket and the
    log only said 'Bad Request' — a day of guessing the cause.
    """
    import io
    import json as json_mod

    linear = fsop.Linear("key")

    class _StubHTTPError(fsop.urllib.error.HTTPError):
        def __init__(self, body: bytes):
            super().__init__(
                url=fsop.LINEAR_URL,
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(body),
            )

    canned_body = json_mod.dumps(
        {"errors": [{"message": "Variable $stateId has no value"}]}
    ).encode()

    def fake_urlopen(req, timeout):
        raise _StubHTTPError(canned_body)

    monkeypatch.setattr(fsop.urllib.request, "urlopen", fake_urlopen)
    try:
        linear._gql("mutation { x }", {})
    except RuntimeError as exc:
        msg = str(exc)
        assert "400" in msg
        assert "Variable $stateId has no value" in msg
    else:
        raise AssertionError("expected RuntimeError")


# ─────────────────────────────────────────────────────────────────────
# Label add
# ─────────────────────────────────────────────────────────────────────


def test_add_label_fetches_id_and_posts():
    calls: list = []
    linear = _linear_with_gql(
        calls,
        canned_responses=[
            {
                "issueLabels": {
                    "nodes": [{"id": "lbl-needs-wilson", "name": "needs-wilson"}]
                }
            },
            {},  # issueAddLabel return
        ],
    )
    linear._issues["AFX-70"] = _issue("Blocked")

    added = linear.add_label("AFX-70", "AFX", "needs-wilson")
    assert added is True
    assert len(calls) == 2
    assert "issueLabels" in calls[0]["query"]
    assert "issueAddLabel" in calls[1]["query"]


def test_add_label_is_no_op_when_already_present():
    calls: list = []
    linear = _linear_with_gql(calls)
    linear._issues["AFX-70"] = _issue("Blocked", labels=("needs-wilson",))

    added = linear.add_label("AFX-70", "AFX", "needs-wilson")
    assert added is False
    assert calls == []


def test_add_label_missing_in_linear_warns_and_skips():
    calls: list = []
    linear = _linear_with_gql(
        calls,
        canned_responses=[{"issueLabels": {"nodes": []}}],
    )
    linear._issues["AFX-70"] = _issue("Blocked")

    added = linear.add_label("AFX-70", "AFX", "mystery-label")
    assert added is False
    # Only the lookup happened — no issueAddLabel call.
    assert len(calls) == 1
    assert "issueLabels" in calls[0]["query"]


# ─────────────────────────────────────────────────────────────────────
# Comment dedup + Linear failure
# ─────────────────────────────────────────────────────────────────────


def test_comment_once_posts_on_first_call_and_dedups_on_second():
    calls: list = []
    linear = fsop.Linear("key")
    linear._issues["AFX-70"] = _issue("Done")

    def gql(q, v):
        calls.append({"query": q, "variables": v})
        if "commentCreate" in q:
            return {}
        return {
            "issue": {"comments": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        }

    linear._gql = gql
    body = "promoted <!-- mark -->"
    assert linear.comment_once("AFX-70", body, "<!-- mark -->") is True
    # Second call dedups — same marker in the cached bodies.
    assert linear.comment_once("AFX-70", body, "<!-- mark -->") is False


def test_comment_once_skips_when_marker_already_present():
    linear = fsop.Linear("key")
    linear._issues["AFX-70"] = _issue("Done")

    def gql(q, v):
        if "commentCreate" in q:
            raise AssertionError("should not post")
        return {
            "issue": {
                "comments": {
                    "nodes": [{"body": "already <!-- m -->"}],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        }

    linear._gql = gql
    assert linear.comment_once("AFX-70", "new <!-- m -->", "<!-- m -->") is False


# ─────────────────────────────────────────────────────────────────────
# End-to-end dispatch flow
# ─────────────────────────────────────────────────────────────────────


class _FakeLinear:
    """Records transition, add_label, and comment_once invocations so the
    dispatch flow can be verified without hitting GraphQL stubs."""

    def __init__(self, states: dict[str, str]):
        self.states = states
        self.calls: list[tuple] = []

    def transition(self, ident, target, *, allowed_from=None):
        old = self.states.get(ident)
        if allowed_from is not None and old not in allowed_from:
            return None
        self.states[ident] = target
        self.calls.append(("transition", ident, target))
        return old

    def add_label(self, ident, team_key, name):
        self.calls.append(("add_label", ident, name))
        return True

    def comment_once(self, ident, body, marker):
        self.calls.append(("comment", ident, marker))
        return True


def test_dispatch_success_moves_to_done_without_needs_wilson():
    fake = _FakeLinear({"AFX-70": "In Staging", "AFX-87": "In Staging"})
    fsop.dispatch(
        fake,
        ["AFX-70", "AFX-87"],
        sha=_SHA,
        outcome="success",
        run_url="",
        team_key="AFX",
        now=_NOW,
    )
    kinds = [c[0] for c in fake.calls]
    assert "add_label" not in kinds
    assert ("transition", "AFX-70", "Done") in fake.calls
    assert ("transition", "AFX-87", "Done") in fake.calls


def test_dispatch_failure_moves_to_blocked_and_adds_needs_wilson():
    fake = _FakeLinear({"AFX-70": "In Staging"})
    fsop.dispatch(
        fake,
        ["AFX-70"],
        sha=_SHA,
        outcome="failure",
        run_url="https://github.com/w/r/actions/runs/1",
        team_key="AFX",
        now=_NOW,
    )
    assert ("transition", "AFX-70", "Blocked") in fake.calls
    assert ("add_label", "AFX-70", fsop.NEEDS_WILSON) in fake.calls
    # Marker is the FAILED marker, not the success one, so a later success
    # deploy's dedupe won't collide with this one.
    comment_markers = [c[2] for c in fake.calls if c[0] == "comment"]
    assert comment_markers == [fsop.failed_marker(_SHA)]


def test_dispatch_skips_tickets_already_past_in_staging():
    fake = _FakeLinear({"AFX-70": "Done", "AFX-87": "In Staging"})
    fsop.dispatch(
        fake,
        ["AFX-70", "AFX-87"],
        sha=_SHA,
        outcome="success",
        run_url="",
        team_key="AFX",
        now=_NOW,
    )
    # AFX-70 was Done — never touched. AFX-87 went to Done.
    transitions = [c for c in fake.calls if c[0] == "transition"]
    assert ("transition", "AFX-70", "Done") not in transitions
    assert ("transition", "AFX-87", "Done") in transitions


def test_dispatch_back_to_back_markers_do_not_cross_contaminate():
    """A second promote at a different SHA uses a different marker, so
    comment_once on the second ticket won't be dedup'd by the first."""
    sha_a = "aaaaaaa" + "0" * 33
    sha_b = "bbbbbbb" + "0" * 33
    fake = _FakeLinear({"AFX-70": "In Staging"})
    fsop.dispatch(
        fake,
        ["AFX-70"],
        sha=sha_a,
        outcome="success",
        run_url="",
        team_key="AFX",
        now=_NOW,
    )
    # Reset state as if a second promote landed before the first's
    # transition to Done — in reality this wouldn't happen (ticket stays
    # Done), but the marker uniqueness guards it.
    fake.states["AFX-70"] = "In Staging"
    fsop.dispatch(
        fake,
        ["AFX-70"],
        sha=sha_b,
        outcome="success",
        run_url="",
        team_key="AFX",
        now=_NOW,
    )
    markers = [c[2] for c in fake.calls if c[0] == "comment"]
    assert markers[0] != markers[1]
    assert markers[0] == fsop.success_marker(sha_a)
    assert markers[1] == fsop.success_marker(sha_b)


def test_main_finds_afx_106_on_promote_143(monkeypatch):
    """AFX-113 regression, end to end: promote #143 (merge a7c78c3) named
    AFX-106 only in PR #137's title and #139/#141's branches, so the Done
    step found AFX-104 and AFX-50 but left AFX-106 In Staging."""
    commits = {
        "d755622": (
            "fix(paper,backtest): wick-mode broker SL/TP after min_hold (#137)",
            _pr(
                137,
                "fix(paper,backtest): AFX-106 wick-mode broker SL/TP after min_hold",
                "agent/AFX-106-wick-stop-fidelity",
            ),
        ),
        "c25dd35": (
            "fix(paper): remove loss-cap and drawdown auto-halts (AFX-104) (#138)",
            _pr(
                138, "fix(paper): remove paper auto-halts (AFX-104)", "agent/AFX-104-x"
            ),
        ),
        "95a76ee": (
            "fix(paper): address PR #137 review (#139)",
            _pr(139, "fix(paper): address #137 review", "agent/AFX-106-review-fixes"),
        ),
        "57a865b": (
            "fix(paper): claim each order atomically (AFX-50) (#140)",
            _pr(140, "fix(paper): one order per signal bar (AFX-50)", "devin/1-afx50"),
        ),
        "5c5d723": (
            "fix(paper): re-entry cutoff by broker fill time (#141)",
            _pr(
                141,
                "fix(paper): re-entry cutoff (#139 review)",
                "agent/AFX-106-review-fixes-2",
            ),
        ),
        "069abbd": (
            "Merge pull request #135 from widarecu/main",
            _pr(135, "Back-merge main → feat/agent-fleet (AFX-63)", "main"),
        ),
    }
    pulls = {sha: [pr] for sha, (_, pr) in commits.items()}
    pulls["a7c78c3"] = [
        _pr(143, "Promote feat/agent-fleet → main (AFX-1)", "feat/agent-fleet")
    ]

    def fake_gh_json(*args):
        path = args[1]
        if path == "repos/w/r/commits/a7c78c3":
            return {
                "sha": "a7c78c3",
                "commit": {
                    "message": "Merge pull request #143 from widarecu/feat/agent-fleet"
                },
                "parents": [{"sha": "c826fe1"}, {"sha": "a0e34f4"}],
            }
        if path == "repos/w/r/compare/c826fe1...a0e34f4":
            return {
                "commits": [
                    {"sha": sha, "commit": {"message": subject}}
                    for sha, (subject, _) in commits.items()
                ]
            }
        if path.endswith("/pulls"):
            return pulls.get(path.split("/")[-2], [])
        raise AssertionError(f"unexpected gh call: {path}")

    dispatched = []
    monkeypatch.setattr(fsop, "_gh_json", fake_gh_json)
    monkeypatch.setattr(fsop, "Linear", lambda api_key: object())
    monkeypatch.setattr(
        fsop, "dispatch", lambda linear, idents, **kw: dispatched.append((idents, kw))
    )
    monkeypatch.setenv("LINEAR_API_KEY", "test")

    assert fsop.main(["--sha", "a7c78c3", "--outcome", "success", "--repo", "w/r"]) == 0
    assert [idents for idents, _ in dispatched] == [["AFX-104", "AFX-50", "AFX-106"]]
    assert dispatched[0][1]["outcome"] == "success"
