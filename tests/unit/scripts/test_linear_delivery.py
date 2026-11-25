"""Shared Linear delivery client and transitions (AFX-118).

Runs the real `LinearClient` against an in-memory fake endpoint
(`linear_fake.FakeLinear`), so request building, errors, pagination and
name-based lookups are covered without any network call.
"""

from __future__ import annotations

import urllib.error

import pytest

from agentfx.integrations import linear_delivery as ld
from agentfx.integrations.claim_store import Taker
from tests.unit.scripts.linear_fake import FakeClaims, FakeLinear


@pytest.fixture
def fake() -> FakeLinear:
    return FakeLinear()


@pytest.fixture
def client(fake: FakeLinear) -> ld.LinearClient:
    return ld.LinearClient("lin_test", opener=fake.opener)


def _states(fake: FakeLinear) -> list[str]:
    return [m[1]["to"] for m in fake.mutations if m[0] == "state"]


class TestClient:
    def test_empty_key_rejected(self):
        with pytest.raises(ld.LinearError):
            ld.LinearClient("")

    def test_from_env_reads_key(self, monkeypatch, fake):
        monkeypatch.setenv("LINEAR_API_KEY", "lin_env")
        assert ld.LinearClient.from_env(opener=fake.opener)._api_key == "lin_env"

    def test_state_lookup_paginates_by_name(self, fake, client):
        # page_size=2 spreads the 10 states over 5 pages; "Done" is on page 5.
        assert client.state_id("team-afx", "Done") == "state-Done"
        assert sum("states(" in q for q in fake.calls) == 5

    def test_state_lookup_is_cached(self, fake, client):
        client.state_id("team-afx", "Ready")
        before = len(fake.calls)
        client.state_id("team-afx", "Done")
        assert len(fake.calls) == before

    def test_missing_state_raises(self, client):
        with pytest.raises(ld.NotFoundError):
            client.state_id("team-afx", "Shipped")

    def test_missing_label_raises(self, client):
        with pytest.raises(ld.NotFoundError):
            client.label_id("team-afx", "no-such-label")

    def test_missing_issue_raises(self, client):
        with pytest.raises(ld.NotFoundError):
            client.issue("AFX-404")

    def test_comment_bodies_paginate(self, fake, client):
        fake.add("AFX-1", "Ready", comments=["a", "b", "c", "d", "e"])
        assert client.comment_bodies("uuid-AFX-1") == ["a", "b", "c", "d", "e"]

    def test_comment_once_dedups_on_marker(self, fake, client):
        fake.add("AFX-1", "Ready", comments=["x", "y", "<!-- m --> old"])
        assert client.comment_once("uuid-AFX-1", "new", "<!-- m -->") is False
        assert client.comment_once("uuid-AFX-1", "new", "<!-- n -->") is True
        assert fake.issues["AFX-1"].comments[-1] == "<!-- n -->\nnew"

    def test_http_error_surfaces_graphql_detail(self, fake, client):
        fake.http_error = (400, {"errors": [{"message": "bad field"}]})
        with pytest.raises(ld.LinearError, match="bad field"):
            client.issue("AFX-1")

    def test_network_error_wrapped(self, fake, client):
        fake.fail_with = urllib.error.URLError("dns")
        with pytest.raises(ld.LinearError, match="unreachable"):
            client.issue("AFX-1")

    def test_graphql_errors_raise(self, client, monkeypatch):
        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"errors": [{"message": "nope"}]}'

        c = ld.LinearClient("k", opener=lambda req, timeout=None: _R())
        with pytest.raises(ld.LinearError, match="nope"):
            c.viewer_id()

    def test_request_carries_auth_header(self, client):
        seen = {}

        def _opener(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            raise urllib.error.URLError("stop")

        c = ld.LinearClient("lin_secret", opener=_opener)
        with pytest.raises(ld.LinearError):
            c.issue("AFX-1")
        assert seen["auth"] == "lin_secret"


ME = Taker("Devin", "https://app.devin.ai/sessions/1")
OTHER = Taker("AgentFX fleet PE", "branch `agent/afx-1-x`")


def _start(client, ident, taker=ME, claims=None):
    return ld.start(client, ident, claims=claims or FakeClaims(), taker=taker)


class TestStart:
    def test_ready_moves_to_in_progress(self, fake, client):
        fake.add("AFX-1", "Ready")
        result = _start(client, "AFX-1")
        assert result.ok and result.changed
        assert fake.issues["AFX-1"].state == "In Progress"

    def test_claim_comment_names_the_taker(self, fake, client):
        fake.add("AFX-1", "Ready")
        _start(client, "AFX-1")
        [comment] = fake.issues["AFX-1"].comments
        assert "Claimed by Devin" in comment and ME.link in comment

    def test_repeat_start_by_another_taker_is_refused(self, fake, client):
        fake.add("AFX-1", "Ready")
        claims = FakeClaims()
        _start(client, "AFX-1", claims=claims)
        again = _start(client, "AFX-1", OTHER, claims)
        assert again.ok is False and again.changed is False
        assert "claimed by Devin" in again.reason
        assert _states(fake) == ["In Progress"]

    def test_own_retry_is_ok_without_a_second_comment(self, fake, client):
        fake.add("AFX-1", "Ready")
        claims = FakeClaims()
        _start(client, "AFX-1", claims=claims)
        again = _start(client, "AFX-1", claims=claims)
        assert again.ok is True and again.changed is False
        assert len(fake.issues["AFX-1"].comments) == 1

    def test_in_progress_without_a_claim_is_refused(self, fake, client):
        fake.add("AFX-1", "In Progress")
        assert _start(client, "AFX-1").ok is False
        assert fake.mutations == []

    def test_loser_finishes_a_winner_that_never_wrote_linear(self, fake, client):
        """The winner took the tag and died: the ticket must not sit in
        Ready with nobody able to take it."""
        fake.add("AFX-1", "Ready")
        claims = FakeClaims()
        claims.tags["afx-claim/AFX-1/20261120000000000"] = OTHER
        result = _start(client, "AFX-1", claims=claims)
        assert result.ok is False
        assert fake.issues["AFX-1"].state == "In Progress"
        assert fake.issues["AFX-1"].comments == []

    def test_back_in_ready_gets_a_fresh_claim(self, fake, client):
        """A ticket Wilson puts back into Ready can be claimed again."""
        issue = fake.add("AFX-1", "Ready")
        claims = FakeClaims()
        _start(client, "AFX-1", claims=claims)
        issue.state = "Ready"
        issue.history.append(
            {"createdAt": "2026-11-24T00:00:00Z", "toState": {"name": "Ready"}}
        )
        assert _start(client, "AFX-1", OTHER, claims).ok is True
        assert len(claims.tags) == 2

    @pytest.mark.parametrize(
        "state", ["Triage", "Backlog", "In Review", "Blocked", "Done", "Canceled"]
    )
    def test_wrong_state_refused_without_writes(self, fake, client, state):
        fake.add("AFX-1", state)
        assert _start(client, "AFX-1").ok is False
        assert fake.mutations == []

    def test_missing_issue_raises(self, client):
        with pytest.raises(ld.NotFoundError):
            _start(client, "AFX-404")

    def test_missing_target_state_raises(self):
        fake = FakeLinear(states=["Ready", "Done"])
        fake.add("AFX-1", "Ready")
        c = ld.LinearClient("k", opener=fake.opener)
        with pytest.raises(ld.NotFoundError):
            _start(c, "AFX-1")
        assert fake.mutations == []

    def test_api_failure_raises(self, fake, client):
        fake.fail_with = urllib.error.URLError("down")
        with pytest.raises(ld.LinearError):
            _start(client, "AFX-1")


class TestBlock:
    @pytest.mark.parametrize("state", ["In Progress", "In Review"])
    def test_active_ticket_blocked_labelled_and_asked(self, fake, client, state):
        fake.add("AFX-1", state)
        result = ld.block(client, "AFX-1", "Which pair?")
        issue = fake.issues["AFX-1"]
        assert result.ok and result.changed
        assert issue.state == "Blocked"
        assert "needs-wilson" in issue.labels
        assert len(issue.comments) == 1 and "Which pair?" in issue.comments[0]

    def test_repeat_block_is_noop(self, fake, client):
        fake.add("AFX-1", "In Progress")
        ld.block(client, "AFX-1", "Which pair?")
        writes = len(fake.mutations)
        again = ld.block(client, "AFX-1", "Which pair?")
        assert again.ok is True and again.changed is False
        assert len(fake.mutations) == writes
        assert len(fake.issues["AFX-1"].comments) == 1

    def test_new_question_on_blocked_ticket_posts_once(self, fake, client):
        fake.add("AFX-1", "Blocked", labels=["needs-wilson"])
        ld.block(client, "AFX-1", "Second question")
        ld.block(client, "AFX-1", "Second question")
        assert [m[0] for m in fake.mutations] == ["comment"]

    @pytest.mark.parametrize(
        "state", ["Triage", "Backlog", "Ready", "Done", "Canceled"]
    )
    def test_inactive_ticket_refused(self, fake, client, state):
        fake.add("AFX-1", state)
        assert ld.block(client, "AFX-1", "q").ok is False
        assert fake.mutations == []

    def test_never_moves_to_ready(self, fake, client):
        for i, state in enumerate(["In Progress", "In Review", "Blocked", "Ready"]):
            fake.add(f"AFX-{i}", state)
            ld.block(client, f"AFX-{i}", "q")
        assert "Ready" not in _states(fake)

    def test_empty_question_rejected(self, fake, client):
        fake.add("AFX-1", "In Progress")
        with pytest.raises(ValueError):
            ld.block(client, "AFX-1", "   ")

    def test_missing_needs_wilson_label_raises(self):
        fake = FakeLinear(labels=["agent-filed"])
        fake.add("AFX-1", "In Progress")
        c = ld.LinearClient("k", opener=fake.opener)
        with pytest.raises(ld.NotFoundError):
            ld.block(c, "AFX-1", "q")


class TestReleaseOwnClaim:
    def test_own_claim_without_workspace_goes_back_to_ready(self, fake, client):
        fake.add("AFX-1", "Ready")
        _start(client, "AFX-1")
        result = ld.release_own_claim(client, "AFX-1", workspace_started=False)
        assert result.ok and fake.issues["AFX-1"].state == "Ready"

    def test_started_workspace_is_never_rolled_back(self, fake, client):
        fake.add("AFX-1", "Ready")
        _start(client, "AFX-1")
        result = ld.release_own_claim(client, "AFX-1", workspace_started=True)
        assert result.ok is False
        assert fake.issues["AFX-1"].state == "In Progress"

    def test_other_actors_claim_is_kept(self, fake, client):
        fake.add(
            "AFX-1",
            "In Progress",
            history=[
                {
                    "createdAt": "2026-11-23T00:00:00Z",
                    "actor": {"id": "user-pe"},
                    "toState": {"name": "In Progress"},
                },
                {
                    "createdAt": "2026-11-23T01:00:00Z",
                    "actor": {"id": "user-devin"},
                    "toState": {"name": "In Progress"},
                },
            ],
        )
        result = ld.release_own_claim(client, "AFX-1", workspace_started=False)
        assert result.ok is False and "another actor" in result.reason
        assert fake.mutations == []

    def test_no_claim_history_is_kept(self, fake, client):
        fake.add("AFX-1", "In Progress")
        assert (
            ld.release_own_claim(client, "AFX-1", workspace_started=False).ok is False
        )
        assert fake.mutations == []

    def test_not_in_progress_is_left_alone(self, fake, client):
        fake.add("AFX-1", "In Review")
        assert (
            ld.release_own_claim(client, "AFX-1", workspace_started=False).ok is False
        )
        assert fake.mutations == []
