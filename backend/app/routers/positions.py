"""
Positions API Router

Endpoints for querying position history.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Aircraft, Position, Flight
from app.schemas import PositionResponse, PositionUpdate

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


@router.put("/{position_id}", response_model=PositionResponse)
async def update_position(
    position_id: int,
    update_data: PositionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a specific position record, recalculating flight stats."""
    # Find the position
    pos_result = await db.execute(
        select(Position).where(Position.id == position_id)
    )
    pos = pos_result.scalars().first()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    old_flight_id = pos.flight_id
    new_flight_id = update_data.flight_id

    # Update position fields
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(pos, field, val)

    await db.commit()
    await db.refresh(pos)

    # Recalculate stats for affected flights
    flight_ids_to_recalc = set()
    if old_flight_id:
        flight_ids_to_recalc.add(old_flight_id)
    if new_flight_id is not None:
        flight_ids_to_recalc.add(new_flight_id)

    from app.services.stats_calculator import calculate_flight_stats
    for fid in flight_ids_to_recalc:
        flight_result = await db.execute(
            select(Flight).where(Flight.id == fid)
        )
        flight = flight_result.scalars().first()
        if flight:
            flight.summary_stats = await calculate_flight_stats(flight, db)
            db.add(flight)

    await db.commit()
    await db.refresh(pos)
    return pos


@router.delete("/{position_id}")
async def delete_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific position record, recalculating flight stats."""
    # Find the position
    pos_result = await db.execute(
        select(Position).where(Position.id == position_id)
    )
    pos = pos_result.scalars().first()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    flight_id = pos.flight_id

    # Delete the position
    await db.delete(pos)
    await db.commit()

    # Recalculate stats for the flight
    if flight_id:
        from app.services.stats_calculator import calculate_flight_stats
        flight_result = await db.execute(
            select(Flight).where(Flight.id == flight_id)
        )
        flight = flight_result.scalars().first()
        if flight:
            flight.summary_stats = await calculate_flight_stats(flight, db)
            db.add(flight)
            await db.commit()

    return {"status": "success", "message": "Position deleted successfully"}

