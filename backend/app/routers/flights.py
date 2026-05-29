"""
Flights API Router

Manage tracked flights with schedule data and position history.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Aircraft, Flight, Position, FlightChangeHistory
from app.schemas import (
    FlightCreate,
    FlightResponse,
    FlightUpdate,
    FlightWithPositions,
    FlightChangeHistoryResponse,
    PositionResponse,
)
from app.services.flightradar import fr24_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flights", tags=["flights"])


@router.get("", response_model=list[FlightResponse])
async def list_flights(
    aircraft_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    hours: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List flights with optional filtering."""
    from app.services.stats_calculator import update_flight_stats_if_needed

    # Subquery: position count per flight
    pos_subq = (
        select(Position.flight_id, func.count(Position.id).label("cnt"))
        .group_by(Position.flight_id)
        .subquery()
    )

    stmt = (
        select(Flight, Aircraft.tail_number, pos_subq.c.cnt)
        .outerjoin(Aircraft, Flight.aircraft_id == Aircraft.id)
        .outerjoin(pos_subq, Flight.id == pos_subq.c.flight_id)
    )

    if aircraft_id:
        stmt = stmt.where(Flight.aircraft_id == aircraft_id)
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        stmt = stmt.where(Flight.status.in_(statuses))
    if hours:
        from sqlalchemy import or_
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_naive = cutoff.replace(tzinfo=None)
        stmt = stmt.where(
            or_(
                Flight.actual_departure >= cutoff_naive,
                Flight.scheduled_departure >= cutoff_naive
            )
        )

    stmt = stmt.order_by(Flight.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.all()

    # Retroactively compute statistics for landed flights that lack them
    updated = False
    for row in rows:
        if await update_flight_stats_if_needed(row[0], db):
            updated = True
    if updated:
        await db.commit()

    return [
        FlightResponse.model_validate(flight).model_copy(update={
            "tail_number": tail_number,
            "position_count": cnt or 0,
        })
        for flight, tail_number, cnt in rows
    ]


@router.get("/active", response_model=list[FlightWithPositions])
async def get_active_flights(db: AsyncSession = Depends(get_db)):
    """Get all currently active flights with their recent positions."""
    result = await db.execute(
        select(Flight).where(Flight.status.in_(["scheduled", "active"]))
    )
    flights = result.scalars().all()

    response = []
    for flight in flights:
        # Get aircraft info
        ac_result = await db.execute(
            select(Aircraft).where(Aircraft.id == flight.aircraft_id)
        )
        aircraft = ac_result.scalars().first()

        # Get recent positions (last 2 hours)
        since = datetime.now(timezone.utc) - timedelta(hours=2)
        pos_result = await db.execute(
            select(Position)
            .where(
                Position.flight_id == flight.id,
                Position.timestamp >= since,
            )
            .order_by(Position.timestamp.asc())
        )
        positions = pos_result.scalars().all()

        from app.schemas import AircraftResponse
        flight_resp = FlightResponse.model_validate(flight)
        flight_data = FlightWithPositions(
            **flight_resp.model_dump(),
            positions=[PositionResponse.model_validate(p) for p in positions],
            aircraft=AircraftResponse.model_validate(aircraft) if aircraft else None,
            change_history=[]
        )
        response.append(flight_data)

    return response


@router.post("", response_model=FlightResponse, status_code=status.HTTP_201_CREATED)
async def add_flight(
    flight: FlightCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a flight to track.

    If flight_number is provided, attempts to look up route/schedule from FR24.
    """
    # Verify aircraft exists
    ac_result = await db.execute(
        select(Aircraft).where(Aircraft.id == flight.aircraft_id)
    )
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    # Deduplication check
    if flight.flight_number:
        # Check if there is an active/scheduled flight with the same flight number
        existing_res = await db.execute(
            select(Flight).where(
                Flight.aircraft_id == flight.aircraft_id,
                Flight.flight_number == flight.flight_number,
                Flight.status.in_(["scheduled", "active"])
            )
        )
        existing_flight = existing_res.scalars().first()
        if existing_flight:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A scheduled or active flight with flight number '{flight.flight_number}' already exists for this aircraft."
            )

    # Try to enrich from FR24
    departure_iata = flight.departure_iata
    departure_name = flight.departure_name
    arrival_iata = flight.arrival_iata
    arrival_name = flight.arrival_name
    callsign = flight.callsign

    if flight.flight_number:
        try:
            fr24_data = fr24_client.lookup_by_flight_number(flight.flight_number)
            if fr24_data:
                if not departure_iata and fr24_data.get("departure_iata"):
                    departure_iata = fr24_data["departure_iata"]
                if not departure_name and fr24_data.get("departure_name"):
                    departure_name = fr24_data["departure_name"]
                if not arrival_iata and fr24_data.get("arrival_iata"):
                    arrival_iata = fr24_data["arrival_iata"]
                if not arrival_name and fr24_data.get("arrival_name"):
                    arrival_name = fr24_data["arrival_name"]
                if not callsign and fr24_data.get("callsign"):
                    callsign = fr24_data["callsign"]

                # Update aircraft ICAO24 if we didn't have it
                if not aircraft.icao24_hex and fr24_data.get("icao24_hex"):
                    aircraft.icao24_hex = fr24_data["icao24_hex"]
                    logger.info(f"Updated ICAO24 for {aircraft.tail_number}: {aircraft.icao24_hex}")

                # Update aircraft type if we didn't have it
                if not aircraft.aircraft_type and fr24_data.get("aircraft_type"):
                    aircraft.aircraft_type = fr24_data["aircraft_type"]

                logger.info(f"Enriched flight data from FR24: {departure_iata}→{arrival_iata}")
        except Exception as e:
            logger.warning(f"FR24 flight lookup failed: {e}")

    new_flight = Flight(
        aircraft_id=flight.aircraft_id,
        flight_number=flight.flight_number,
        callsign=callsign,
        departure_iata=departure_iata,
        departure_icao=flight.departure_icao,
        departure_name=departure_name,
        arrival_iata=arrival_iata,
        arrival_icao=flight.arrival_icao,
        arrival_name=arrival_name,
        scheduled_departure=flight.scheduled_departure,
        scheduled_arrival=flight.scheduled_arrival,
        status=flight.status or "scheduled",
    )
    db.add(new_flight)
    await db.flush()
    await db.refresh(new_flight)

    logger.info(f"Added flight: {new_flight.flight_number} ({departure_iata}→{arrival_iata})")
    return FlightResponse.model_validate(new_flight)


@router.get("/{flight_id}", response_model=FlightWithPositions)
async def get_flight(
    flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get flight details with full position trail."""
    result = await db.execute(
        select(Flight).where(Flight.id == flight_id)
    )
    flight = result.scalars().first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    # Retroactively compute statistics if needed
    from app.services.stats_calculator import update_flight_stats_if_needed
    if await update_flight_stats_if_needed(flight, db):
        await db.commit()
        await db.refresh(flight)

    # Get aircraft
    ac_result = await db.execute(
        select(Aircraft).where(Aircraft.id == flight.aircraft_id)
    )
    aircraft = ac_result.scalars().first()

    # Get all positions for this flight
    pos_result = await db.execute(
        select(Position)
        .where(Position.flight_id == flight.id)
        .order_by(Position.timestamp.asc())
    )
    positions = pos_result.scalars().all()

    # Load change history
    history_result = await db.execute(
        select(FlightChangeHistory)
        .where(FlightChangeHistory.flight_id == flight.id)
        .order_by(FlightChangeHistory.changed_at.desc())
    )
    history = history_result.scalars().all()

    from app.schemas import AircraftResponse
    flight_resp = FlightResponse.model_validate(flight)
    flight_data = FlightWithPositions(
        **flight_resp.model_dump(),
        positions=[PositionResponse.model_validate(p) for p in positions],
        aircraft=AircraftResponse.model_validate(aircraft) if aircraft else None,
        change_history=[FlightChangeHistoryResponse.model_validate(h) for h in history]
    )

    return flight_data


@router.get("/{flight_id}/positions", response_model=list[PositionResponse])
async def get_flight_positions(
    flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get position trail for a specific flight."""
    # Verify flight exists
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Flight not found")

    pos_result = await db.execute(
        select(Position)
        .where(Position.flight_id == flight_id)
        .order_by(Position.timestamp.asc())
    )
    positions = pos_result.scalars().all()
    return [PositionResponse.model_validate(p) for p in positions]


@router.put("/{flight_id}", response_model=FlightResponse)
async def update_flight(
    flight_id: uuid.UUID,
    update: FlightUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update flight information."""
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalars().first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    update_data = update.model_dump(exclude_unset=True)
    
    # Record manual_admin changes in FlightChangeHistory
    from app.models import record_flight_changes
    await record_flight_changes(flight, update_data, "manual_admin", db)

    for field, value in update_data.items():
        setattr(flight, field, value)

    await db.commit()
    await db.refresh(flight)
    return FlightResponse.model_validate(flight)


@router.delete("/{flight_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flight(
    flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a flight and its position history."""
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalars().first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    await db.delete(flight)
    logger.info(f"Deleted flight: {flight.flight_number}")

@router.post("/{flight_id}/reconcile")
async def reconcile_flight_endpoint(
    flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manually reconcile a stuck flight using external APIs."""
    from app.services.reconciliation import reconciliation_service
    try:
        result = await reconciliation_service.reconcile_flight(str(flight_id), db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconciliation failed: {e}")


@router.post("/{flight_id}/merge/{source_flight_id}")
async def merge_flights_endpoint(
    flight_id: uuid.UUID,
    source_flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Merge source_flight_id positions into flight_id and delete the source.

    Useful for cleaning up duplicate flights created when a restart interrupted
    an active leg: merge the stub auto-created after restart back into the
    original flight.
    """
    from app.services.reconciliation import reconciliation_service
    try:
        result = await reconciliation_service.merge_flights(str(flight_id), str(source_flight_id), db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merge failed: {e}")


@router.get("/{flight_id}/history", response_model=list[FlightChangeHistoryResponse])
async def get_flight_history(
    flight_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get audit trail of changes for a specific flight."""
    # Verify flight exists
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Flight not found")

    history_result = await db.execute(
        select(FlightChangeHistory)
        .where(FlightChangeHistory.flight_id == flight_id)
        .order_by(FlightChangeHistory.changed_at.desc())
    )
    history = history_result.scalars().all()
    return [FlightChangeHistoryResponse.model_validate(h) for h in history]
