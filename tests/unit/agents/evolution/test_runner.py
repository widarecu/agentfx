"""Subprocess resource-limits helper tests.

We don't actually fork a subprocess in unit tests (slow, platform-noisy).
Instead we mock `resource.setrlimit()` and verify the helper computes
the right values + handles platform errors without crashing.
"""

from __future__ import annotations

import resource
from unittest.mock import patch

import pytest

from agentfx.agents.evolution.runner import (
    ResourceLimits,
    apply_resource_limits,
)


class TestResourceLimits:
    def test_defaults_are_two_gib_and_six_hours(self) -> None:
        limits = ResourceLimits()
        assert limits.max_ram_bytes == 2 * 1024 * 1024 * 1024
        assert limits.max_cpu_seconds == 6 * 3600

    def test_custom_limits(self) -> None:
        limits = ResourceLimits(max_ram_bytes=512 * 1024 * 1024, max_cpu_seconds=60)
        assert limits.max_ram_bytes == 512 * 1024 * 1024
        assert limits.max_cpu_seconds == 60


class TestApplyResourceLimits:
    def test_rejects_zero_or_negative_ram(self) -> None:
        with pytest.raises(ValueError, match="max_ram_bytes"):
            apply_resource_limits(ResourceLimits(max_ram_bytes=0))
        with pytest.raises(ValueError, match="max_ram_bytes"):
            apply_resource_limits(ResourceLimits(max_ram_bytes=-1))

    def test_rejects_zero_or_negative_cpu(self) -> None:
        with pytest.raises(ValueError, match="max_cpu_seconds"):
            apply_resource_limits(ResourceLimits(max_cpu_seconds=0))
        with pytest.raises(ValueError, match="max_cpu_seconds"):
            apply_resource_limits(ResourceLimits(max_cpu_seconds=-10))

    def test_calls_setrlimit_for_both_caps(self) -> None:
        # Pretend the existing hard limits are infinity so the helper
        # uses our requested values verbatim.
        with (
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch("resource.setrlimit") as mock_setrlimit,
        ):
            apply_resource_limits(
                ResourceLimits(max_ram_bytes=1_000_000_000, max_cpu_seconds=3600)
            )

        # Should call setrlimit twice — once for RLIMIT_AS, once for RLIMIT_CPU
        assert mock_setrlimit.call_count == 2
        which = [call.args[0] for call in mock_setrlimit.call_args_list]
        assert resource.RLIMIT_AS in which
        assert resource.RLIMIT_CPU in which

    def test_clamps_to_existing_hard_limit(self) -> None:
        # If the existing hard limit is *lower* than requested, we must
        # clamp — raising a hard limit requires root.
        existing_hard = 500_000_000  # 500 MB hard cap already in place
        with (
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, existing_hard),
            ),
            patch("resource.setrlimit") as mock_setrlimit,
        ):
            apply_resource_limits(
                ResourceLimits(max_ram_bytes=2_000_000_000, max_cpu_seconds=3600)
            )

        ram_call = next(
            c for c in mock_setrlimit.call_args_list if c.args[0] == resource.RLIMIT_AS
        )
        new_soft, new_hard = ram_call.args[1]
        # The new soft must be capped at the existing hard
        assert new_soft == existing_hard
        assert new_hard == existing_hard

    def test_swallows_oserror_without_crashing(self) -> None:
        # Some platforms ignore RLIMIT_AS — the helper logs and continues.
        with (
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch("resource.setrlimit", side_effect=OSError("not supported")),
        ):
            # Should not raise
            apply_resource_limits(ResourceLimits())

    def test_swallows_value_error_without_crashing(self) -> None:
        # A ValueError from setrlimit (e.g. lowering the hard limit on
        # some kernels) shouldn't tear down the worker.
        with (
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch("resource.setrlimit", side_effect=ValueError("invalid")),
        ):
            apply_resource_limits(ResourceLimits())
