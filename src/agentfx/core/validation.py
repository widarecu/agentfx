"""Walk-forward fold construction for the v0.3.0 Validator (US-004).

Splits a historical window into K contiguous, time-ordered, non-overlapping
folds. The last fold is the OOS test the GA never sees during evolution —
this module is the single source of truth for that split, so any code path
that uses fold[k-1] for OOS evaluation gets the same boundaries.

Time-ordered contiguous splitting is mandatory for time-series — random
shuffled k-fold leaks future information into training and is wrong here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WalkForwardFold:
    """A single fold in a walk-forward split.

    `is_oos=True` only on the final fold (index k-1). The orchestrator's
    fitness evaluation must NOT see is_oos=True folds; the validator's
    evaluation operates on those folds only. This boundary is the
    integrity guarantee of the v0.3.0 evaluation pipeline.
    """

    index: int
    start: datetime
    end: datetime
    is_oos: bool


def make_walk_forward_folds(
    start: datetime,
    end: datetime,
    k: int = 4,
) -> list[WalkForwardFold]:
    """Split [start, end) into K contiguous time-ordered folds.

    Each fold's `[start, end)` interval is a half-open window. The folds
    cover the input range exactly: the first fold's start equals `start`,
    the last fold's end equals `end`, and adjacent folds share their
    boundary timestamp.

    The final fold is flagged `is_oos=True`. The first K-1 folds are
    training; the validator runs the genome on the OOS fold only.

    Args:
        start: Window start (inclusive). Must be tz-aware.
        end: Window end (exclusive). Must be tz-aware and > start.
        k: Number of folds. Default 4 per the plan; minimum 2 (one train +
           one OOS). Maximum is unbounded but K > ~10 dilutes per-fold data
           below useful sizes for FX backtests.

    Returns:
        List of K `WalkForwardFold` instances ordered by `index` (0..k-1).

    Raises:
        ValueError: if k < 2, end <= start, or either timestamp is naive.
    """
    if k < 2:
        raise ValueError(f"k must be >= 2 (got {k}); need at least train + OOS")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be tz-aware datetimes")
    if end <= start:
        raise ValueError(f"end ({end}) must be > start ({start})")

    duration = end - start
    fold_duration = duration / k

    folds: list[WalkForwardFold] = []
    for i in range(k):
        fold_start = start + (fold_duration * i)
        # Pin the last fold's end to `end` exactly to avoid floating-point
        # gaps from successive timedelta multiplication.
        fold_end = end if i == k - 1 else start + (fold_duration * (i + 1))
        folds.append(
            WalkForwardFold(
                index=i,
                start=fold_start,
                end=fold_end,
                is_oos=(i == k - 1),
            )
        )
    return folds


def training_window(folds: list[WalkForwardFold]) -> tuple[datetime, datetime]:
    """The (start, end) the GA may use for fitness evaluation — i.e. the
    union of all non-OOS folds.

    For contiguous folds this equals (folds[0].start, folds[-2].end). The
    helper exists so callers can stop manually slicing the list and risking
    off-by-one against the OOS boundary.
    """
    if not folds:
        raise ValueError("folds must be non-empty")
    train_folds = [f for f in folds if not f.is_oos]
    if not train_folds:
        raise ValueError("no training folds (every fold is OOS?)")
    return train_folds[0].start, train_folds[-1].end


def oos_window(folds: list[WalkForwardFold]) -> tuple[datetime, datetime]:
    """The (start, end) the validator uses for OOS evaluation.

    Returns the union of all OOS folds: `(first_oos.start, last_oos.end)`.
    For the legacy K=4 design (one OOS fold), this is just that fold's
    range. For the v0.4.0 rolling design (5 OOS folds in the last 30%),
    this is the contiguous last-30% span — exactly what the on-demand
    backtest endpoints expect to slice.
    """
    if not folds:
        raise ValueError("folds must be non-empty")
    oos_folds = [f for f in folds if f.is_oos]
    if not oos_folds:
        raise ValueError(
            "no OOS folds — make_walk_forward_folds() should produce at least 1"
        )
    return oos_folds[0].start, oos_folds[-1].end


def make_rolling_walk_forward_folds(
    start: datetime,
    end: datetime,
    *,
    n_train_folds: int = 3,
    n_oos_folds: int = 5,
    train_frac: float = 0.7,
) -> list[WalkForwardFold]:
    """v0.4.0 rolling walk-forward — N training folds + M OOS folds.

    Splits `[start, end)` into two regions:

    - Training region — first `train_frac` of the window, divided into
      `n_train_folds` contiguous non-overlapping training folds. The GA's
      fitness function evaluates over these (one backtest per fold; the
      fold-consistency penalty rewards strategies that perform similarly
      across them).
    - OOS region — the remaining `(1 - train_frac)`, divided into
      `n_oos_folds` contiguous non-overlapping OOS folds. The validator
      runs one backtest per OOS fold; the median Sharpe across them is the
      promotion signal.

    Why two regions and not full overlap: keeping the OOS region strictly
    after the training region preserves the walk-forward integrity
    guarantee — the GA never sees data from after the training region's
    end. Within each region the folds are contiguous (no overlap, no gap).
    Five OOS folds gives a meaningful median signal at modest extra cost
    (~25% more backtest calls than the legacy K=4 design).

    Returned folds are ordered by `index` from 0 (first training fold) to
    `n_train_folds + n_oos_folds - 1` (last OOS fold). Training folds have
    `is_oos=False`; OOS folds have `is_oos=True`.

    Args:
        start: Window start (inclusive). Tz-aware.
        end: Window end (exclusive). Tz-aware and > start.
        n_train_folds: Number of training folds. Default 3.
        n_oos_folds: Number of OOS folds. Default 5.
        train_frac: Fraction of the total window allocated to training.
            Default 0.7 (70% train / 30% OOS).

    Raises:
        ValueError: on bad inputs (negative fold counts, train_frac
            outside (0, 1), naive timestamps, or end <= start).
    """
    if n_train_folds < 1:
        raise ValueError(f"n_train_folds must be >= 1 (got {n_train_folds})")
    if n_oos_folds < 1:
        raise ValueError(f"n_oos_folds must be >= 1 (got {n_oos_folds})")
    if not (0.0 < train_frac < 1.0):
        raise ValueError(f"train_frac must be in (0, 1) exclusive (got {train_frac})")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be tz-aware datetimes")
    if end <= start:
        raise ValueError(f"end ({end}) must be > start ({start})")

    duration = end - start
    train_end = start + duration * train_frac
    train_fold_duration = (train_end - start) / n_train_folds
    oos_fold_duration = (end - train_end) / n_oos_folds

    folds: list[WalkForwardFold] = []
    for i in range(n_train_folds):
        fold_start = start + train_fold_duration * i
        fold_end = (
            train_end
            if i == n_train_folds - 1
            else start + train_fold_duration * (i + 1)
        )
        folds.append(
            WalkForwardFold(index=i, start=fold_start, end=fold_end, is_oos=False)
        )

    for j in range(n_oos_folds):
        fold_start = train_end + oos_fold_duration * j
        fold_end = (
            end if j == n_oos_folds - 1 else train_end + oos_fold_duration * (j + 1)
        )
        folds.append(
            WalkForwardFold(
                index=n_train_folds + j,
                start=fold_start,
                end=fold_end,
                is_oos=True,
            )
        )

    return folds


__all__ = [
    "WalkForwardFold",
    "make_rolling_walk_forward_folds",
    "make_walk_forward_folds",
    "oos_window",
    "training_window",
]
