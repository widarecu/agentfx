"""Exercise the deploy tag retry protocol with a fake gh process."""

import json
import subprocess
from collections import deque

import pytest

from agentfx.integrations import github_refs as gr
from agentfx.integrations.deploy_sync import GhCli, record_tag, synced_tag

OLD = "a" * 40
TARGET = "b" * 40
OTHER = "c" * 40
REF = "refs/tags/deployed/staging"

UNAVAILABLE = (1, "", "gh: Service Unavailable (HTTP 503)")
NOT_FOUND = (1, "", "gh: Not Found (HTTP 404)")
OK = (0, "", "")


def ref_at(sha):
    return 0, json.dumps({"object": {"sha": sha}}), ""


class ScriptedGh:
    def __init__(self):
        self.calls = []
        self.delays = []
        self.responses = deque()

    def process(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, *self.responses.popleft())

    def run(self, args):
        return gr.run_gh(args, runner=self.process)

    @property
    def methods(self):
        return [cmd[3] for cmd in self.calls]


@pytest.fixture
def gh(monkeypatch):
    gh = ScriptedGh()
    monkeypatch.setattr(gr.time, "sleep", gh.delays.append)
    return gh


@pytest.mark.parametrize("name", [record_tag(TARGET), synced_tag(TARGET)])
@pytest.mark.parametrize("already_applied", [False, True])
def test_sync_creates_immutable_refs_once_after_ambiguous_write(
    gh, name, already_applied
):
    gh.responses.extend(
        [UNAVAILABLE, ref_at(TARGET)]
        if already_applied
        else [UNAVAILABLE, NOT_FOUND, OK]
    )

    GhCli("o/r", runner=gh.run).create_tag_ref(name, TARGET)

    methods = ["POST", "GET"]
    assert gh.methods == methods + ([] if already_applied else ["POST"])
    assert gh.calls[1][-1] == f"repos/o/r/git/ref/tags/{name}"
    assert gh.delays == [1]


@pytest.mark.parametrize("already_applied", [False, True])
def test_sync_prunes_refs_once_after_ambiguous_delete(gh, already_applied):
    name = record_tag(TARGET)
    gh.responses.extend(
        [UNAVAILABLE, NOT_FOUND] if already_applied else [UNAVAILABLE, ref_at(OLD), OK]
    )

    GhCli("o/r", runner=gh.run).delete_tag_ref(name, OLD)

    methods = ["DELETE", "GET"]
    assert gh.methods == methods + ([] if already_applied else ["DELETE"])


def test_sync_does_not_delete_a_ref_replaced_during_retry(gh):
    gh.responses.extend([UNAVAILABLE, ref_at(OTHER)])

    with pytest.raises(gr.GitHubError, match="changed .* refusing to retry"):
        GhCli("o/r", runner=gh.run).delete_tag_ref(record_tag(TARGET), OLD)

    assert gh.methods == ["DELETE", "GET"]


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("current", [NOT_FOUND, ref_at(OTHER)])
def test_patch_does_not_retry_if_the_ref_changed(gh, force, current):
    gh.responses.extend([UNAVAILABLE, current])

    with pytest.raises(gr.GitHubError, match="changed .* refusing to retry"):
        gr.RefWriter("o/r", gh.run).put(REF, TARGET, expected=OLD, force=force)

    assert gh.methods == ["PATCH", "GET"]


def test_failed_read_cannot_cause_an_unchecked_write(gh):
    gh.responses.extend([UNAVAILABLE, UNAVAILABLE, (0, "{", ""), ref_at(OLD)])

    with pytest.raises(gr.GitHubError, match="HTTP 503"):
        gr.RefWriter("o/r", gh.run).put(REF, TARGET, expected=OLD)

    assert gh.methods == ["PATCH", "GET", "GET", "GET"]
    assert gh.delays == [1, 2, 4]


@pytest.mark.parametrize(
    "failure",
    [
        "stream error: stream ID 1; INTERNAL_ERROR; received from peer",
        "http2: client connection lost",
        "http2: server sent GOAWAY and closed the connection",
    ],
)
def test_http2_disconnect_checks_if_the_patch_already_landed(gh, failure):
    gh.responses.extend(
        [(1, "", f'Patch "https://api.github.com/": {failure}'), ref_at(TARGET)]
    )

    gr.RefWriter("o/r", gh.run).put(REF, TARGET, expected=OLD)

    assert gh.methods == ["PATCH", "GET"]


@pytest.mark.parametrize("last_sha", [OLD, TARGET])
def test_persistent_write_errors_are_bounded_and_the_last_write_is_checked(
    gh, last_sha
):
    gh.responses.extend(
        [UNAVAILABLE, ref_at(OLD)] * 2 + [UNAVAILABLE, ref_at(last_sha)]
    )
    writer = gr.RefWriter("o/r", gh.run)

    if last_sha == TARGET:
        writer.put(REF, TARGET, expected=OLD)
    else:
        with pytest.raises(gr.GitHubError, match="HTTP 503"):
            writer.put(REF, TARGET, expected=OLD)

    assert gh.methods == ["PATCH", "GET"] * 3
    assert gh.delays == [1, 2, 4]


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429])
def test_permanent_http_errors_are_not_retried(gh, status):
    gh.responses.append((1, "", f"gh: Rejected (HTTP {status})"))

    with pytest.raises(gr.GitHubError, match=f"HTTP {status}"):
        gr.RefWriter("o/r", gh.run).put(REF, TARGET, expected=OLD)

    assert gh.methods == ["PATCH"]
    assert gh.delays == []
