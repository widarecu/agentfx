"""In-memory log buffer for the Agent Fleet's Quant Analyst.

Why not read docker logs or files?
- UAT is Docker, staging + prod are Railway. Each has its own log
  retrieval story. An in-process ring buffer works identically
  everywhere, with zero new dependencies.
- Logs are already in RAM — a Python `logging.Handler` sees every
  record the moment it is emitted. Capturing them to a bounded
  deque is nearly free (CPU + memory).

What this captures:
- Only WARNING+ records (DEBUG and INFO are noise for anomaly
  analysis, and keeping them would blow the buffer size).
- All loggers in the backend process — the handler attaches to
  the root logger during orchestrator startup.
- Records are stored as plain-dict snapshots (level, name,
  message, exc_text, timestamp) so they serialise cleanly into
  the QA prompt without pulling heavy LogRecord internals in.

Buffer sizing:
- 1000 records at WARNING+ is a lot for a healthy process — a
  burst of 200 ERROR tracebacks is still visible in full, and
  older records roll out of view (acceptable: QA analyses recent
  anomalies, not the archaeology of them).

Thread safety:
- `collections.deque(maxlen=N)` has thread-safe append / pop
  semantics for the single-element operations we use. For bulk
  snapshotting we take a lock to keep the view consistent.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

_MAX_RECORDS = 1000


@dataclass(frozen=True)
class CapturedLog:
    """A snapshot of one log record, LLM-ready."""

    timestamp: datetime
    level: str  # "WARNING" / "ERROR" / "CRITICAL"
    logger_name: str
    message: str
    exc_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact dict form for the QA prompt."""
        out: dict[str, Any] = {
            "ts": self.timestamp.isoformat(),
            "level": self.level,
            "logger": self.logger_name,
            "msg": self.message,
        }
        if self.exc_text:
            # Truncate long tracebacks — enough to classify the
            # root cause, cheap on tokens.
            out["exc"] = self.exc_text[:2000]
        return out


class _RingBufferHandler(logging.Handler):
    """Captures WARNING+ records into a shared ring buffer."""

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__(level=logging.WARNING)
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        # Defensive — a buggy handler must never kill the emitting
        # thread (that would silently drop all future logs).
        try:
            captured = CapturedLog(
                timestamp=datetime.fromtimestamp(record.created, tz=UTC),
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                exc_text=self.format(record) if record.exc_info is not None else None,
            )
            self._buffer.append(captured)
        except Exception:  # noqa: BLE001 — swallow anything
            # Don't call self.handleError here — some log handlers
            # recurse, which can wedge the whole logging system.
            pass


class LogBuffer:
    """Thread-safe bounded buffer + attach helper.

    One instance per process, exposed as the module-level
    `_global_buffer` and accessed through `get_global_buffer()`.
    Attaches a handler to the root logger on first call to
    `attach_global_handler()`; subsequent attach calls are no-ops.
    """

    def __init__(self, maxlen: int = _MAX_RECORDS) -> None:
        self._deque: deque[CapturedLog] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._attached = False
        self._handler: _RingBufferHandler | None = None

    def append(self, record: CapturedLog) -> None:
        with self._lock:
            self._deque.append(record)

    def snapshot_since(self, *, since: datetime, limit: int = 100) -> list[CapturedLog]:
        """Return records emitted at or after `since`, newest-first.

        Capped at `limit` so a flood of errors can't blow the
        prompt budget. QA uses this to pull "last 2 hours of
        warnings" or similar.
        """
        with self._lock:
            # Snapshot a copy under the lock so the iteration is
            # consistent, then filter + slice outside.
            snapshot = list(self._deque)
        filtered = [r for r in snapshot if r.timestamp >= since]
        filtered.sort(key=lambda r: r.timestamp, reverse=True)
        return filtered[:limit]

    def snapshot_recent(
        self, *, minutes: int = 120, limit: int = 100
    ) -> list[CapturedLog]:
        """Convenience: records within the last N minutes."""
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        return self.snapshot_since(since=since, limit=limit)

    def count_since(self, *, since: datetime, level: str | None = None) -> int:
        """Count records at or after `since`. Optionally filter by
        exact level name (`"ERROR"`, `"CRITICAL"`)."""
        with self._lock:
            snapshot = list(self._deque)
        count = 0
        for r in snapshot:
            if r.timestamp < since:
                continue
            if level is not None and r.level != level:
                continue
            count += 1
        return count

    def attach_global_handler(self) -> None:
        """Install the handler on the root logger. Idempotent."""
        if self._attached:
            return
        handler = _RingBufferHandler(self)
        # Include exception info in the format so `self.format(record)`
        # (called in `emit` only when exc_info is set) returns the
        # full traceback.
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)
        self._handler = handler
        self._attached = True

    def detach_global_handler(self) -> None:
        """Remove the handler from the root logger. Used by tests
        so they don't leak handlers across runs."""
        if not self._attached or self._handler is None:
            return
        try:
            logging.getLogger().removeHandler(self._handler)
        except Exception:  # noqa: BLE001
            pass
        self._handler = None
        self._attached = False


_global_buffer: LogBuffer | None = None
_global_lock = threading.Lock()


def get_global_buffer() -> LogBuffer:
    """Return the process-wide `LogBuffer` singleton (creating it
    on first call). The orchestrator attaches it during lifespan
    startup; the QA agent reads from it on each scan.
    """
    global _global_buffer
    with _global_lock:
        if _global_buffer is None:
            _global_buffer = LogBuffer()
    return _global_buffer


__all__ = [
    "CapturedLog",
    "LogBuffer",
    "get_global_buffer",
]
