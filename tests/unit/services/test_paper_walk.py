"""Unit tests for `services.paper_walk.walk()` — the pure sequential
state machine at the heart of the v0.7.0 paper-runner fidelity fix.

Every edge case from the design table is covered here:
  1. Empty inputs → no-op.
  2. Bookmark filter → only bars strictly > bookmark are walked.
  3. Bookmark None → only latest bar considered (fresh-run semantics).
  4. Flat + no signal → nothing.
  5. Flat + signal → opens.
  6. In LONG, no exit fires → hold, bars_held increments.
  7. In LONG, SL hits → close (reason=stop).
  8. In LONG, TP hits → close (reason=target).
  9. In LONG, max_hold reached → close (reason=max_hold).
  10. In LONG, opposite signal + opposite_signal=True → flip.
  11. In LONG, opposite signal + opposite_signal=False → hold.
  12. min_hold suppresses ALL exit paths.
  13. min_hold boundary — the exact bar min_hold opens on.
  14. Same-direction re-entry while holding → no-op.
  15. Multi-bar walk with intermediate transitions.
  16. THE BUG: LONG open + persistent OBV<0 during min_hold + walk
      catches up post-hold → walk does NOT flip (matches backtest).
  17. Fresh signal AFTER min_hold expires → fires at the correct bar.

Plus a property test that pins walk() to backtest's `_apply_exits`
for a batch of random-ish inputs — pure defence in depth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentfx.core.dsl.compiler import _apply_exits
from agentfx.core.dsl.grammar import ExitRules
from agentfx.services.paper_walk import (
    Transition,
    WalkResult,
    WalkState,
    walk,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────────


PIP = 0.0001
BASE_TS = datetime(2026, 8, 2, 21, 0, 0, tzinfo=UTC)


def _bars(count: int, start: datetime = BASE_TS) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [start + timedelta(minutes=i) for i in range(count)], tz="UTC"
    )


def _series(values: list, index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(values, index=index)


def _rules(
    stop_pips: int = 20,
    target_pips: int = 40,
    min_hold: int = 0,
    max_hold: int = 100,
    opposite_signal: bool = True,
) -> ExitRules:
    return ExitRules(
        stop_pips=stop_pips,
        target_pips=target_pips,
        min_hold_bars=min_hold,
        max_hold_bars=max_hold,
        opposite_signal=opposite_signal,
    )


def _flat() -> WalkState:
    return WalkState(in_position=0, entry_close=None, bars_held=0)


def _long_at(price: float, bars_held: int = 0) -> WalkState:
    return WalkState(in_position=1, entry_close=price, bars_held=bars_held)


def _short_at(price: float, bars_held: int = 0) -> WalkState:
    return WalkState(in_position=-1, entry_close=price, bars_held=bars_held)


# ─────────────────────────────────────────────────────────────────────
# 1. Empty inputs
# ─────────────────────────────────────────────────────────────────────


class TestEmpty:
    def test_empty_raw_entries_is_noop(self):
        idx = pd.DatetimeIndex([], tz="UTC")
        result = walk(
            _series([], idx),
            _series([], idx),
            _flat(),
            None,
            _rules(),
            PIP,
        )
        assert result == WalkResult(
            final_state=_flat(), transitions=[], last_bar_ts=None
        )

    def test_mismatched_lengths_raises(self):
        idx1 = _bars(2)
        idx2 = _bars(3)
        with pytest.raises(ValueError, match="same length"):
            walk(
                _series([0, 0], idx1),
                _series([1.0, 1.0, 1.0], idx2),
                _flat(),
                None,
                _rules(),
                PIP,
            )

    def test_mismatched_index_raises(self):
        idx = _bars(2)
        idx2 = _bars(2, start=BASE_TS + timedelta(hours=1))
        with pytest.raises(ValueError, match="share an index"):
            walk(
                _series([0, 0], idx),
                _series([1.0, 1.0], idx2),
                _flat(),
                None,
                _rules(),
                PIP,
            )


# ─────────────────────────────────────────────────────────────────────
# 2. Bookmark filtering
# ─────────────────────────────────────────────────────────────────────


class TestBookmark:
    def test_bookmark_filters_to_strictly_greater_bars(self):
        idx = _bars(5)  # bars 0..4
        # Signal on bars 1, 2 (values -1 = SHORT).
        raw = _series([0, -1, -1, 0, 0], idx)
        close = _series([1.15, 1.15, 1.15, 1.15, 1.15], idx)
        # Bookmark at bar 2. Only bars 3, 4 walked. No signal in those
        # → flat stays flat, no transitions.
        result = walk(raw, close, _flat(), idx[2].to_pydatetime(), _rules(), PIP)
        assert result.transitions == []
        assert result.final_state == _flat()
        assert result.last_bar_ts == idx[4].to_pydatetime()

    def test_bookmark_none_takes_only_latest_bar(self):
        # Fresh-run semantics: no bookmark → only the newest bar is
        # considered. Prevents backfilling ancient signals on first tick.
        idx = _bars(5)
        raw = _series([1, 1, 1, 1, 0], idx)  # LONG signal on bars 0..3
        close = _series([1.15, 1.15, 1.15, 1.15, 1.15], idx)
        result = walk(raw, close, _flat(), None, _rules(), PIP)
        assert result.transitions == []  # latest bar has raw=0
        assert result.final_state == _flat()

    def test_bookmark_at_or_past_latest_returns_noop(self):
        idx = _bars(3)
        raw = _series([1, -1, 1], idx)
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(raw, close, _flat(), idx[-1].to_pydatetime(), _rules(), PIP)
        assert result.transitions == []
        assert result.final_state == _flat()
        assert result.last_bar_ts is None


# ─────────────────────────────────────────────────────────────────────
# 3–5. Flat state
# ─────────────────────────────────────────────────────────────────────


class TestFromFlat:
    def test_flat_no_signal_stays_flat(self):
        idx = _bars(3)
        raw = _series([0, 0, 0], idx)
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _flat(),
            (BASE_TS - timedelta(hours=1)),  # bookmark before all bars
            _rules(),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state == _flat()
        assert result.last_bar_ts == idx[-1].to_pydatetime()

    def test_flat_long_signal_opens_long(self):
        idx = _bars(3)
        raw = _series([0, 1, 0], idx)
        # Prices stay flat post-open so TP/SL don't accidentally trip.
        close = _series([1.15, 1.16, 1.16], idx)
        result = walk(
            raw, close, _flat(), BASE_TS - timedelta(hours=1), _rules(min_hold=0), PIP
        )
        assert len(result.transitions) == 1
        t = result.transitions[0]
        assert t.kind == "open"
        assert t.direction == 1
        assert t.bar_ts == idx[1].to_pydatetime()
        assert t.close_px == pytest.approx(1.16)
        assert result.final_state.in_position == 1
        assert result.final_state.entry_close == pytest.approx(1.16)
        # bars_held incremented on bar 2 (hold): open on bar 1 → 0, then
        # bar 2 hold → bars_held == 1.
        assert result.final_state.bars_held == 1

    def test_flat_short_signal_opens_short(self):
        idx = _bars(3)
        raw = _series([0, 0, -1], idx)
        close = _series([1.15, 1.15, 1.14], idx)
        result = walk(
            raw, close, _flat(), BASE_TS - timedelta(hours=1), _rules(min_hold=0), PIP
        )
        assert len(result.transitions) == 1
        assert result.transitions[0].kind == "open"
        assert result.transitions[0].direction == -1
        assert result.final_state == _short_at(1.14, bars_held=0)


# ─────────────────────────────────────────────────────────────────────
# 6–11. In-position exits (no min_hold)
# ─────────────────────────────────────────────────────────────────────


class TestInLong:
    def test_hold_when_no_exit_conditions_met(self):
        idx = _bars(3)
        raw = _series([0, 0, 0], idx)
        close = _series([1.15, 1.1501, 1.1502], idx)  # +1 pip, +2 pip
        result = walk(
            raw,
            close,
            _long_at(1.15, bars_held=0),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, min_hold=0),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state.in_position == 1
        assert result.final_state.bars_held == 3

    def test_stop_loss_closes_long(self):
        idx = _bars(3)
        raw = _series([0, 0, 0], idx)
        # Bar 2: move -20.1 pips from entry → SL trips at 20 pips.
        close = _series([1.15, 1.149, 1.14790], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, min_hold=0),
            PIP,
        )
        assert len(result.transitions) == 1
        t = result.transitions[0]
        assert t.kind == "close" and t.reason == "stop"
        assert t.direction == 0
        assert t.bar_ts == idx[2].to_pydatetime()
        assert result.final_state == _flat()

    def test_take_profit_closes_long(self):
        idx = _bars(3)
        raw = _series([0, 0, 0], idx)
        close = _series([1.15, 1.153, 1.1541], idx)  # +41 pip on bar 2
        result = walk(
            raw,
            close,
            _long_at(1.15),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, min_hold=0),
            PIP,
        )
        assert len(result.transitions) == 1
        assert result.transitions[0].reason == "target"

    def test_max_hold_closes_long_on_exact_bar(self):
        idx = _bars(5)
        raw = _series([0, 0, 0, 0, 0], idx)
        close = _series([1.15, 1.15, 1.15, 1.15, 1.15], idx)
        # Start with bars_held=2. max_hold=5 → close on bar N where
        # 2+N == 5. Walk visits bars 0..4 → increment to 3, 4, 5, 6, 7.
        # First bar with new_bars_held >= 5 is idx[2] (2 + 3 = 5).
        result = walk(
            raw,
            close,
            _long_at(1.15, bars_held=2),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, max_hold=5),
            PIP,
        )
        assert len(result.transitions) == 1
        assert result.transitions[0].reason == "max_hold"
        assert result.transitions[0].bar_ts == idx[2].to_pydatetime()

    def test_opposite_flip_when_enabled(self):
        idx = _bars(3)
        raw = _series([0, 0, -1], idx)  # SHORT signal on bar 2
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15),
            BASE_TS - timedelta(hours=1),
            _rules(min_hold=0, opposite_signal=True),
            PIP,
        )
        assert len(result.transitions) == 1
        t = result.transitions[0]
        assert t.kind == "flip"
        assert t.reason == "opposite_flip"
        assert t.direction == -1
        assert result.final_state.in_position == -1
        assert result.final_state.bars_held == 0

    def test_opposite_signal_ignored_when_disabled(self):
        idx = _bars(3)
        raw = _series([0, 0, -1], idx)
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15),
            BASE_TS - timedelta(hours=1),
            _rules(min_hold=0, opposite_signal=False),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state.in_position == 1

    def test_same_direction_signal_is_noop_while_holding(self):
        idx = _bars(3)
        raw = _series([0, 1, 1], idx)  # LONG re-signal while LONG
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15),
            BASE_TS - timedelta(hours=1),
            _rules(min_hold=0),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state.in_position == 1
        assert result.final_state.bars_held == 3


# ─────────────────────────────────────────────────────────────────────
# 12–13. min_hold gate
# ─────────────────────────────────────────────────────────────────────


class TestMinHold:
    def test_min_hold_suppresses_stop_loss(self):
        idx = _bars(3)
        raw = _series([0, 0, 0], idx)
        # SL would trip at bar 2 (move = -21 pips), but min_hold=5 blocks.
        close = _series([1.15, 1.149, 1.1479], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15, bars_held=0),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, min_hold=5),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state.in_position == 1
        assert result.final_state.bars_held == 3

    def test_min_hold_suppresses_opposite_flip(self):
        idx = _bars(3)
        raw = _series([0, -1, -1], idx)  # SHORT signals during min_hold
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15, bars_held=0),
            BASE_TS - timedelta(hours=1),
            _rules(min_hold=5, opposite_signal=True),
            PIP,
        )
        assert result.transitions == []
        assert result.final_state.in_position == 1

    def test_min_hold_gate_opens_on_exact_boundary(self):
        # min_hold=3 → gate opens when new_bars_held == 3.
        # Start bars_held=1 → walk increments 2, 3, 4. Gate opens on
        # walked bar 1 (new_bars_held=3). If raw_pos[bar1] == -in_pos
        # AND we're past the gate, flip fires there.
        idx = _bars(3)
        raw = _series([-1, -1, -1], idx)  # SHORT signals on all bars
        close = _series([1.15, 1.15, 1.15], idx)
        result = walk(
            raw,
            close,
            _long_at(1.15, bars_held=1),
            BASE_TS - timedelta(hours=1),
            _rules(min_hold=3, opposite_signal=True),
            PIP,
        )
        # Bar 0: new_bars_held=2 < 3 → hold. Bar 1: new_bars_held=3 →
        # gate open → flip fires. Bar 2: never reached (walk continues
        # past a flip but raw[2] == -1 which equals new in_position
        # (SHORT), so same-direction no-op).
        assert len(result.transitions) == 1
        assert result.transitions[0].bar_ts == idx[1].to_pydatetime()
        assert result.transitions[0].kind == "flip"


# ─────────────────────────────────────────────────────────────────────
# 15. Multi-bar walks (catch-up scenarios)
# ─────────────────────────────────────────────────────────────────────


class TestMultiBarWalk:
    def test_multi_bar_walk_records_all_transitions_in_order(self):
        idx = _bars(5)
        # Bar 0: LONG entry from flat. Bar 3: TP hit. Bar 4: nothing
        # (flat again). Sequence: open → hold → hold → close → nothing.
        raw = _series([1, 0, 0, 0, 0], idx)
        close = _series([1.15, 1.15, 1.15, 1.1541, 1.16], idx)
        result = walk(
            raw,
            close,
            _flat(),
            BASE_TS - timedelta(hours=1),
            _rules(stop_pips=20, target_pips=40, min_hold=0),
            PIP,
        )
        kinds = [t.kind for t in result.transitions]
        assert kinds == ["open", "close"]
        assert result.transitions[0].bar_ts == idx[0].to_pydatetime()
        assert result.transitions[1].bar_ts == idx[3].to_pydatetime()
        assert result.transitions[1].reason == "target"
        assert result.final_state == _flat()


# ─────────────────────────────────────────────────────────────────────
# 16. THE BUG — regression test
# ─────────────────────────────────────────────────────────────────────


class TestBugRegression:
    """The exact scenario from Wilson's UAT 2026-08-06 report.

    Trade #1 opens LONG at bar 0. min_hold_bars = 60. During bars
    5..15 (well inside min_hold), the short-entry condition fires
    (persistent — raw_pos = -1). After min_hold expires at bar 60,
    the short-entry condition is no longer firing (raw_pos = 0). Bar
    69: still no signal.

    Backtest would hold LONG throughout: min_hold silently suppresses
    the fires at bars 5–15, and post-min-hold there's no fresh signal
    to flip on.

    The pre-refactor paper runner would fire SHORT at ~bar 60 because
    it processed the queued raw_pos = -1 from bars 5–15 all at once.

    walk() must NOT flip. Final state must remain LONG.
    """

    def test_persistent_short_signal_during_min_hold_is_dropped(self):
        idx = _bars(80)  # 80 M1 bars
        raw_values = [0] * 80
        # LONG entry on bar 0.
        raw_values[0] = 1
        # SHORT signal (persistent — every bar) during bars 5..15.
        for i in range(5, 16):
            raw_values[i] = -1
        # After bar 15 → no more signals. Bar 60+ (post-min-hold): no fires.
        raw = _series(raw_values, idx)
        close = _series([1.15] * 80, idx)  # flat prices → no SL/TP fires
        result = walk(
            raw,
            close,
            _flat(),
            BASE_TS - timedelta(hours=1),
            _rules(
                stop_pips=20,
                target_pips=40,
                min_hold=60,
                max_hold=1000,
                opposite_signal=True,
            ),
            PIP,
        )
        # Exactly one transition: the LONG open on bar 0. NO flip.
        kinds = [t.kind for t in result.transitions]
        assert kinds == ["open"], (
            f"expected only ['open'], got {kinds}. "
            "Persistent short signal during min_hold must be silently "
            "dropped — matches backtest _apply_exits."
        )
        assert result.final_state.in_position == 1

    def test_fresh_short_signal_after_min_hold_does_flip(self):
        """Compl to the above: if the signal fires AFTER min_hold
        expires, we DO flip. The gate suppresses during; it doesn't
        block forever."""
        idx = _bars(80)
        raw_values = [0] * 80
        raw_values[0] = 1  # LONG entry
        raw_values[62] = -1  # SHORT signal AFTER min_hold expires
        raw = _series(raw_values, idx)
        close = _series([1.15] * 80, idx)
        result = walk(
            raw,
            close,
            _flat(),
            BASE_TS - timedelta(hours=1),
            _rules(
                stop_pips=20,
                target_pips=40,
                min_hold=60,
                max_hold=1000,
                opposite_signal=True,
            ),
            PIP,
        )
        kinds = [t.kind for t in result.transitions]
        assert kinds == ["open", "flip"]
        assert result.transitions[1].bar_ts == idx[62].to_pydatetime()
        assert result.final_state.in_position == -1


# ─────────────────────────────────────────────────────────────────────
# 17. Property: walk() == backtest _apply_exits (defence in depth)
# ─────────────────────────────────────────────────────────────────────


def _walk_full_backtest_style(raw_pos_arr, close_arr, exit_rules, pip_size):
    """Adapter: run walk() over the FULL series (no bookmark) starting
    from flat, and return the per-bar `final` position array the way
    _apply_exits does. Lets us compare on an equal footing."""
    idx = pd.DatetimeIndex(
        [BASE_TS + timedelta(minutes=i) for i in range(len(raw_pos_arr))],
        tz="UTC",
    )
    raw = pd.Series(raw_pos_arr.astype(int), index=idx)
    close = pd.Series(close_arr.astype(float), index=idx)
    # walk() takes ONLY bars > bookmark; passing bookmark just before
    # bar 0 walks all bars.
    result = walk(
        raw, close, _flat(), BASE_TS - timedelta(seconds=1), exit_rules, pip_size
    )
    # Reconstruct the per-bar "final position" array from transitions.
    final = np.zeros(len(raw_pos_arr), dtype=np.int8)
    pos = 0
    ti = 0
    for i, ts in enumerate(idx):
        # Advance the transition pointer up to this bar.
        while (
            ti < len(result.transitions)
            and result.transitions[ti].bar_ts <= ts.to_pydatetime()
        ):
            t = result.transitions[ti]
            if t.kind == "open" or t.kind == "flip":
                pos = t.direction
            else:  # close
                pos = 0
            ti += 1
        final[i] = pos
    return final


class TestPropertyBacktestParity:
    """Feed the same inputs to walk() and _apply_exits, assert same
    per-bar final position array. Catches drift between the two
    implementations."""

    @pytest.mark.parametrize("seed", list(range(20)))
    def test_walk_matches_apply_exits_on_random_input(self, seed):
        rng = np.random.default_rng(seed)
        n = 200
        # Random raw_pos with some autocorrelation to look strategy-ish.
        raw = rng.choice([-1, 0, 0, 0, 0, 1], size=n).astype(int)
        # Random walk price starting at 1.15.
        close = 1.15 + rng.normal(0, 0.001, size=n).cumsum()
        rules = _rules(
            stop_pips=20, target_pips=40, min_hold=5, max_hold=100, opposite_signal=True
        )

        # walk() output (per-bar final position reconstructed):
        walk_final = _walk_full_backtest_style(raw, close, rules, PIP)

        # _apply_exits over the SAME raw_pos array — note that _apply_exits
        # takes raw_pos already shifted (it's what the compiler passes it).
        # For this parity test we treat raw already-shifted so both sides
        # see identical inputs.
        applyexit_final, _reasons = _apply_exits(
            raw.astype(np.int8), close.astype(float), rules, PIP
        )

        assert (walk_final == applyexit_final).all(), (
            f"seed={seed}: walk() and _apply_exits diverge.\n"
            f"walk:        {walk_final.tolist()}\n"
            f"apply_exits: {applyexit_final.tolist()}"
        )
