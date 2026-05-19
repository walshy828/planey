"""
Data Cleanup Service

Periodically purges old position data and flight change history to manage database size.
Retention periods are configurable via the Settings UI (stored in DB).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text

from app.config import settings
from app.database import async_session
from app.models import Position, FlightChangeHistory, Setting

logger = logging.getLogger(__name__)


class CleanupService:
    """Handles periodic data cleanup tasks."""

    async def _get_retention_days(self, key: str, default: int) -> int:
        """Fetch a retention setting from the DB, falling back to a default."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Setting).where(Setting.key == key)
                )
                setting = result.scalars().first()
                if setting and setting.value.isdigit():
                    return int(setting.value)
        except Exception as e:
            logger.warning(f"Failed to read setting '{key}': {e}")
        return default

    async def purge_old_positions(self):
        """Delete position records older than the configured retention period."""
        retention_days = await self._get_retention_days(
            "position_retention_days", settings.position_retention_days
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        try:
            async with async_session() as session:
                result = await session.execute(
                    delete(Position).where(Position.timestamp < cutoff)
                )
                deleted = result.rowcount
                await session.commit()

                if deleted > 0:
                    logger.info(f"Purged {deleted} position records older than {cutoff.date()} ({retention_days} day retention)")
                else:
                    logger.debug("No old positions to purge")

        except Exception as e:
            logger.error(f"Position cleanup failed: {e}", exc_info=True)

    async def purge_old_flight_history(self):
        """Delete flight change history records older than the configured retention period."""
        retention_days = await self._get_retention_days(
            "flight_history_retention_days", 90
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        try:
            async with async_session() as session:
                result = await session.execute(
                    delete(FlightChangeHistory).where(FlightChangeHistory.changed_at < cutoff)
                )
                deleted = result.rowcount
                await session.commit()

                if deleted > 0:
                    logger.info(f"Purged {deleted} flight history records older than {cutoff.date()} ({retention_days} day retention)")
                else:
                    logger.debug("No old flight history to purge")

        except Exception as e:
            logger.error(f"Flight history cleanup failed: {e}", exc_info=True)

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
                result = await session.execute(text(sql), {"cutoff": cutoff})
                deleted = result.rowcount
                await session.commit()

                if deleted > 0:
                    logger.info(f"Downsampled {deleted} positions older than {days_threshold} days")

        except Exception as e:
            logger.error(f"Position downsampling failed: {e}", exc_info=True)


# Singleton instance
cleanup_service = CleanupService()
