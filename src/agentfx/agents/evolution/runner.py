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
import os
import resource
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _default_max_ram_bytes() -> int:
    """Resolve the RLIMIT_AS cap, env-configurable via
    ``EVOLUTION_CHILD_MAX_RAM_GB`` (whole gigabytes).

    Default raised to 6 GiB in 2026-05 after Railway staging hit
    MemoryError at *module import time* with the previous 2 GiB cap:
    glibc malloc reserves ~128 MiB of virtual address space per arena,
    one set of arenas per CPU core. Local Docker Desktop with 2–4 vCPUs
    stayed under 2 GiB; Railway containers with 8+ vCPUs blew past it
    just importing numpy + pandas + scipy + sklearn + anthropic before
    any orchestrator logic ran.

    The cap is on RLIMIT_AS (virtual address space), NOT resident set
    size, so 6 GiB is well below Railway's 24 GiB plan and still gives
    a sane backstop for runaway pandas merges.
    """
    raw = os.environ.get("EVOLUTION_CHILD_MAX_RAM_GB", "6")
    try:
        gb = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "invalid EVOLUTION_CHILD_MAX_RAM_GB=%r; falling back to 6 GiB", raw
        )
        gb = 6.0
    return int(gb * 1024 * 1024 * 1024)


@dataclass(frozen=True)
class ResourceLimits:
    """Per-run resource caps applied inside the child process."""

    # Hard cap on the child's virtual address space. Default 6 GiB,
    # configurable via EVOLUTION_CHILD_MAX_RAM_GB. See the helper's
    # docstring for why 2 GiB (the previous default) was insufficient
    # on Railway-class boxes.
    max_ram_bytes: int = field(default_factory=_default_max_ram_bytes)

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
