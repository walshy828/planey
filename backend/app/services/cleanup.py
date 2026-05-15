"""
Data Cleanup Service

Periodically purges old position data to manage database size.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.config import settings
from app.database import async_session
from app.models import Position

logger = logging.getLogger(__name__)


class CleanupService:
    """Handles periodic data cleanup tasks."""

    async def purge_old_positions(self):
        """Delete position records older than POSITION_RETENTION_DAYS."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.position_retention_days)

        try:
            async with async_session() as session:
                result = await session.execute(
                    delete(Position).where(Position.timestamp < cutoff)
                )
                deleted = result.rowcount
                await session.commit()

                if deleted > 0:
                    logger.info(f"Purged {deleted} position records older than {cutoff.date()}")
                else:
                    logger.debug("No old positions to purge")

        except Exception as e:
            logger.error(f"Position cleanup failed: {e}", exc_info=True)

    async def downsample_old_positions(self, days_threshold: int = 7, target_interval_minutes: int = 5):
        """
        Downsample positions older than days_threshold to one per target_interval_minutes.
        This reduces storage for old flight history while preserving general route shape.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)

        try:
            async with async_session() as session:
                # Use a CTE to find positions to delete (keep first per interval)
                # This is done via raw SQL for efficiency
                sql = f"""
                DELETE FROM positions
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY aircraft_id,
                                   date_trunc('hour', timestamp) +
                                   (EXTRACT(minute FROM timestamp)::int / {target_interval_minutes})
                                   * interval '{target_interval_minutes} minutes'
                                   ORDER BY timestamp
                               ) as rn
                        FROM positions
                        WHERE timestamp < :cutoff
                    ) sub
                    WHERE rn > 1
                )
                """
                from sqlalchemy import text
                result = await session.execute(text(sql), {"cutoff": cutoff})
                deleted = result.rowcount
                await session.commit()

                if deleted > 0:
                    logger.info(f"Downsampled {deleted} positions older than {days_threshold} days")

        except Exception as e:
            logger.error(f"Position downsampling failed: {e}", exc_info=True)


# Singleton instance
cleanup_service = CleanupService()
