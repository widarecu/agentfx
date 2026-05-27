"""Subprocess-side helpers for running the orchestrator under resource caps.

This module provides the `apply_resource_limits()` function the child
process calls inside its entry point to enforce RAM and CPU-time caps via
`resource.setrlimit()`. The parent-side fork + supervision lives in
`api/workers/evolution_runner.py` (next chunk).

Why split: the orchestrator stays pure Python (testable on any platform);
the parent-side process management lives near the FastAPI lifespan
machinery where crash-recovery is wired into app startup.

The CPU-time cap is an OS-level *backstop* — the orchestrator's in-Python
`max_wall_clock_seconds` stop check is the primary mechanism. The
backstop only fires if the orchestrator itself hangs (e.g. inside a tight
numerical loop holding the GIL).
"""

from __future__ import annotations

import logging
import resource
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceLimits:
    """Per-run resource caps applied inside the child process."""

    # Hard cap on resident set size (address space). 2 GiB default —
    # enough for 5y of M1 candles + a working population. If the run
    # legitimately needs more, raise this (NOT the LLM injection budget).
    max_ram_bytes: int = 2 * 1024 * 1024 * 1024

    # Hard cap on total CPU time (seconds). Acts as a backstop against
    # in-Python stop checks failing to fire. Default 6h — matches the
    # orchestrator's default `max_wall_clock_seconds`.
    max_cpu_seconds: int = 6 * 3600


def apply_resource_limits(limits: ResourceLimits) -> None:
    """Apply CPU + RAM caps to the current process.

    Idempotent (calling twice with the same limits is fine). Lowering an
    existing limit is allowed; raising a hard limit requires root, so we
    only set the soft limit and leave the hard limit alone.

    Args:
        limits: The caps to apply.

    Raises:
        ValueError: if any limit is non-positive.
    """
    if limits.max_ram_bytes <= 0:
        raise ValueError(f"max_ram_bytes must be > 0 (got {limits.max_ram_bytes})")
    if limits.max_cpu_seconds <= 0:
        raise ValueError(f"max_cpu_seconds must be > 0 (got {limits.max_cpu_seconds})")

    # Address space (mostly equivalent to virtual memory on Linux). On
    # macOS RLIMIT_AS is honoured; some BSDs ignore it.
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        new_soft = (
            min(limits.max_ram_bytes, hard)
            if hard != resource.RLIM_INFINITY
            else limits.max_ram_bytes
        )
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
        logger.info("set RLIMIT_AS soft=%d hard=%d", new_soft, hard)
    except (ValueError, OSError) as e:  # noqa: PERF203
        # On some platforms RLIMIT_AS isn't enforceable — log and continue.
        # The CPU cap below is a sufficient backstop on its own.
        logger.warning("failed to set RLIMIT_AS: %s", e)

    # CPU time. SIGXCPU at the soft limit, SIGKILL at the hard.
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        new_soft = (
            min(limits.max_cpu_seconds, hard)
            if hard != resource.RLIM_INFINITY
            else limits.max_cpu_seconds
        )
        resource.setrlimit(resource.RLIMIT_CPU, (new_soft, hard))
        logger.info("set RLIMIT_CPU soft=%d hard=%d", new_soft, hard)
    except (ValueError, OSError) as e:
        logger.warning("failed to set RLIMIT_CPU: %s", e)


__all__ = ["ResourceLimits", "apply_resource_limits"]
