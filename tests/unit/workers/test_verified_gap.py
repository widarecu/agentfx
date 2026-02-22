"""Unit tests for verified empty gap tracking and integration with detect_and_heal_gaps."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from src.agentfx.api.core.config import settings
from src.agentfx.api.models.verified_gap import VerifiedEmptyGap
from src.agentfx.api.services.verified_gap import VerifiedGapService
from src.agentfx.api.workers.candle_ingestion import (
    detect_and_heal_gaps,
)


@pytest.mark.unit
class TestVerifiedGapService:
    """Tests for the VerifiedGapService CRUD operations."""

    def _make_mock_db(self):
        """Create a mock database session."""
        return MagicMock()

    def test_mark_gap_verified_creates_new_record(self):
        """When no existing record, mark_gap_verified creates a new VerifiedEmptyGap."""
        db = self._make_mock_db()
        db.query.return_value.filter.return_value.first.return_value = None

        gap_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)

        VerifiedGapService.mark_gap_verified(
            db, "oanda", "EUR_USD", "M1", gap_start, gap_end, reason="no_data"
        )

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert isinstance(added, VerifiedEmptyGap)
        assert added.exchange == "oanda"
        assert added.instrument == "EUR_USD"
        assert added.timeframe == "M1"
        assert added.gap_start == gap_start
        assert added.gap_end == gap_end
        assert added.gap_duration_seconds == 300.0
        assert added.attempt_count == 1
        assert added.reason == "no_data"
        db.commit.assert_called_once()

    def test_mark_gap_verified_updates_existing_record(self):
        """When record exists, mark_gap_verified increments attempt_count and updates timestamp."""
        db = self._make_mock_db()
        existing = MagicMock()
        existing.attempt_count = 2
        db.query.return_value.filter.return_value.first.return_value = existing

        gap_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)

        VerifiedGapService.mark_gap_verified(
            db, "oanda", "EUR_USD", "M1", gap_start, gap_end, reason="no_data"
        )

        assert existing.attempt_count == 3
        assert existing.last_verified_at is not None
        assert existing.reason == "no_data"
        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_mark_gap_verified_normalizes_case(self):
        """Instrument and timeframe are uppercased."""
        db = self._make_mock_db()
        db.query.return_value.filter.return_value.first.return_value = None

        gap_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)

        VerifiedGapService.mark_gap_verified(
            db, "oanda", "eur_usd", "m1", gap_start, gap_end
        )

        added = db.add.call_args[0][0]
        assert added.instrument == "EUR_USD"
        assert added.timeframe == "M1"

    def test_get_verified_gaps_returns_set_of_tuples(self):
        """get_verified_gaps returns a set of (gap_start, gap_end) tuples."""
        db = self._make_mock_db()
        gap1_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap1_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)
        gap2_start = datetime(2023, 6, 2, 3, 0, tzinfo=UTC)
        gap2_end = datetime(2023, 6, 2, 3, 5, tzinfo=UTC)

        row1 = MagicMock()
        row1.gap_start = gap1_start
        row1.gap_end = gap1_end
        row2 = MagicMock()
        row2.gap_start = gap2_start
        row2.gap_end = gap2_end

        db.query.return_value.filter.return_value.all.return_value = [row1, row2]

        result = VerifiedGapService.get_verified_gaps(db, "oanda", "EUR_USD", "M1")

        assert result == {(gap1_start, gap1_end), (gap2_start, gap2_end)}

    def test_get_verified_gaps_empty_when_no_records(self):
        """get_verified_gaps returns empty set when no verified gaps exist."""
        db = self._make_mock_db()
        db.query.return_value.filter.return_value.all.return_value = []

        result = VerifiedGapService.get_verified_gaps(db, "oanda", "EUR_USD", "M1")

        assert result == set()

    def test_remove_healed_gap_returns_true_when_deleted(self):
        """remove_healed_gap returns True when a record was deleted."""
        db = self._make_mock_db()
        db.query.return_value.filter.return_value.delete.return_value = 1

        gap_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)

        result = VerifiedGapService.remove_healed_gap(
            db, "oanda", "EUR_USD", "M1", gap_start, gap_end
        )

        assert result is True
        db.commit.assert_called_once()

    def test_remove_healed_gap_returns_false_when_not_found(self):
        """remove_healed_gap returns False when no record was found."""
        db = self._make_mock_db()
        db.query.return_value.filter.return_value.delete.return_value = 0

        gap_start = datetime(2023, 6, 1, 3, 0, tzinfo=UTC)
        gap_end = datetime(2023, 6, 1, 3, 5, tzinfo=UTC)

        result = VerifiedGapService.remove_healed_gap(
            db, "oanda", "EUR_USD", "M1", gap_start, gap_end
        )

        assert result is False
        db.commit.assert_not_called()


@pytest.mark.unit
class TestDetectAndHealGapsWithVerifiedGaps:
    """Tests for detect_and_heal_gaps integration with verified gap tracking."""

    @pytest.fixture
    def mock_deps(self):
        """Mock all dependencies for detect_and_heal_gaps including VerifiedGapService."""
        with (
            patch("src.agentfx.api.services.market_data.MarketDataService") as mock_mds,
            patch("src.agentfx.api.core.database.SessionLocal") as mock_session,
            patch(
                "src.agentfx.api.workers.candle_ingestion.is_market_closure",
                return_value=(False, ""),
            ),
            patch(
                "src.agentfx.api.workers.candle_ingestion.fetch_candles_range"
            ) as mock_fetch,
            patch(
                "src.agentfx.api.workers.candle_ingestion.store_candles",
                return_value=1,
            ) as mock_store,
            patch(
                "src.agentfx.api.services.verified_gap.VerifiedGapService"
            ) as mock_vgs,
        ):
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            # Default: no verified gaps
            mock_vgs.get_verified_gaps.return_value = set()
            yield {
                "mds": mock_mds,
                "db": mock_db,
                "fetch": mock_fetch,
                "store": mock_store,
                "vgs": mock_vgs,
            }

    def _make_timestamps_with_gaps(self, gap_count, base=None):
        """Create timestamps with `gap_count` gaps separated by enough normal candles
        to prevent merging. Each gap is ~10 minutes for M1 (well above 5-min threshold)."""
        base = base or datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = []
        offset = 0
        for i in range(gap_count):
            timestamps.append(base + timedelta(minutes=offset))
            offset += 1
            # 10 min gap (> 5 min threshold for M1)
            offset += 10
            timestamps.append(base + timedelta(minutes=offset))
            offset += 1
            # 65 normal candles to prevent merging (> 60 * 60s merge threshold)
            for j in range(65):
                timestamps.append(base + timedelta(minutes=offset))
                offset += 1
        return timestamps

    def test_verified_gaps_skipped_during_detection(self, mock_deps):
        """Gaps in verified_set are skipped and not processed."""
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = self._make_timestamps_with_gaps(3, base)
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        # Gaps produced by _make_timestamps_with_gaps(3) with 65 normal candle separation:
        # Gap 1: base+0min → base+11min
        # Gap 2: base+77min → base+88min
        # Gap 3: base+154min → base+165min
        # Mark gap 2 as verified
        gap2_start = base + timedelta(minutes=77)
        gap2_end = base + timedelta(minutes=88)
        mock_deps["vgs"].get_verified_gaps.return_value = {(gap2_start, gap2_end)}

        mock_deps["fetch"].return_value = [
            {
                "time": "2023-01-01T00:05:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
        ]

        detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        # 3 gaps detected but 1 verified → 2 processed
        assert mock_deps["fetch"].call_count == 2

    def test_empty_gap_marked_as_verified_when_no_data(self, mock_deps):
        """When OANDA returns no candles, the gap is marked as verified empty."""
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = self._make_timestamps_with_gaps(1, base)
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        # OANDA returns no data
        mock_deps["fetch"].return_value = []

        detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        mock_deps["vgs"].mark_gap_verified.assert_called_once()
        args = mock_deps["vgs"].mark_gap_verified.call_args
        assert args[1].get(
            "reason", args[0][5] if len(args[0]) > 5 else None
        ) == "no_data" or "no_data" in str(args)

    def test_healed_gap_removed_from_verified(self, mock_deps):
        """When data is successfully stored for a gap, remove it from verified table."""
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = self._make_timestamps_with_gaps(1, base)
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        # OANDA returns data and store succeeds
        mock_deps["fetch"].return_value = [
            {
                "time": "2023-01-01T00:05:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
        ]
        mock_deps["store"].return_value = 3  # 3 candles stored

        detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        mock_deps["vgs"].remove_healed_gap.assert_called_once()

    def test_verified_gaps_allow_progress_through_all_gaps(self, mock_deps):
        """With verified gaps, subsequent health checks process NEW gaps instead of repeating."""
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        # Create 15 gaps, use a limit of 10 to test batching
        timestamps = self._make_timestamps_with_gaps(15, base)
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        # OANDA returns no data for all gaps
        mock_deps["fetch"].return_value = []

        # First run: no verified gaps → processes up to the limit (10)
        mock_deps["vgs"].get_verified_gaps.return_value = set()
        with patch.object(settings, "MAX_GAPS_PER_HEALTH_CHECK", 10):
            detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        first_run_count = mock_deps["vgs"].mark_gap_verified.call_count
        assert first_run_count == 10

        # Collect the gaps that were marked as verified in the first run
        verified_from_first_run = set()
        for c in mock_deps["vgs"].mark_gap_verified.call_args_list:
            # Positional args: db, exchange, instrument, timeframe, gap_start, gap_end
            gap_start = c[0][4]
            gap_end = c[0][5]
            verified_from_first_run.add((gap_start, gap_end))

        # Reset mocks for second run
        mock_deps["fetch"].reset_mock()
        mock_deps["vgs"].mark_gap_verified.reset_mock()

        # Second run: those 10 gaps are now verified → processes the remaining 5
        mock_deps["vgs"].get_verified_gaps.return_value = verified_from_first_run
        with patch.object(settings, "MAX_GAPS_PER_HEALTH_CHECK", 10):
            detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        # Should process 5 remaining gaps (15 - 10 = 5)
        assert mock_deps["fetch"].call_count == 5
        assert mock_deps["vgs"].mark_gap_verified.call_count == 5

    def test_store_returns_zero_marked_as_data_exists(self, mock_deps):
        """When OANDA returns data but all candles already exist (store=0),
        gap is marked as verified with reason='data_exists' to prevent re-fetching."""
        base = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = self._make_timestamps_with_gaps(1, base)
        mock_deps["mds"].get_candle_timestamps.return_value = timestamps

        mock_deps["fetch"].return_value = [
            {
                "time": "2023-01-01T00:05:00Z",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": 100,
            }
        ]
        mock_deps["store"].return_value = 0  # All candles already exist

        detect_and_heal_gaps("EUR_USD", "M1", mock_deps["db"])

        # Should mark as verified with reason="data_exists"
        mock_deps["vgs"].mark_gap_verified.assert_called_once()
        call_args = mock_deps["vgs"].mark_gap_verified.call_args
        assert call_args[1].get("reason") == "data_exists" or (
            len(call_args[0]) > 5 and call_args[0][5] == "data_exists"
        )
        # Should NOT remove from verified (nothing to remove)
        mock_deps["vgs"].remove_healed_gap.assert_not_called()


@pytest.mark.unit
class TestVerifiedEmptyGapModel:
    """Tests for the VerifiedEmptyGap SQLAlchemy model."""

    def test_model_tablename(self):
        """Test the table name is correct."""
        assert VerifiedEmptyGap.__tablename__ == "verified_empty_gaps"

    def test_model_repr(self):
        """Test the __repr__ output."""
        gap = VerifiedEmptyGap(
            instrument="EUR_USD",
            timeframe="M1",
            gap_start=datetime(2023, 1, 1, 3, 0, tzinfo=UTC),
            gap_end=datetime(2023, 1, 1, 3, 5, tzinfo=UTC),
        )
        repr_str = repr(gap)
        assert "EUR_USD" in repr_str
        assert "M1" in repr_str

    def test_model_has_required_columns(self):
        """Test that all required columns exist on the model."""
        columns = {c.name for c in VerifiedEmptyGap.__table__.columns}
        expected = {
            "id",
            "exchange",
            "instrument",
            "timeframe",
            "gap_start",
            "gap_end",
            "gap_duration_seconds",
            "attempt_count",
            "first_verified_at",
            "last_verified_at",
            "reason",
        }
        assert expected == columns

    def test_model_has_unique_index(self):
        """Test that the unique composite index exists."""
        indexes = VerifiedEmptyGap.__table__.indexes
        unique_idx = [idx for idx in indexes if idx.unique]
        assert len(unique_idx) == 1
        assert unique_idx[0].name == "idx_verified_gaps_lookup"
