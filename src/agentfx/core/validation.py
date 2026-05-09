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

    Always the final fold's range, but exposed as a helper for symmetry
    with `training_window()` and to centralize the OOS-selection rule.
    """
    if not folds:
        raise ValueError("folds must be non-empty")
    oos_folds = [f for f in folds if f.is_oos]
    if len(oos_folds) != 1:
        raise ValueError(
            f"expected exactly 1 OOS fold, got {len(oos_folds)} — "
            "make_walk_forward_folds() should always produce 1"
        )
    return oos_folds[0].start, oos_folds[0].end


__all__ = [
    "WalkForwardFold",
    "make_walk_forward_folds",
    "oos_window",
    "training_window",
]
