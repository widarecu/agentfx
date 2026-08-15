"""Sequential per-bar walk for the paper runner (v0.7.0 — Wilson UAT
2026-08-06 fidelity fix).

Backtest is the ground truth. Its `_apply_exits` walks bars one at a
time and applies stop / target / max_hold / min_hold / opposite_flip in
a deterministic state machine. The paper runner must produce the same
trade sequence for the same OHLC + raw-signal input, otherwise
backtest-validated genome metrics don't transfer to live execution.

Before this module, the runner used per-tick single-bar evaluation
gated by ad-hoc early-returns. When the position was inside min_hold,
the tick returned early without updating the bookmark
(`last_evaluated_bar_ts`) — so signals fired during the hold window
were silently queued in `raw_entries`. When min_hold finally expired,
`fired.iloc[0]` picked the OLDEST queued signal and executed it,
opening the paper position at the wrong bar (and wrong price). Backtest
never queues; it holds silently through min_hold and only reacts to
signals from bars AFTER the gate opens.

`walk()` here is a straight port of the same state machine used by
`_apply_exits` (see src/agentfx/core/dsl/compiler.py:220–291),
adapted for the paper runner's callsite (resumes from persisted state,
returns transitions the caller executes against the broker). Every
divergence from `_apply_exits` semantics is a bug in this module.

The paper runner calls this once per tick with the bars that closed
since the last successful tick (`bookmark_ts + 1` to newest). In the
common case there are 0 or 1 such bars; after a stale-signal window
clears there may be several.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

import pandas as pd

from agentfx.core.dsl.grammar import ExitRules


TransitionKind = Literal["open", "close", "flip"]
TransitionReason = Literal["entry", "stop", "target", "max_hold", "opposite_flip"]
Direction = Literal[-1, 0, 1]


@dataclass(frozen=True)
class WalkState:
    """Persisted state carried between ticks.

    - `in_position` mirrors OANDA's net-units sign at the start of the
      tick. 0 = flat, +1 = LONG, -1 = SHORT.
    - `entry_close` is the close price at the bar the position opened;
      SL/TP distances are measured from this price. None when flat.
    - `bars_held` is the count of complete bars since entry. Increments
      by 1 per walked bar during hold. Resets to 0 on any close/flip.
      Carried in state (rather than recomputed from entry_bar_ts) so
      the walk is a pure function of its inputs — no DB access.
    """

    in_position: Direction
    entry_close: float | None
    bars_held: int


@dataclass(frozen=True)
class Transition:
    """One state change that happened at a specific bar during the walk.

    Callers (paper_runner._signal_tick) turn transitions into broker
    calls + Trade rows. Multiple transitions in one WalkResult only
    happen after a real ingestion outage (many bars catch up in one
    tick); the normal case is 0 or 1.
    """

    bar_ts: datetime
    kind: TransitionKind
    direction: Direction  # sign of the position AFTER this transition
    reason: TransitionReason
    close_px: float  # close price at bar_ts — used for exit_price / entry_close


@dataclass(frozen=True)
class WalkResult:
    """Return value of `walk()`. Everything the caller needs to
    execute the walk's decisions against the broker and to persist a
    new bookmark."""

    final_state: WalkState
    transitions: list[Transition]
    last_bar_ts: datetime | None  # for `last_evaluated_bar_ts` bookmark


def walk(
    raw_entries: pd.Series,
    close_series: pd.Series,
    start_state: WalkState,
    bookmark_ts: datetime | None,
    exit_rules: ExitRules,
    pip_size: float,
    *,
    high_series: pd.Series | None = None,
    low_series: pd.Series | None = None,
) -> WalkResult:
    """Walk bars strictly newer than `bookmark_ts` through the strategy
    state machine. Returns the terminal state, all transitions along
    the way, and the timestamp of the last bar walked.

    The state machine is a bar-for-bar port of `_apply_exits` in
    `core/dsl/compiler.py`. Key invariants (verified in tests):

    - During `bars_held < min_hold_bars`, no exit path fires (stop /
      target / max_hold / opposite_flip are ALL suppressed). Hold
      through, increment bars_held.
    - Once min_hold is met, exits are checked in fixed order per bar:
      1. Stop-loss (move_pips <= -stop_pips)
      2. Take-profit (move_pips >= +target_pips)
      3. Max-hold (bars_held >= max_hold_bars)
      4. Opposite flip (raw_pos[t] == -in_position AND
         exit_rules.opposite_signal)
      First match wins; the rest are skipped for that bar.
    - Entry from flat: fires on the first bar in the walk where
      raw_pos[t] != 0.
    - Same-direction re-entry while holding is a no-op (backtest
      doesn't re-enter; the position is already in the wanted
      direction).

    Parameters
    ----------
    raw_entries : pd.Series[int]
        Per-bar entry EVENTS produced by `strategy.raw_entry_signals(df)`.
        Values in {-1, 0, +1}. Index is the bar timestamp (tz-aware
        DatetimeIndex; sorted ascending).
    close_series : pd.Series[float]
        Per-bar close prices with the SAME index as `raw_entries`.
        Used for SL/TP evaluation and entry_close on new positions.
    start_state : WalkState
        The state the runner is in at the START of this walk. Built
        by the caller from the broker position + the open Trade row.
    bookmark_ts : datetime | None
        The `last_evaluated_bar_ts` from `run.parameters`. Only bars
        strictly greater than this are walked. None = no bookmark
        (first tick after a fresh deploy); the walk considers only the
        latest bar in that case, matching legacy fresh-run behaviour.
    exit_rules : ExitRules
        Strategy exit block from the compiled DSL spec. All fields are
        respected; opposite_flip fires only when
        `exit_rules.opposite_signal` is True.
    pip_size : float
        The instrument's price-per-pip (0.01 JPY, 0.0001 non-JPY).
        Used for `move_pips` computation.

    Returns
    -------
    WalkResult
        `final_state` is what the runner should reconcile to the
        broker. `transitions` is the ordered list of state changes;
        for the normal case (0 or 1 new bar) there is at most 1. The
        caller executes them against the broker (typically just the
        last one; intermediate flips during a multi-bar catch-up
        cannot be back-executed on OANDA and should be logged for
        audit only). `last_bar_ts` is the bookmark to persist.
    """
    if len(raw_entries) != len(close_series):
        raise ValueError(
            f"raw_entries ({len(raw_entries)}) and close_series "
            f"({len(close_series)}) must be the same length"
        )
    if len(raw_entries) == 0:
        return WalkResult(final_state=start_state, transitions=[], last_bar_ts=None)
    if not raw_entries.index.equals(close_series.index):
        raise ValueError("raw_entries and close_series must share an index")

    # Defensive: tests (and pre-eval_mode ExitRules deserialisers)
    # may pass a bare SimpleNamespace without the new field. Default
    # to close-mode, which is the legacy behaviour.
    wick = getattr(exit_rules, "eval_mode", "close") == "wick"
    if wick:
        if high_series is None or low_series is None:
            raise ValueError("eval_mode='wick' requires high_series and low_series")
        if not high_series.index.equals(
            close_series.index
        ) or not low_series.index.equals(close_series.index):
            raise ValueError("high_series/low_series must share close_series index")

    # Filter to bars strictly greater than bookmark.
    if bookmark_ts is not None:
        # pandas DatetimeIndex comparison works with tz-aware Timestamp.
        cutoff = pd.Timestamp(bookmark_ts)
        mask = raw_entries.index > cutoff
        raw_entries = raw_entries[mask]
        close_series = close_series[mask]
        if wick:
            high_series = high_series[mask]
            low_series = low_series[mask]
    else:
        # No bookmark → this is the first tick after a fresh deploy.
        # Only consider the latest bar; don't backfill history. Matches
        # the pre-refactor `raw_entries.tail(1)` fresh-run behaviour.
        raw_entries = raw_entries.tail(1)
        close_series = close_series.tail(1)
        if wick:
            high_series = high_series.tail(1)
            low_series = low_series.tail(1)

    if len(raw_entries) == 0:
        # Bookmark was already at or past the latest bar. No-op tick.
        return WalkResult(final_state=start_state, transitions=[], last_bar_ts=None)

    stop_pips = float(exit_rules.stop_pips)
    target_pips = float(exit_rules.target_pips)
    min_hold_bars = int(exit_rules.min_hold_bars)
    max_hold_bars = int(exit_rules.max_hold_bars)
    flip_on_opposite = bool(exit_rules.opposite_signal)
    stop_price_dist = stop_pips * pip_size
    target_price_dist = target_pips * pip_size

    state = start_state
    transitions: list[Transition] = []

    for bar_ts_val, raw_val in raw_entries.items():
        bar_ts = _to_pydatetime(bar_ts_val)
        raw = int(raw_val)
        close_px = float(close_series.loc[bar_ts_val])

        if state.in_position != 0:
            if state.entry_close is None:
                raise ValueError(
                    "walk() invariant broken: in_position != 0 with entry_close None"
                )

            # Advance bars_held for THIS bar before gating. bars_held
            # in _apply_exits is `t - entry_bar` where t is the current
            # array position and entry_bar is the position when we
            # opened; incrementing before the gate reproduces that
            # arithmetic. See test_walk_min_hold_boundary for the
            # off-by-one check.
            new_bars_held = state.bars_held + 1

            # Min-hold gate: suppress ALL exit paths while under it.
            if new_bars_held < min_hold_bars:
                state = replace(state, bars_held=new_bars_held)
                continue

            # ── Stop / target evaluation ────────────────────────────
            # Two modes (compiler.py _apply_exits mirrors this branch):
            #   close: check bar close vs threshold.
            #   wick:  check bar low (long SL / short TP) or high
            #          (long TP / short SL); fill at exact threshold.
            stop_hit = False
            target_hit = False
            exit_px = close_px  # default fill price (max_hold/flip)

            if wick:
                bar_high = float(high_series.loc[bar_ts_val])
                bar_low = float(low_series.loc[bar_ts_val])
                if state.in_position == 1:
                    long_sl = state.entry_close - stop_price_dist
                    long_tp = state.entry_close + target_price_dist
                    if bar_low <= long_sl:
                        stop_hit = True
                        exit_px = long_sl
                    elif bar_high >= long_tp:
                        target_hit = True
                        exit_px = long_tp
                else:  # SHORT
                    short_sl = state.entry_close + stop_price_dist
                    short_tp = state.entry_close - target_price_dist
                    if bar_high >= short_sl:
                        stop_hit = True
                        exit_px = short_sl
                    elif bar_low <= short_tp:
                        target_hit = True
                        exit_px = short_tp
            else:
                move_pips = (close_px - state.entry_close) / pip_size
                if state.in_position == -1:
                    move_pips = -move_pips
                if move_pips <= -stop_pips:
                    stop_hit = True
                elif move_pips >= target_pips:
                    target_hit = True

            # Exits, in fixed order. First match wins.
            if stop_hit:
                transitions.append(
                    Transition(
                        bar_ts=bar_ts,
                        kind="close",
                        direction=0,
                        reason="stop",
                        close_px=exit_px,
                    )
                )
                state = WalkState(in_position=0, entry_close=None, bars_held=0)
                continue

            if target_hit:
                transitions.append(
                    Transition(
                        bar_ts=bar_ts,
                        kind="close",
                        direction=0,
                        reason="target",
                        close_px=exit_px,
                    )
                )
                state = WalkState(in_position=0, entry_close=None, bars_held=0)
                continue

            if new_bars_held >= max_hold_bars:
                transitions.append(
                    Transition(
                        bar_ts=bar_ts,
                        kind="close",
                        direction=0,
                        reason="max_hold",
                        close_px=close_px,
                    )
                )
                state = WalkState(in_position=0, entry_close=None, bars_held=0)
                continue

            if flip_on_opposite and raw == -state.in_position:
                # Close current + open opposite in the same bar. New
                # position's entry_close = this bar's close. bars_held
                # resets to 0.
                transitions.append(
                    Transition(
                        bar_ts=bar_ts,
                        kind="flip",
                        direction=raw,  # type: ignore[arg-type]
                        reason="opposite_flip",
                        close_px=close_px,
                    )
                )
                state = WalkState(
                    in_position=raw,  # type: ignore[arg-type]
                    entry_close=close_px,
                    bars_held=0,
                )
                continue

            # No exit fired → hold. Persist the incremented bars_held.
            state = replace(state, bars_held=new_bars_held)
            continue

        # in_position == 0 → look for entry.
        if raw != 0:
            transitions.append(
                Transition(
                    bar_ts=bar_ts,
                    kind="open",
                    direction=raw,  # type: ignore[arg-type]
                    reason="entry",
                    close_px=close_px,
                )
            )
            state = WalkState(
                in_position=raw,  # type: ignore[arg-type]
                entry_close=close_px,
                bars_held=0,
            )
        # else: flat + no signal → nothing to do this bar. Fall through.

    return WalkResult(
        final_state=state,
        transitions=transitions,
        last_bar_ts=_to_pydatetime(raw_entries.index[-1]),
    )


def _to_pydatetime(ts) -> datetime:
    """pandas Timestamps have `.to_pydatetime()`; raw datetimes don't.
    Callers use the returned datetime for JSON serialization
    (bookmark persistence). Also ensures tz-aware."""
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    else:
        dt = ts
    if isinstance(dt, datetime) and dt.tzinfo is None:
        from datetime import UTC

        dt = dt.replace(tzinfo=UTC)
    return dt
