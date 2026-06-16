"""Walk-forward fold splitter tests.

These guarantee the integrity properties the validator (US-004) depends
on: contiguous, time-ordered, non-overlapping, exactly-one-OOS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentfx.core.validation import (
    WalkForwardFold,
    make_rolling_walk_forward_folds,
    make_walk_forward_folds,
    oos_window,
    training_window,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# ─────────────────────────────────────────────────────────────────────────
# Basic shape
# ─────────────────────────────────────────────────────────────────────────


class TestMakeWalkForwardFolds:
    def test_returns_k_folds(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        assert len(folds) == 4

    def test_default_k_is_four(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1))
        assert len(folds) == 4

    def test_indexes_are_zero_based_and_ordered(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        assert [f.index for f in folds] == [0, 1, 2, 3]

    def test_only_last_fold_is_oos(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        oos = [f for f in folds if f.is_oos]
        assert len(oos) == 1
        assert oos[0].index == 3  # last

    def test_first_fold_starts_at_input_start(self) -> None:
        start = _utc(2024, 1, 1)
        folds = make_walk_forward_folds(start, _utc(2024, 5, 1), k=4)
        assert folds[0].start == start

    def test_last_fold_ends_at_input_end_exactly(self) -> None:
        # Pin guarantee: float arithmetic on timedeltas can leave gaps;
        # the last fold's end must equal the input end exactly.
        end = _utc(2024, 5, 1)
        folds = make_walk_forward_folds(_utc(2024, 1, 1), end, k=4)
        assert folds[-1].end == end

    def test_folds_are_contiguous(self) -> None:
        # Each fold's end must equal the next fold's start
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        for i in range(len(folds) - 1):
            assert folds[i].end == folds[i + 1].start

    def test_folds_are_non_overlapping(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        for i in range(len(folds) - 1):
            # Strictly: fold[i].end <= fold[i+1].start (contiguous case is fine)
            assert folds[i].end <= folds[i + 1].start

    def test_folds_have_roughly_equal_duration(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        durations = [(f.end - f.start) for f in folds]
        # All within 1 second of each other (microsecond rounding only)
        max_diff = max(durations) - min(durations)
        assert max_diff <= timedelta(seconds=1)

    def test_k_two_produces_one_train_one_oos(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 3, 1), k=2)
        assert len(folds) == 2
        assert folds[0].is_oos is False
        assert folds[1].is_oos is True

    def test_k_eight(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 9, 1), k=8)
        assert len(folds) == 8
        oos = [f for f in folds if f.is_oos]
        assert len(oos) == 1


# ─────────────────────────────────────────────────────────────────────────
# Argument validation
# ─────────────────────────────────────────────────────────────────────────


class TestArgumentValidation:
    def test_k_below_two_raises(self) -> None:
        with pytest.raises(ValueError, match="k must be >= 2"):
            make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=1)
        with pytest.raises(ValueError, match="k must be >= 2"):
            make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=0)

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >"):
            make_walk_forward_folds(_utc(2024, 5, 1), _utc(2024, 1, 1), k=4)

    def test_end_equal_to_start_raises(self) -> None:
        same = _utc(2024, 1, 1)
        with pytest.raises(ValueError, match="must be >"):
            make_walk_forward_folds(same, same, k=4)

    def test_naive_start_raises(self) -> None:
        naive_start = datetime(2024, 1, 1)  # no tzinfo
        with pytest.raises(ValueError, match="tz-aware"):
            make_walk_forward_folds(naive_start, _utc(2024, 5, 1), k=4)

    def test_naive_end_raises(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            make_walk_forward_folds(_utc(2024, 1, 1), datetime(2024, 5, 1), k=4)


# ─────────────────────────────────────────────────────────────────────────
# Training / OOS window helpers
# ─────────────────────────────────────────────────────────────────────────


class TestTrainingWindow:
    def test_returns_first_to_oos_boundary(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        train_start, train_end = training_window(folds)
        # Training range = first 3 folds, ending at the OOS boundary
        assert train_start == folds[0].start
        assert train_end == folds[2].end  # = folds[3].start

    def test_does_not_overlap_oos(self) -> None:
        # The integrity guarantee: training window's end must equal the
        # OOS fold's start exactly. No overlap.
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        _, train_end = training_window(folds)
        oos_start, _ = oos_window(folds)
        assert train_end == oos_start

    def test_empty_folds_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            training_window([])


class TestOosWindow:
    def test_returns_last_fold_range(self) -> None:
        folds = make_walk_forward_folds(_utc(2024, 1, 1), _utc(2024, 5, 1), k=4)
        oos_start, oos_end = oos_window(folds)
        assert oos_start == folds[-1].start
        assert oos_end == folds[-1].end

    def test_empty_folds_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            oos_window([])

    def test_no_oos_fold_raises(self) -> None:
        # Construct a malformed list manually
        bogus = [
            WalkForwardFold(0, _utc(2024, 1, 1), _utc(2024, 2, 1), is_oos=False),
            WalkForwardFold(1, _utc(2024, 2, 1), _utc(2024, 3, 1), is_oos=False),
        ]
        with pytest.raises(ValueError, match="at least 1"):
            oos_window(bogus)

    def test_returns_union_of_multiple_oos_folds(self) -> None:
        # v0.4.0 rolling design: multiple contiguous OOS folds.
        # oos_window now returns the union (first OOS start → last OOS end).
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1),
            end=_utc(2024, 12, 31),
            n_train_folds=3,
            n_oos_folds=5,
        )
        oos_folds = [f for f in folds if f.is_oos]
        oos_start, oos_end = oos_window(folds)
        assert oos_start == oos_folds[0].start
        assert oos_end == oos_folds[-1].end


# ─────────────────────────────────────────────────────────────────────────
# Rolling walk-forward (v0.4.0)
# ─────────────────────────────────────────────────────────────────────────


class TestMakeRollingWalkForwardFolds:
    def test_default_returns_three_train_plus_five_oos(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        assert len(folds) == 8
        train = [f for f in folds if not f.is_oos]
        oos = [f for f in folds if f.is_oos]
        assert len(train) == 3
        assert len(oos) == 5

    def test_indexes_are_zero_based_and_ordered(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        assert [f.index for f in folds] == list(range(8))

    def test_training_folds_come_before_oos_folds(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        for i in range(3):
            assert folds[i].is_oos is False
        for i in range(3, 8):
            assert folds[i].is_oos is True

    def test_train_region_is_seventy_percent_by_default(self) -> None:
        start = _utc(2024, 1, 1)
        end = _utc(2024, 12, 31)
        folds = make_rolling_walk_forward_folds(start=start, end=end)
        train_end = folds[2].end
        oos_start = folds[3].start
        # Training region ends exactly where OOS starts — contiguous, no gap
        assert train_end == oos_start
        # Training span is ~70% of total (allow rounding for sub-second)
        total = (end - start).total_seconds()
        train_span = (train_end - start).total_seconds()
        assert abs(train_span / total - 0.7) < 0.001

    def test_first_train_fold_starts_at_window_start(self) -> None:
        start = _utc(2024, 1, 1)
        folds = make_rolling_walk_forward_folds(start=start, end=_utc(2024, 12, 31))
        assert folds[0].start == start

    def test_last_oos_fold_ends_exactly_at_window_end(self) -> None:
        end = _utc(2024, 12, 31)
        folds = make_rolling_walk_forward_folds(start=_utc(2024, 1, 1), end=end)
        assert folds[-1].end == end

    def test_all_folds_are_contiguous(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        for prev, curr in zip(folds, folds[1:]):
            assert prev.end == curr.start

    def test_oos_folds_have_roughly_equal_duration(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        oos = [f for f in folds if f.is_oos]
        durations = [(f.end - f.start).total_seconds() for f in oos]
        # All within 0.1% of each other
        assert max(durations) - min(durations) < 0.001 * max(durations)

    def test_custom_fold_counts(self) -> None:
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1),
            end=_utc(2024, 12, 31),
            n_train_folds=4,
            n_oos_folds=3,
        )
        train = [f for f in folds if not f.is_oos]
        oos = [f for f in folds if f.is_oos]
        assert len(train) == 4
        assert len(oos) == 3

    def test_custom_train_frac(self) -> None:
        start = _utc(2024, 1, 1)
        end = _utc(2024, 12, 31)
        folds = make_rolling_walk_forward_folds(start=start, end=end, train_frac=0.5)
        train_end = folds[2].end
        total = (end - start).total_seconds()
        train_span = (train_end - start).total_seconds()
        assert abs(train_span / total - 0.5) < 0.001

    def test_training_window_helper_works_with_rolling(self) -> None:
        # training_window() should return the union of all train folds
        folds = make_rolling_walk_forward_folds(
            start=_utc(2024, 1, 1), end=_utc(2024, 12, 31)
        )
        train_start, train_end = training_window(folds)
        assert train_start == folds[0].start
        assert train_end == folds[2].end  # last train fold

    def test_zero_train_folds_raises(self) -> None:
        with pytest.raises(ValueError, match="n_train_folds"):
            make_rolling_walk_forward_folds(
                start=_utc(2024, 1, 1),
                end=_utc(2024, 12, 31),
                n_train_folds=0,
            )

    def test_zero_oos_folds_raises(self) -> None:
        with pytest.raises(ValueError, match="n_oos_folds"):
            make_rolling_walk_forward_folds(
                start=_utc(2024, 1, 1),
                end=_utc(2024, 12, 31),
                n_oos_folds=0,
            )

    def test_train_frac_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="train_frac"):
            make_rolling_walk_forward_folds(
                start=_utc(2024, 1, 1),
                end=_utc(2024, 12, 31),
                train_frac=1.0,
            )
        with pytest.raises(ValueError, match="train_frac"):
            make_rolling_walk_forward_folds(
                start=_utc(2024, 1, 1),
                end=_utc(2024, 12, 31),
                train_frac=0.0,
            )

    def test_naive_timestamps_raise(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            make_rolling_walk_forward_folds(
                start=datetime(2024, 1, 1),
                end=_utc(2024, 12, 31),
            )

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >"):
            make_rolling_walk_forward_folds(
                start=_utc(2024, 12, 31), end=_utc(2024, 1, 1)
            )
