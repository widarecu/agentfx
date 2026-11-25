"""Reconcile ambiguous GitHub ref writes before replaying them."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from collections.abc import Callable, Sequence

_LOG = logging.getLogger(__name__)
_RETRY_DELAYS = (1, 2, 4)
_HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b")
_TRANSPORT_ERROR = re.compile(
    r"unexpected end of json input|\beof\b|connection (?:reset|refused)|"
    r"broken pipe|timeout|timed out|temporary failure in name resolution",
    re.IGNORECASE,
)


class GitHubError(RuntimeError):
    def __init__(
        self, message: str, status: int | None = None, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def run_gh(
    args: list[str],
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> str:
    cmd = ["gh", *args]
    proc = (
        runner(cmd)
        if runner is not None
        else subprocess.run(cmd, capture_output=True, text=True, check=False)
    )
    if proc.returncode == 0:
        return proc.stdout

    detail = (proc.stderr or proc.stdout or "").strip()
    match = _HTTP_STATUS.search(detail)
    status = int(match[1]) if match else None
    # Rate limits need GitHub's reset interval, not this short backoff.
    retryable = proc.returncode == 1 and (
        status == 408
        or (status is not None and 500 <= status <= 599)
        or (status is None and (not detail or bool(_TRANSPORT_ERROR.search(detail))))
    )
    raise GitHubError(
        detail or f"gh exited {proc.returncode}", status, retryable=retryable
    )


class RefWriter:
    """Apply a mutation only once unless the ref still has its expected value."""

    def __init__(self, repo: str, run: Callable[[list[str]], str] = run_gh) -> None:
        self._run = run
        self._base = f"repos/{repo}/git"

    def put(
        self, ref: str, sha: str, *, expected: str | None, force: bool = False
    ) -> None:
        if expected is None:
            args = [
                "-X",
                "POST",
                f"{self._base}/refs",
                "-f",
                f"ref={ref}",
                "-f",
                f"sha={sha}",
            ]
        else:
            args = [
                "-X",
                "PATCH",
                f"{self._base}/{ref}",
                "-f",
                f"sha={sha}",
                "-F",
                f"force={str(force).lower()}",
            ]
        self._write(ref, args, expected, sha)

    def delete(self, ref: str, *, expected: str) -> None:
        self._write(ref, ["-X", "DELETE", f"{self._base}/{ref}"], expected, None)

    def _read(self, ref: str) -> str | None:
        try:
            out = self._run(
                ["api", "-X", "GET", f"{self._base}/ref/{ref.removeprefix('refs/')}"]
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        try:
            sha = json.loads(out)["object"]["sha"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubError(
                f"invalid GitHub response for {ref}", retryable=True
            ) from exc
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitHubError(f"invalid GitHub SHA for {ref}", retryable=True)
        return sha

    def _write(
        self, ref: str, args: list[str], expected: str | None, desired: str | None
    ) -> None:
        last_write_error = None
        for delay in (*_RETRY_DELAYS, None):
            if last_write_error is not None:
                try:
                    current = self._read(ref)
                except GitHubError as exc:
                    if delay is None or not exc.retryable:
                        raise GitHubError(
                            f"could not verify {ref} after {last_write_error}: {exc}",
                            exc.status,
                        ) from exc
                    _LOG.warning("Could not verify %s; rechecking in %ss", ref, delay)
                    time.sleep(delay)
                    continue
                if current == desired:
                    return
                if current != expected:
                    raise GitHubError(
                        f"{ref} changed from {expected} to {current}; refusing to retry"
                    ) from last_write_error
            # After the last write's error, only verify; don't write again.
            if delay is None:
                assert last_write_error is not None
                raise last_write_error
            try:
                self._run(["api", *args])
                return
            except GitHubError as exc:
                if not exc.retryable:
                    raise
                last_write_error = exc
                _LOG.warning(
                    "GitHub %s failed for %s; rechecking in %ss", args[1], ref, delay
                )
                time.sleep(delay)
