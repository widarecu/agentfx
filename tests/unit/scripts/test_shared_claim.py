"""One shared claim and block for every taker (AFX-142).

Wilson's laptop (`afx start` with a key), keyless sessions (the
`afx-request.yml` round trip) and the fleet all call
`linear_delivery.start` / `block`. These tests race two takers for one
Ready ticket, retry a block, and pin the GitHub claim store and the
keyless request path, all without network.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import urllib.error
from pathlib import Path

import pytest
import yaml

from agentfx.integrations import claim_store as cs
from agentfx.integrations import linear_delivery as ld
from tests.unit.scripts.linear_fake import FakeClaims, FakeLinear

_REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "afx_cli_claim", _REPO / "scripts/afx.py"
)
assert _spec is not None and _spec.loader is not None
afx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = afx
_spec.loader.exec_module(afx)

WILSON = "wilson-user-id"
LAPTOP = cs.Taker("Wilson (Claude Code)", "branch `agent/AFX-7-x`")
FLEET = cs.Taker("AgentFX fleet PE", "branch `agent/afx-7-x-20261125`")


def _ready(fake: FakeLinear) -> None:
    fake.add("AFX-7", "Ready", assignee=WILSON)


def test_two_racing_takers_one_wins():
    """Both takers read Ready before either writes; exactly one gets the
    claim, the other is refused, and Linear shows one claim."""
    fake = FakeLinear()
    _ready(fake)
    claims = FakeClaims()
    both_read_ready = threading.Barrier(2, timeout=5)
    claims.before_create = both_read_ready.wait
    results: dict[str, ld.TransitionResult] = {}

    def take(taker: cs.Taker) -> None:
        client = ld.LinearClient("k", opener=fake.opener)
        results[taker.name] = ld.start(client, "AFX-7", claims=claims, taker=taker)

    threads = [threading.Thread(target=take, args=(t,)) for t in (LAPTOP, FLEET)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    winners = [name for name, r in results.items() if r.ok]
    assert len(results) == 2 and len(winners) == 1
    loser = next(r for r in results.values() if not r.ok)
    assert f"claimed by {winners[0]}" in loser.reason

    issue = fake.issues["AFX-7"]
    assert issue.state == "In Progress"
    [comment] = issue.comments
    assert f"Claimed by {winners[0]}" in comment
    assert issue.assignee == WILSON and issue.delegate is None
    assert {kind for kind, _ in fake.mutations} == {"state", "comment"}


def test_accountable_assignee_does_not_stop_the_fleet():
    fake = FakeLinear()
    _ready(fake)
    client = ld.LinearClient("k", opener=fake.opener)
    assert ld.start(client, "AFX-7", claims=FakeClaims(), taker=FLEET).ok
    assert fake.issues["AFX-7"].assignee == WILSON


def test_retried_block_posts_the_question_once():
    fake = FakeLinear()
    fake.add("AFX-7", "In Progress", assignee=WILSON)
    client = ld.LinearClient("k", opener=fake.opener)
    for _ in range(3):
        assert ld.block(client, "AFX-7", "Which pair?").ok
    issue = fake.issues["AFX-7"]
    assert issue.state == "Blocked"
    assert issue.labels == ["needs-wilson"]
    assert len(issue.comments) == 1 and "Which pair?" in issue.comments[0]
    assert issue.assignee == WILSON and issue.delegate is None


def test_retried_block_after_a_partial_failure_completes_once():
    """The first try moved the ticket and died before labelling."""
    fake = FakeLinear()
    fake.add("AFX-7", "Blocked")
    client = ld.LinearClient("k", opener=fake.opener)
    assert ld.block(client, "AFX-7", "Which pair?").ok
    assert ld.block(client, "AFX-7", "Which pair?").ok
    assert fake.issues["AFX-7"].labels == ["needs-wilson"]
    assert len(fake.issues["AFX-7"].comments) == 1


# ── GitHub claim store ───────────────────────────────────────────────


class FakeGitHubApi:
    """`urlopen` stand-in for the REST calls `GitHubClaims` makes."""

    def __init__(self) -> None:
        self.refs: dict[str, dict] = {"refs/heads/main": {"sha": "m" * 40}}
        self.tags: dict[str, dict] = {}
        self.statuses: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []

    def opener(self, req, timeout=None):
        method = req.get_method()
        path = req.full_url.split("/repos/widarecu/agentfx/", 1)[1]
        body = json.loads(req.data) if req.data else None
        self.calls.append((method, path))
        status, payload = self._route(method, path, body)
        if status >= 400:
            raise urllib.error.HTTPError(
                req.full_url,
                status,
                "err",
                {},
                io.BytesIO(json.dumps(payload).encode()),
            )
        return _Resp(json.dumps(payload).encode() if payload is not None else b"")

    def _route(self, method, path, body):
        if method == "GET" and path.startswith("git/ref/"):
            ref = "refs/" + path.removeprefix("git/ref/")
            if ref not in self.refs:
                return 404, {"message": "Not Found"}
            sha = self.refs[ref]["sha"]
            kind = "tag" if sha in self.tags else "commit"
            return 200, {"ref": ref, "object": {"sha": sha, "type": kind}}
        if method == "POST" and path == "git/tags":
            sha = f"t{len(self.tags):039d}"
            self.tags[sha] = body
            return 201, {"sha": sha}
        if method == "GET" and path.startswith("git/tags/"):
            return 200, self.tags[path.removeprefix("git/tags/")]
        if method == "POST" and path == "git/refs":
            if body["ref"] in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[body["ref"]] = {"sha": body["sha"]}
            return 201, {"ref": body["ref"]}
        if method == "DELETE" and path.startswith("git/refs/"):
            ref = "refs/" + path.removeprefix("git/refs/")
            if self.refs.pop(ref, None) is None:
                return 422, {"message": "Reference does not exist"}
            return 204, None
        if method == "POST" and path.startswith("statuses/"):
            self.statuses.setdefault(path.removeprefix("statuses/"), []).insert(0, body)
            return 201, body
        if method == "GET" and path.startswith("commits/"):
            sha = path.split("/")[1]
            return 200, self.statuses.get(sha, [])
        raise AssertionError((method, path))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def api() -> FakeGitHubApi:
    return FakeGitHubApi()


@pytest.fixture
def github(api) -> cs.GitHubClaims:
    return cs.GitHubClaims("gh-token", opener=api.opener)


class TestGitHubClaims:
    def test_first_create_wins_second_is_refused(self, api, github):
        assert github.create("afx-claim/AFX-7/1", LAPTOP) is True
        assert github.create("afx-claim/AFX-7/1", FLEET) is False
        assert github.holder("afx-claim/AFX-7/1") == LAPTOP

    def test_claim_is_an_annotated_tag_on_main(self, api, github):
        github.create("afx-claim/AFX-7/1", LAPTOP)
        [tag] = api.tags.values()
        assert tag["object"] == "m" * 40 and tag["type"] == "commit"
        assert cs.Taker.parse(tag["message"]) == LAPTOP

    def test_absent_claim_has_no_holder(self, github):
        assert github.holder("afx-claim/AFX-7/1") is None

    def test_other_http_errors_raise(self, api, github, monkeypatch):
        monkeypatch.setattr(api, "_route", lambda *a: (500, {"message": "boom"}))
        with pytest.raises(cs.ClaimStoreError):
            github.create("afx-claim/AFX-7/1", LAPTOP)

    def test_empty_token_rejected(self):
        with pytest.raises(cs.ClaimStoreError):
            cs.GitHubClaims("")

    def test_tag_name_has_one_slot_per_ready_period(self):
        assert (
            cs.claim_tag("AFX-7", "2026-11-24T06:37:52.868Z")
            == "afx-claim/AFX-7/20261124063752868"
        )

    def test_real_store_serializes_linear_claims(self, api, github):
        fake = FakeLinear()
        _ready(fake)
        client = ld.LinearClient("k", opener=fake.opener)
        assert ld.start(client, "AFX-7", claims=github, taker=LAPTOP).ok
        assert not ld.start(client, "AFX-7", claims=github, taker=FLEET).ok
        assert len(fake.issues["AFX-7"].comments) == 1


# ── Keyless requests ─────────────────────────────────────────────────


def _args(*argv: str):
    return afx._parser().parse_args(list(argv))


class TestKeylessRequest:
    def test_message_round_trips_without_naming_the_ticket(self):
        args = _args("start", "AFX-7", "--taker", "Devin", "--link", "https://s/1")
        message = afx.request_message(args)
        assert "AFX-7" not in message  # Linear must not link the request
        parsed = afx.parse_request(message)
        assert (parsed.command, parsed.issue, parsed.taker, parsed.link) == (
            "start",
            "AFX-7",
            "Devin",
            "https://s/1",
        )

    def test_block_message_carries_the_question(self):
        parsed = afx.parse_request(afx.request_message(_args("block", "AFX-7", "Q?")))
        assert (parsed.command, parsed.question) == ("block", "Q?")

    @pytest.mark.parametrize(
        "message", ["", 'x\n\n{"command": "rm"}', 'x\n\n{"command": "start"}']
    )
    def test_bad_requests_rejected(self, message):
        with pytest.raises(ValueError):
            afx.parse_request(message)

    def _round_trip(self, api, github, fake, *argv):
        """Script pushes the request, the workflow runs it, the script
        reads the verdict."""
        sha = "r" * 40
        api.refs["refs/heads/afx-request/abc"] = {"sha": sha}

        def push(message):
            env = {
                "REQUEST_SHA": sha,
                "REQUEST_REF": "refs/heads/afx-request/abc",
                "REQUEST_MESSAGE": message,
                "RUN_URL": "https://run/1",
            }
            client = ld.LinearClient("k", opener=fake.opener)
            assert afx.run_request(client=client, github=github, env=env) == 0
            return sha

        code = afx.keyless(_args(*argv), push=push, github=github, sleep=lambda s: None)
        assert "refs/heads/afx-request/abc" not in api.refs
        return code

    def test_keyless_start_claims_through_the_shared_claim(self, api, github):
        fake = FakeLinear()
        _ready(fake)
        assert (
            self._round_trip(api, github, fake, "start", "AFX-7", "--taker", "Devin")
            == afx.EXIT_OK
        )
        assert fake.issues["AFX-7"].state == "In Progress"
        assert "Claimed by Devin" in fake.issues["AFX-7"].comments[0]
        assert any(r.startswith("refs/tags/afx-claim/AFX-7/") for r in api.refs)

    def test_keyless_start_loses_to_an_earlier_taker(self, api, github):
        fake = FakeLinear()
        _ready(fake)
        client = ld.LinearClient("k", opener=fake.opener)
        ld.start(client, "AFX-7", claims=github, taker=FLEET)
        code = self._round_trip(api, github, fake, "start", "AFX-7", "--taker", "Devin")
        assert code == afx.EXIT_REFUSED
        assert len(fake.issues["AFX-7"].comments) == 1

    def test_keyless_block_retry_posts_once(self, api, github):
        fake = FakeLinear()
        fake.add("AFX-7", "In Progress")
        for _ in range(2):
            assert (
                self._round_trip(api, github, fake, "block", "AFX-7", "Which pair?")
                == afx.EXIT_OK
            )
        assert fake.issues["AFX-7"].state == "Blocked"
        assert len(fake.issues["AFX-7"].comments) == 1

    def test_no_verdict_times_out_as_error(self, api, github, capsys):
        args = _args("start", "AFX-7", "--wait", "20")
        code = afx.keyless(
            args, push=lambda m: "s" * 40, github=github, sleep=lambda s: None
        )
        assert code == afx.EXIT_ERROR
        assert "check the ticket in Linear" in capsys.readouterr().err

    def test_request_errors_are_reported_on_the_commit(self, api, github):
        env = {"REQUEST_SHA": "e" * 40, "REQUEST_REF": "", "REQUEST_MESSAGE": "junk"}
        assert afx.run_request(client=None, github=github, env=env) == afx.EXIT_ERROR
        assert api.statuses["e" * 40][0]["state"] == "error"


def test_request_workflow_runs_main_code_with_least_privilege():
    wf = yaml.safe_load((_REPO / ".github/workflows/afx-request.yml").read_text())
    on = wf.get("on") or wf[True]
    assert on == {"push": {"branches": ["afx-request/**"]}}
    assert wf["permissions"] == {}
    job = wf["jobs"]["run"]
    assert job["permissions"] == {"contents": "write", "statuses": "write"}
    checkout, run = job["steps"]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] is False
    assert run["run"] == "python3 scripts/afx.py request run"
    assert "${{" not in run["run"]  # untrusted values reach the shell via env only
