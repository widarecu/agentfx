"""Unit tests for the evolution router.

Focused on the pure-logic helpers extracted from the router (currently
`_resolve_backtest_slice`). HTTP-level coverage of `GET …/backtest` lives
in the integration suite where a real backtest engine + DB are
available; the slice-selection logic itself is exercised here without
that overhead.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentfx.api.routers.evolution import (
    _resolve_backtest_slice,
    _resolve_pinned_spread_pips,
)
from agentfx.core.validation import make_walk_forward_folds


@pytest.fixture
def folds_and_window():
    """A 4-fold walk-forward split over a 1-year window.

    4 folds → 3 training, 1 OOS (final). Each fold is ~91 days.
    """
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    folds = make_walk_forward_folds(start=start, end=end, k=4)
    return folds, (start, end)


def test_training_slice_excludes_oos_fold(folds_and_window):
    folds, full = folds_and_window
    s, e = _resolve_backtest_slice(folds=folds, full_window=full, window="training")
    # Training spans folds 0..k-2; must end before the OOS fold starts.
    oos_fold = next(f for f in folds if f.is_oos)
    assert s == folds[0].start
    assert e == oos_fold.start  # contiguous boundary
    assert e < full[1]  # strictly shorter than the full window


def test_oos_slice_is_the_held_out_fold(folds_and_window):
    folds, full = folds_and_window
    s, e = _resolve_backtest_slice(folds=folds, full_window=full, window="oos")
    oos_fold = next(f for f in folds if f.is_oos)
    assert s == oos_fold.start
    assert e == oos_fold.end
    assert e == full[1]  # OOS ends at the full window's end (final fold)


def test_full_slice_is_the_entire_effective_window(folds_and_window):
    folds, full = folds_and_window
    s, e = _resolve_backtest_slice(folds=folds, full_window=full, window="full")
    # `full` mode ignores the fold split entirely — returns the
    # caller-supplied bounds verbatim.
    assert (s, e) == full


def test_training_and_oos_are_contiguous_and_disjoint(folds_and_window):
    """Sanity: train end == oos start, with no overlap or gap."""
    folds, full = folds_and_window
    _, train_end = _resolve_backtest_slice(
        folds=folds, full_window=full, window="training"
    )
    oos_start, _ = _resolve_backtest_slice(folds=folds, full_window=full, window="oos")
    assert train_end == oos_start


def test_unknown_window_value_treated_as_full():
    """Defensive: the Literal at the type boundary should prevent this,
    but if a future caller bypasses validation the helper must not raise."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    folds = make_walk_forward_folds(start=start, end=end, k=4)
    s, e = _resolve_backtest_slice(
        folds=folds,
        full_window=(start, end),
        window="something_else",  # type: ignore[arg-type]
    )
    assert (s, e) == (start, end)


# ─────────────────────────────────────────────────────────────────────
# _resolve_pinned_spread_pips
# ─────────────────────────────────────────────────────────────────────
# The endpoint must replay each genome backtest at the EXACT cost model
# the GA + validator paid, otherwise the on-demand metrics drift from
# the persisted walk-forward fold values. These tests lock in the
# precedence the helper uses to pick the right value.


def test_per_pair_dict_wins_over_legacy_scalar():
    """v0.4.0+ runs always have the per-pair dict; the legacy scalar may
    coexist for back-compat. Per-pair wins because it's the more specific
    answer when both are present."""
    cfg = {
        "effective_spread_pips_per_pair": {"EUR_USD": 1.0, "GBP_USD": 1.3},
        "effective_spread_pips": 0.6,  # stale legacy entry
        "spread_pips": None,
    }
    assert _resolve_pinned_spread_pips(cfg, "EUR_USD") == 1.0
    assert _resolve_pinned_spread_pips(cfg, "GBP_USD") == 1.3


def test_legacy_scalar_used_when_dict_lacks_instrument():
    """Pre-v0.4.0 runs only have the scalar — instrument lookup in the
    dict misses, so we fall through to the legacy field."""
    cfg = {
        "effective_spread_pips_per_pair": {"GBP_USD": 1.3},
        "effective_spread_pips": 1.0,
    }
    # EUR_USD isn't in the dict; should fall back to scalar
    assert _resolve_pinned_spread_pips(cfg, "EUR_USD") == 1.0


def test_user_pinned_spread_used_when_no_effective_present():
    """A run that crashed before the worker could snapshot the live spread
    still has the user's original `spread_pips` if they pinned one. Use
    that as the last meaningful fallback."""
    cfg = {"spread_pips": 0.8}
    assert _resolve_pinned_spread_pips(cfg, "EUR_USD") == 0.8


def test_returns_none_when_nothing_was_pinned():
    """Truly ancient runs with no spread info at all → return None and
    let the caller decide whether to defer to a live lookup."""
    assert _resolve_pinned_spread_pips({}, "EUR_USD") is None
    assert (
        _resolve_pinned_spread_pips({"effective_spread_pips_per_pair": {}}, "EUR_USD")
        is None
    )


def test_does_not_silently_use_live_spread_when_pinned_exists():
    """Regression: the previous code only consulted `spread_pips` (the
    user-input field). When the user didn't pin and the worker pinned
    via `effective_spread_pips`, the endpoint would silently fall back
    to the *current* live spread lookup inside `run_backtest_from_db`.
    This test locks that path closed."""
    cfg = {
        "spread_pips": None,  # user didn't pin
        "effective_spread_pips": 1.0,  # worker pinned at run-start
    }
    assert _resolve_pinned_spread_pips(cfg, "EUR_USD") == 1.0
