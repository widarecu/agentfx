import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .. import models
from ..core.config import settings

logger = logging.getLogger(__name__)


class VerifiedGapService:
    """Service for managing verified empty gaps."""

    @staticmethod
    def get_verified_gaps(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
    ) -> set[tuple[datetime, datetime]]:
        """Get verified empty gaps as a set of (gap_start, gap_end) tuples.

        Only returns gaps verified within GAP_REVERIFY_DAYS (gaps to skip).
        Gaps older than that are eligible for re-verification.
        """
        cutoff = datetime.now(UTC) - timedelta(days=settings.GAP_REVERIFY_DAYS)

        rows = (
            db.query(
                models.VerifiedEmptyGap.gap_start,
                models.VerifiedEmptyGap.gap_end,
            )
            .filter(
                models.VerifiedEmptyGap.exchange == exchange,
                models.VerifiedEmptyGap.instrument == instrument.upper(),
                models.VerifiedEmptyGap.timeframe == timeframe.upper(),
                models.VerifiedEmptyGap.last_verified_at >= cutoff,
            )
            .all()
        )

        return {(row.gap_start, row.gap_end) for row in rows}

    @staticmethod
    def mark_gap_verified(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        gap_start: datetime,
        gap_end: datetime,
        reason: str = "no_data",
    ) -> None:
        """Record a gap as verified empty, or update if it already exists."""
        existing = (
            db.query(models.VerifiedEmptyGap)
            .filter(
                models.VerifiedEmptyGap.exchange == exchange,
                models.VerifiedEmptyGap.instrument == instrument.upper(),
                models.VerifiedEmptyGap.timeframe == timeframe.upper(),
                models.VerifiedEmptyGap.gap_start == gap_start,
                models.VerifiedEmptyGap.gap_end == gap_end,
            )
            .first()
        )

        now = datetime.now(UTC)
        if existing:
            existing.attempt_count += 1
            existing.last_verified_at = now
            existing.reason = reason
        else:
            gap = models.VerifiedEmptyGap(
                exchange=exchange,
                instrument=instrument.upper(),
                timeframe=timeframe.upper(),
                gap_start=gap_start,
                gap_end=gap_end,
                gap_duration_seconds=(gap_end - gap_start).total_seconds(),
                attempt_count=1,
                first_verified_at=now,
                last_verified_at=now,
                reason=reason,
            )
            db.add(gap)

        db.commit()

    @staticmethod
    def remove_healed_gap(
        db: Session,
        exchange: str,
        instrument: str,
        timeframe: str,
        gap_start: datetime,
        gap_end: datetime,
    ) -> bool:
        """Remove a verified gap when data is successfully backfilled."""
        count = (
            db.query(models.VerifiedEmptyGap)
            .filter(
                models.VerifiedEmptyGap.exchange == exchange,
                models.VerifiedEmptyGap.instrument == instrument.upper(),
                models.VerifiedEmptyGap.timeframe == timeframe.upper(),
                models.VerifiedEmptyGap.gap_start == gap_start,
                models.VerifiedEmptyGap.gap_end == gap_end,
            )
            .delete()
        )
        if count:
            db.commit()
        return count > 0
