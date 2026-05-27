"""Unit tests for the evolution runner's config rehydration.

The runner is a thin shim between the persisted ``evolution_runs`` row and
the orchestrator child process. Its critical responsibility is loading
every modal-exposed knob off the persisted config_json — if a field is
dropped here, the user-set value silently becomes the dataclass default,
which is invisible at API time and only shows up in run output much later.
``min_cagr_pct`` was the canonical regression: the validator kept enforcing
15% even when the user explicitly set 8% or 10%. These tests pin the
round-trip for every knob the modal exposes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentfx.api.workers.evolution_runner import (
    DataWindowNotCoveredError,
    _assert_window_covered,
    _build_orchestrator_config,
    _build_stop_conditions,
    _resolve_requested_window,
    _was_window_explicitly_pinned,
)


def test_min_cagr_pct_round_trips_from_config_dict():
    """The exact regression that hid through evorun_20260524_e2e7c243:
    user submitted min_cagr_pct=0.09, DB stored 0.09, worker dropped it,
    validator enforced the dataclass default 0.15. Pin it."""
    cfg = _build_orchestrator_config(
        {"min_cagr_pct": 0.07},
        instrument="EUR_USD",
        granularity="M1",
    )
    assert cfg.min_cagr_pct == 0.07, (
        "Worker dropped min_cagr_pct — user-set Gate B floor was ignored, "
        "validator silently fell back to the 15% dataclass default."
    )


def test_all_modal_exposed_knobs_round_trip():
    """Every field the start-run modal lets the user set must survive the
    JSON → OrchestratorConfig round-trip. If you add a knob to the modal
    AND to OrchestratorConfig but forget to wire it through here, the user
    value is silently ignored — same failure mode as the min_cagr bug."""
    cfg_dict = {
        "population_size": 25,
        "llm_injection_every_n": 4,
        "n_llm_proposals": 7,
        "n_validation_folds": 5,
        "top_n_to_validate": 3,
        "crossover_prob": 0.55,
        "mutation_prob": 0.35,
        "elite_fraction": 0.15,
        "tournament_size": 4,
        "rng_seed": 7,
        "account_balance": 25_000.0,
        "position_size": 50_000,
        "spread_pips": 0.8,
        "leverage": 50,
        "use_prior_winners": False,
        "n_prior_winners": 5,
        "llm_seed_gen0": False,
        "n_llm_gen0_proposals": 8,
        "min_cagr_pct": 0.08,
    }
    cfg = _build_orchestrator_config(cfg_dict, instrument="GBP_USD", granularity="H1")

    # Top-level identity
    assert cfg.instrument == "GBP_USD"
    assert cfg.granularity == "H1"

    # Every knob — explicit so the assertion list is also the regression
    # contract. If a field is added to OrchestratorConfig and to the
    # modal but not asserted here, the next person will silently break it.
    assert cfg.population_size == 25
    assert cfg.llm_injection_every_n == 4
    assert cfg.n_llm_proposals == 7
    assert cfg.n_validation_folds == 5
    assert cfg.top_n_to_validate == 3
    assert cfg.crossover_prob == 0.55
    assert cfg.mutation_prob == 0.35
    assert cfg.elite_fraction == 0.15
    assert cfg.tournament_size == 4
    assert cfg.rng_seed == 7
    assert cfg.account_balance == 25_000.0
    assert cfg.position_size == 50_000
    assert cfg.spread_pips == 0.8
    assert cfg.leverage == 50
    assert cfg.use_prior_winners is False
    assert cfg.n_prior_winners == 5
    assert cfg.llm_seed_gen0 is False
    assert cfg.n_llm_gen0_proposals == 8
    assert cfg.min_cagr_pct == 0.08


def test_empty_config_dict_falls_back_to_dataclass_defaults():
    """An empty config_dict (legacy rows, or omitted-everything callers)
    must produce a fully-defaulted OrchestratorConfig — not crash."""
    cfg = _build_orchestrator_config({}, instrument="EUR_USD", granularity="M1")
    # Spot-check a few defaults from the dataclass definition
    assert cfg.population_size == 30
    assert cfg.min_cagr_pct == 0.15
    assert cfg.leverage == 30
    assert cfg.use_prior_winners is True
    assert cfg.llm_seed_gen0 is True


# ─────────────────────────────────────────────────────────────────────────
# Stop conditions rehydration — same risk shape as OrchestratorConfig.
# A dropped field silently falls back to the dataclass default; the
# `enable_target_fitness_stop` flag is the canonical example because
# default-off means a missed wiring would let runs early-stop on
# training fitness the user didn't actually opt into (or vice versa).
# ─────────────────────────────────────────────────────────────────────────


def test_enable_target_fitness_stop_round_trips():
    """User-set opt-in flag must reach the dataclass — dropping it would
    re-introduce the silent early-stop behaviour we just removed."""
    sc = _build_stop_conditions({"enable_target_fitness_stop": True})
    assert sc.enable_target_fitness_stop is True

    sc = _build_stop_conditions({"enable_target_fitness_stop": False})
    assert sc.enable_target_fitness_stop is False


def test_all_stop_condition_fields_round_trip():
    """Every StopConditions field exposed in the API must survive the
    JSON → StopConditions round-trip."""
    sc = _build_stop_conditions(
        {
            "max_generations": 12,
            "max_wall_clock_seconds": 7200,
            "target_fitness": 2.0,
            "enable_target_fitness_stop": True,
            "no_improvement_generations": 8,
        }
    )
    assert sc.max_generations == 12
    assert sc.max_wall_clock_seconds == 7200
    assert sc.target_fitness == 2.0
    assert sc.enable_target_fitness_stop is True
    assert sc.no_improvement_generations == 8


# ─────────────────────────────────────────────────────────────────────────
# Data-window resolution — the new explicit-vs-rolling logic.
# Reproducibility-critical: a run launched today should evaluate over the
# SAME data window if re-launched next week (when both dates are set).
# ─────────────────────────────────────────────────────────────────────────


def test_window_explicit_start_and_end_used_verbatim():
    """When both `data_window_start` and `data_window_end` are set in the
    config, the worker must use them as-is and ignore `data_years` and
    wall-clock time. This is the reproducibility guarantee."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 6, 30, tzinfo=UTC)
    s, e = _resolve_requested_window(
        {
            "data_years": 999,  # ignored when both dates set
            "data_window_start": start.isoformat(),
            "data_window_end": end.isoformat(),
        }
    )
    assert s == start
    assert e == end


def test_window_only_end_set_backfills_start_via_data_years():
    """If only end is pinned, start is shifted `data_years * 365` days
    behind it — still reproducible because no `now()` reference."""
    end = datetime(2026, 1, 1, tzinfo=UTC)
    s, e = _resolve_requested_window(
        {"data_years": 2, "data_window_end": end.isoformat()}
    )
    assert e == end
    assert s == end - timedelta(days=2 * 365)


def test_window_only_start_set_forwardfills_end_via_data_years():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    s, e = _resolve_requested_window(
        {"data_years": 3, "data_window_start": start.isoformat()}
    )
    assert s == start
    assert e == start + timedelta(days=3 * 365)


def test_window_neither_set_falls_back_to_rolling_from_now():
    """Legacy behaviour for runs created via the modal without ticking
    the fixed-date checkbox. Verified by checking the span and the
    end's proximity to now (since now() shifts mid-test)."""
    before = datetime.now(UTC)
    s, e = _resolve_requested_window({"data_years": 5})
    after = datetime.now(UTC)
    assert before <= e <= after
    span = e - s
    assert abs(span.total_seconds() - 5 * 365 * 86400) < 5  # 5y ± 5s


def test_window_accepts_datetime_objects_not_just_strings():
    """SQLAlchemy might return JSONB values as already-parsed types in
    some configurations. Defensive — both forms must work."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    end = datetime(2025, 9, 1, tzinfo=UTC)
    s, e = _resolve_requested_window(
        {"data_window_start": start, "data_window_end": end}
    )
    assert s == start
    assert e == end


def test_was_window_explicitly_pinned_detects_either_key():
    """Either of the two date keys → user explicitly anchored. We treat
    partial pins as intentional too — a half-pinned window still has
    a date the user doesn't want silently shifted."""
    assert _was_window_explicitly_pinned({}) is False
    assert _was_window_explicitly_pinned({"data_years": 5}) is False
    assert (
        _was_window_explicitly_pinned({"data_window_start": "2025-01-01T00:00:00Z"})
        is True
    )
    assert (
        _was_window_explicitly_pinned({"data_window_end": "2025-12-31T00:00:00Z"})
        is True
    )
    assert (
        _was_window_explicitly_pinned(
            {
                "data_window_start": "2025-01-01T00:00:00Z",
                "data_window_end": "2025-12-31T00:00:00Z",
            }
        )
        is True
    )
    # Explicit nulls (user un-checked the fixed-dates box) → not pinned
    assert (
        _was_window_explicitly_pinned(
            {"data_window_start": None, "data_window_end": None}
        )
        is False
    )


class TestAssertWindowCovered:
    """The strict mode for pinned windows — the contract is: never
    silently shrink. A user who pins 2024-07 → 2025-06 must get either
    an evaluation over THAT exact window or a clear failure, not a
    silently-clamped 48-day slice."""

    def test_pinned_window_fully_within_available_passes(self):
        requested = (
            datetime(2025, 6, 1, tzinfo=UTC),
            datetime(2025, 12, 1, tzinfo=UTC),
        )
        available = (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        # No exception → contract satisfied.
        _assert_window_covered(requested, available)

    def test_start_predates_oldest_candle_raises(self):
        """Exactly the user's incident: requested 2024-07 → 2025-06 but
        oldest candle is 2025-05-13. Silent clamp would give a 48-day
        window the user didn't ask for. We now fail loudly."""
        requested = (
            datetime(2024, 7, 1, tzinfo=UTC),
            datetime(2025, 6, 30, tzinfo=UTC),
        )
        available = (
            datetime(2025, 5, 13, 12, 17, tzinfo=UTC),
            datetime(2026, 5, 19, tzinfo=UTC),
        )
        with pytest.raises(DataWindowNotCoveredError) as ei:
            _assert_window_covered(requested, available)
        msg = str(ei.value)
        assert "predates the oldest candle" in msg
        assert "2025-05-13" in msg  # surfaces the actual available range
        assert ei.value.stop_reason == "data_window_not_covered"

    def test_end_extends_past_newest_candle_raises(self):
        requested = (
            datetime(2025, 6, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        )
        available = (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2026, 5, 19, tzinfo=UTC),
        )
        with pytest.raises(DataWindowNotCoveredError) as ei:
            _assert_window_covered(requested, available)
        assert "extends past the newest candle" in str(ei.value)

    def test_both_endpoints_problematic_lists_both(self):
        """Multi-problem error message — the user should learn about
        every issue in one go, not be sent on a guess-and-retry loop."""
        requested = (
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2030, 1, 1, tzinfo=UTC),
        )
        available = (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(DataWindowNotCoveredError) as ei:
            _assert_window_covered(requested, available)
        msg = str(ei.value)
        assert "predates" in msg and "extends past" in msg

    def test_no_data_at_all_raises_clearly(self):
        requested = (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 12, 31, tzinfo=UTC),
        )
        with pytest.raises(DataWindowNotCoveredError) as ei:
            _assert_window_covered(requested, None)
        assert "No candle data available" in str(ei.value)


def test_window_naive_datetimes_get_utc_attached():
    """Belt-and-braces: never silently treat a naive datetime as local
    time — the whole rest of the pipeline is UTC-tagged."""
    naive_start = datetime(2025, 1, 1)  # no tzinfo
    naive_end = datetime(2025, 12, 31)
    s, e = _resolve_requested_window(
        {"data_window_start": naive_start, "data_window_end": naive_end}
    )
    assert s.tzinfo is not None and s.utcoffset() == timedelta(0)
    assert e.tzinfo is not None and e.utcoffset() == timedelta(0)


def test_stop_conditions_empty_dict_uses_defaults():
    """Empty input → all dataclass defaults. Critically,
    enable_target_fitness_stop must default to False (the new behaviour)."""
    sc = _build_stop_conditions({})
    assert sc.max_generations == 30
    assert sc.target_fitness == 1.5
    assert sc.enable_target_fitness_stop is False, (
        "Default must be False — opt-in only. Flipping this default would "
        "silently restore the early-stop-on-training-fitness behaviour."
    )
    assert sc.no_improvement_generations == 5
