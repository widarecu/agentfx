"""Unit tests for `agentfx.agents.fleet.log_buffer`.

Covers:
- Only WARNING+ records are captured (DEBUG/INFO filtered).
- Ring buffer bounds (`maxlen`) enforced.
- Snapshot windowing (since / recent) + limit.
- Handler attach/detach is idempotent + leak-free.
- Exception info is captured in `exc_text`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from agentfx.agents.fleet.log_buffer import CapturedLog, LogBuffer

pytestmark = pytest.mark.unit


class TestLogCapture:
    def _attached_buffer(self) -> LogBuffer:
        """Return a LogBuffer attached to the root logger; tests
        must detach it before returning to avoid handler leaks
        across the test session."""
        buf = LogBuffer(maxlen=50)
        buf.attach_global_handler()
        return buf

    def test_captures_warnings_and_errors(self):
        buf = self._attached_buffer()
        try:
            logger = logging.getLogger("tests.log_buffer.capture")
            logger.warning("w-msg")
            logger.error("e-msg")
            logger.critical("c-msg")

            snapshot = buf.snapshot_recent(minutes=1)
            levels = {r.level for r in snapshot}
            assert "WARNING" in levels
            assert "ERROR" in levels
            assert "CRITICAL" in levels
            messages = {r.message for r in snapshot}
            assert {"w-msg", "e-msg", "c-msg"}.issubset(messages)
        finally:
            buf.detach_global_handler()

    def test_filters_out_info_and_debug(self):
        buf = self._attached_buffer()
        try:
            logger = logging.getLogger("tests.log_buffer.filter")
            logger.debug("d-msg")
            logger.info("i-msg")
            logger.warning("w-msg")

            snapshot = buf.snapshot_recent(minutes=1)
            messages = {r.message for r in snapshot}
            assert "d-msg" not in messages
            assert "i-msg" not in messages
            assert "w-msg" in messages
        finally:
            buf.detach_global_handler()

    def test_ring_buffer_caps_size(self):
        buf = self._attached_buffer()
        try:
            logger = logging.getLogger("tests.log_buffer.cap")
            for i in range(100):
                logger.warning("msg-%d", i)

            # maxlen=50 → only the last 50 survive.
            snapshot = buf.snapshot_recent(minutes=1, limit=200)
            # Not everything emitted this call will land in snapshot
            # because other tests also leak records; filter to ours.
            ours = [r for r in snapshot if r.message.startswith("msg-")]
            assert len(ours) <= 50
            # Last 50 by index means msg-99 is in, msg-0 is not.
            assert any(r.message == "msg-99" for r in ours)
            assert all(r.message != "msg-0" for r in ours)
        finally:
            buf.detach_global_handler()

    def test_captures_exception_text(self):
        buf = self._attached_buffer()
        try:
            logger = logging.getLogger("tests.log_buffer.exc")
            try:
                raise ValueError("deliberate test error")
            except ValueError:
                logger.exception("something broke")

            snapshot = buf.snapshot_recent(minutes=1)
            exc_records = [
                r for r in snapshot if r.message == "something broke" and r.exc_text
            ]
            assert exc_records, "Exception record not captured"
            # Default handler formatter is "%(message)s" — so exc_text
            # is just the message when no `exc_info` was emitted.
            # We assert `exc_text` is non-None, which only happens
            # when `record.exc_info` was set.
            assert exc_records[0].exc_text is not None
        finally:
            buf.detach_global_handler()


class TestSnapshotWindow:
    def test_since_filter_excludes_old_records(self):
        buf = LogBuffer()
        now = datetime.now(UTC)
        buf.append(
            CapturedLog(
                timestamp=now - timedelta(hours=3),
                level="WARNING",
                logger_name="old",
                message="old-msg",
            )
        )
        buf.append(
            CapturedLog(
                timestamp=now - timedelta(minutes=30),
                level="WARNING",
                logger_name="new",
                message="new-msg",
            )
        )

        recent = buf.snapshot_since(since=now - timedelta(hours=1))
        assert [r.message for r in recent] == ["new-msg"]

    def test_limit_caps_returned_count(self):
        buf = LogBuffer()
        now = datetime.now(UTC)
        for i in range(20):
            buf.append(
                CapturedLog(
                    timestamp=now - timedelta(seconds=i),
                    level="WARNING",
                    logger_name="l",
                    message=f"m-{i}",
                )
            )
        got = buf.snapshot_recent(minutes=10, limit=5)
        assert len(got) == 5

    def test_snapshot_sorted_newest_first(self):
        buf = LogBuffer()
        now = datetime.now(UTC)
        for i in [5, 1, 10, 2]:
            buf.append(
                CapturedLog(
                    timestamp=now - timedelta(minutes=i),
                    level="WARNING",
                    logger_name="l",
                    message=f"m-{i}",
                )
            )
        got = buf.snapshot_recent(minutes=60)
        timestamps = [r.timestamp for r in got]
        assert timestamps == sorted(timestamps, reverse=True)


class TestHandlerLifecycle:
    def test_attach_is_idempotent(self):
        buf = LogBuffer()
        root = logging.getLogger()
        before = len(root.handlers)
        buf.attach_global_handler()
        buf.attach_global_handler()
        try:
            # Only one handler added despite two attach calls.
            assert len(root.handlers) == before + 1
        finally:
            buf.detach_global_handler()

    def test_detach_removes_handler(self):
        buf = LogBuffer()
        root = logging.getLogger()
        before = len(root.handlers)
        buf.attach_global_handler()
        buf.detach_global_handler()
        # Back to the starting handler count.
        assert len(root.handlers) == before


class TestPromptSerialization:
    def test_to_prompt_dict_truncates_long_tracebacks(self):
        long_tb = "x" * 5000
        rec = CapturedLog(
            timestamp=datetime.now(UTC),
            level="ERROR",
            logger_name="l",
            message="boom",
            exc_text=long_tb,
        )
        out = rec.to_prompt_dict()
        assert len(out["exc"]) == 2000
        assert out["msg"] == "boom"
