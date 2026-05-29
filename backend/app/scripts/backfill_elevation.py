"""
Backfill ground_elevation_ft for historical positions.

Processes all positions that have a lat/lon but no ground_elevation_ft yet,
using the Open-Meteo Elevation API in batches of 100.  Commits every 500
positions so progress is preserved if the script is interrupted.

Run from the repo root with the virtualenv active:
    cd backend && python app/scripts/backfill_elevation.py

Optional flags:
    --dry-run   Show counts without writing to the database.
    --batch N   Override the write-commit batch size (default 500).
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import select, func, update

from app.database import async_session
from app.models import Position
from app.services.elevation import get_elevations_ft

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backfill_elevation")

API_BATCH = 100   # Open-Meteo max per request
DEFAULT_COMMIT_BATCH = 500


async def backfill(dry_run: bool = False, commit_batch: int = DEFAULT_COMMIT_BATCH):
    async with async_session() as session:
        # Count total work upfront
        count_q = select(func.count()).select_from(Position).where(
            Position.ground_elevation_ft.is_(None),
            Position.latitude.isnot(None),
            Position.longitude.isnot(None),
        )
        total = (await session.execute(count_q)).scalar_one()
        logger.info("Positions needing elevation backfill: %d", total)

        if total == 0:
            logger.info("Nothing to do.")
            return

        if dry_run:
            logger.info("Dry run — no writes performed.")
            return

        processed = 0
        pending_updates: list[tuple[int, float]] = []  # (id, elevation_ft)

        # Stream in API-sized chunks to avoid loading millions of rows at once
        offset = 0
        while True:
            rows_q = (
                select(Position.id, Position.latitude, Position.longitude)
                .where(
                    Position.ground_elevation_ft.is_(None),
                    Position.latitude.isnot(None),
                    Position.longitude.isnot(None),
                )
                .order_by(Position.id)
                .limit(API_BATCH)
                .offset(offset)
            )
            rows = (await session.execute(rows_q)).all()
            if not rows:
                break

            ids = [r.id for r in rows]
            coords = [(r.latitude, r.longitude) for r in rows]

            elevations = await get_elevations_ft(coords)

            for pos_id, elev in zip(ids, elevations):
                if elev is not None:
                    pending_updates.append((pos_id, elev))

            processed += len(rows)
            logger.info("Fetched elevations for %d / %d positions", processed, total)

            # Bulk-update and commit when we hit the commit batch size
            if len(pending_updates) >= commit_batch:
                await _flush(session, pending_updates)
                pending_updates = []

            # If we got fewer rows than requested, we've reached the end
            if len(rows) < API_BATCH:
                break

            offset += API_BATCH

        # Flush any remainder
        if pending_updates:
            await _flush(session, pending_updates)

        logger.info("Backfill complete. %d positions updated.", processed)


async def _flush(session, updates: list[tuple[int, float]]):
    """Bulk-update a batch of positions and commit."""
    if not updates:
        return
    # SQLAlchemy bulk update via mappings
    await session.execute(
        update(Position),
        [{"id": pos_id, "ground_elevation_ft": elev} for pos_id, elev in updates],
    )
    await session.commit()
    logger.info("Committed %d elevation updates.", len(updates))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill ground_elevation_ft for historical positions.")
    parser.add_argument("--dry-run", action="store_true", help="Count positions without writing")
    parser.add_argument("--batch", type=int, default=DEFAULT_COMMIT_BATCH, metavar="N",
                        help=f"Commit every N positions (default {DEFAULT_COMMIT_BATCH})")
    args = parser.parse_args()

    asyncio.run(backfill(dry_run=args.dry_run, commit_batch=args.batch))
