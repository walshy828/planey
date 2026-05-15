"""
Positions API Router

Endpoints for querying position history.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Aircraft, Position
from app.schemas import PositionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("/latest", response_model=list[dict])
async def get_latest_positions(db: AsyncSession = Depends(get_db)):
    """Get the latest position for all active tracked aircraft."""
    # Get all active aircraft
    ac_result = await db.execute(
        select(Aircraft).where(Aircraft.active == True)
    )
    aircraft_list = ac_result.scalars().all()

    result = []
    for ac in aircraft_list:
        pos_result = await db.execute(
            select(Position)
            .where(Position.aircraft_id == ac.id)
            .order_by(Position.timestamp.desc())
            .limit(1)
        )
        pos = pos_result.scalars().first()
        if pos:
            result.append({
                "aircraft_id": str(ac.id),
                "tail_number": ac.tail_number,
                "display_name": ac.display_name,
                "aircraft_type": ac.aircraft_type,
                "position": PositionResponse.model_validate(pos).model_dump(),
            })

    return result


@router.get("/{aircraft_id}/history", response_model=list[PositionResponse])
async def get_position_history(
    aircraft_id: uuid.UUID,
    hours: int = Query(24, le=720, description="Number of hours of history"),
    db: AsyncSession = Depends(get_db),
):
    """Get position history for an aircraft within a time range."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(Position)
        .where(
            Position.aircraft_id == aircraft_id,
            Position.timestamp >= since,
        )
        .order_by(Position.timestamp.asc())
    )
    positions = result.scalars().all()
    return [PositionResponse.model_validate(p) for p in positions]
