"""
Aircraft API Router

CRUD operations for tracked aircraft.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Aircraft, Flight, Position
from app.schemas import (
    AircraftCreate,
    AircraftResponse,
    AircraftUpdate,
    AircraftWithLatest,
    PositionResponse,
    FlightResponse,
)
from app.services.flightradar import fr24_client
from app.services.fa_scraper import fa_scraper
from app.services.home_assistant import ha_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])


@router.get("", response_model=list[AircraftWithLatest])
async def list_aircraft(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """List all tracked aircraft with their latest position and active flight."""
    query = select(Aircraft)
    if active_only:
        query = query.where(Aircraft.active == True)
    query = query.order_by(Aircraft.created_at.desc())

    result = await db.execute(query)
    aircraft_list = result.scalars().all()

    response = []
    for ac in aircraft_list:
        # Get latest position
        pos_result = await db.execute(
            select(Position)
            .where(Position.aircraft_id == ac.id)
            .order_by(Position.timestamp.desc())
            .limit(1)
        )
        latest_pos = pos_result.scalars().first()

        # Get active flight
        flight_result = await db.execute(
            select(Flight)
            .where(
                Flight.aircraft_id == ac.id,
                Flight.status.in_(["scheduled", "active"]),
            )
            .order_by(Flight.scheduled_departure.desc().nullslast())
            .limit(1)
        )
        active_flight = flight_result.scalars().first()

        ac_data = AircraftWithLatest.model_validate(ac)
        if latest_pos:
            ac_data.latest_position = PositionResponse.model_validate(latest_pos)
        if active_flight:
            ac_data.active_flight = FlightResponse.model_validate(active_flight)
        response.append(ac_data)

    return response


@router.post("", response_model=AircraftResponse, status_code=status.HTTP_201_CREATED)
async def add_aircraft(
    aircraft: AircraftCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a new aircraft to track.

    If ICAO24 hex is not provided, attempts to look it up via FlightRadar24.
    """
    # Check for duplicate tail number
    existing = await db.execute(
        select(Aircraft).where(Aircraft.tail_number == aircraft.tail_number.upper())
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Aircraft {aircraft.tail_number} is already being tracked",
        )

    # Try to enrich data from FR24 if we don't have ICAO24
    icao24 = aircraft.icao24_hex
    aircraft_type = aircraft.aircraft_type
    airline = aircraft.airline
    display_name = aircraft.display_name
    photo_url = None

    if not icao24 or not aircraft_type:
        try:
            fr24_data = await fr24_client.lookup_by_registration(aircraft.tail_number)
            if fr24_data:
                if not icao24 and fr24_data.get("icao24_hex"):
                    icao24 = fr24_data["icao24_hex"]
                if not aircraft_type and fr24_data.get("aircraft_type"):
                    aircraft_type = fr24_data["aircraft_type"]
                if not airline and fr24_data.get("airline"):
                    airline = fr24_data["airline"]
                if not display_name and fr24_data.get("flight_number"):
                    display_name = f"{airline or ''} {aircraft.tail_number}".strip()
                if fr24_data.get("photo_url"):
                    photo_url = fr24_data["photo_url"]
                logger.info(f"Enriched aircraft data from FR24: ICAO24={icao24}, type={aircraft_type}")
        except Exception as e:
            logger.warning(f"FR24 lookup failed for {aircraft.tail_number}: {e}")

    new_aircraft = Aircraft(
        tail_number=aircraft.tail_number.upper(),
        icao24_hex=icao24.lower() if icao24 else None,
        aircraft_type=aircraft_type,
        airline=airline,
        display_name=display_name or aircraft.tail_number.upper(),
        category=aircraft.category or "plane",
        photo_url=photo_url,
        active=True,
    )
    db.add(new_aircraft)
    await db.flush()
    await db.refresh(new_aircraft)

    logger.info(f"Added aircraft: {new_aircraft.tail_number} (ICAO24: {new_aircraft.icao24_hex})")
    return AircraftResponse.model_validate(new_aircraft)


@router.get("/{aircraft_id}", response_model=AircraftWithLatest)
async def get_aircraft(
    aircraft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get aircraft details with latest position and active flight."""
    result = await db.execute(
        select(Aircraft).where(Aircraft.id == aircraft_id)
    )
    aircraft = result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    # Get latest position
    pos_result = await db.execute(
        select(Position)
        .where(Position.aircraft_id == aircraft.id)
        .order_by(Position.timestamp.desc())
        .limit(1)
    )
    latest_pos = pos_result.scalars().first()

    # Get active flight
    flight_result = await db.execute(
        select(Flight)
        .where(
            Flight.aircraft_id == aircraft.id,
            Flight.status.in_(["scheduled", "active"]),
        )
        .order_by(Flight.scheduled_departure.desc().nullslast())
        .limit(1)
    )
    active_flight = flight_result.scalars().first()

    ac_data = AircraftWithLatest.model_validate(aircraft)
    if latest_pos:
        ac_data.latest_position = PositionResponse.model_validate(latest_pos)
    if active_flight:
        ac_data.active_flight = FlightResponse.model_validate(active_flight)

    return ac_data


@router.put("/{aircraft_id}", response_model=AircraftResponse)
async def update_aircraft(
    aircraft_id: uuid.UUID,
    update: AircraftUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update aircraft information."""
    result = await db.execute(
        select(Aircraft).where(Aircraft.id == aircraft_id)
    )
    aircraft = result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "tail_number" and value:
            value = value.upper()
        if field == "icao24_hex" and value:
            value = value.lower()
        setattr(aircraft, field, value)

    await db.flush()
    await db.refresh(aircraft)
    return AircraftResponse.model_validate(aircraft)


@router.delete("/{aircraft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aircraft(
    aircraft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Remove an aircraft from tracking (cascades to flights and positions)."""
    result = await db.execute(
        select(Aircraft).where(Aircraft.id == aircraft_id)
    )
    aircraft = result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    # Remove from HA
    await ha_service.remove_aircraft_sensor(aircraft.tail_number)

    tail = aircraft.tail_number
    await db.delete(aircraft)
    logger.info(f"Deleted aircraft: {tail}")


@router.post("/lookup", response_model=dict)
async def lookup_aircraft(
    tail_number: str = None,
    flight_number: str = None,
):
    """
    Look up aircraft/flight info from FlightRadar24 without adding it.
    Useful for getting ICAO24 hex, aircraft type, and current status.
    """
    if not tail_number and not flight_number:
        raise HTTPException(
            status_code=400,
            detail="Provide either tail_number or flight_number",
        )

    if tail_number:
        result = await fr24_client.lookup_by_registration(tail_number)
    else:
        result = await fr24_client.lookup_by_flight_number(flight_number)

    if not result:
        raise HTTPException(status_code=404, detail="Aircraft/flight not found on FlightRadar24")

    return result


@router.post("/{aircraft_id}/sync_fa", response_model=dict)
async def sync_aircraft_from_flightaware(
    aircraft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually sync upcoming flights from FlightAware web scraper.
    """
    result = await db.execute(select(Aircraft).where(Aircraft.id == aircraft_id))
    aircraft = result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    logger.info(f"Manual FlightAware sync triggered for {aircraft.tail_number}")
    
    try:
        flights_data = await fa_scraper.scrape_upcoming_flights(aircraft.tail_number)
        if not flights_data:
            return {"status": "success", "message": "No upcoming flights found", "count": 0}

        from app.routers.webhooks import normalize_airport_code

        added_count = 0
        updated_count = 0
        synced_flights = []
        for f_data in flights_data:
            f_num = f_data["flight_number"]
            orig_code = normalize_airport_code(f_data.get("origin_code"))
            dest_code = normalize_airport_code(f_data.get("destination_code"))

            # Build query to search for existing flight
            # Match either:
            # 1. Any scheduled or active flight with this flight number
            # 2. OR a landed flight that matches the departure time window (within 12 hours)
            dep_time = f_data.get("departure_time")
            query = select(Flight).where(
                Flight.aircraft_id == aircraft.id,
                Flight.flight_number == f_num
            )
            
            if isinstance(dep_time, datetime):
                # Ensure tz-aware
                if dep_time.tzinfo is None:
                    dep_time = dep_time.replace(tzinfo=timezone.utc)
                else:
                    dep_time = dep_time.astimezone(timezone.utc)
                
                query = query.where(
                    Flight.status.in_(["scheduled", "active"]) |
                    (
                        (Flight.status == "landed") &
                        (
                            (Flight.actual_departure.between(dep_time - timedelta(hours=12), dep_time + timedelta(hours=12))) |
                            (Flight.scheduled_departure.between(dep_time - timedelta(hours=12), dep_time + timedelta(hours=12)))
                        )
                    )
                )
            else:
                query = query.where(Flight.status.in_(["scheduled", "active"]))

            existing = await db.execute(query)
            flight = existing.scalars().first()

            if flight:
                # Update existing flight with more info
                logger.info(f"Updating existing flight {f_num} with new data from FA")
                if f_data.get("origin_name"): 
                    flight.departure_name = f_data["origin_name"]
                    logger.info(f"  - Setting departure_name: {f_data['origin_name']}")
                if f_data.get("destination_name"): 
                    flight.arrival_name = f_data["destination_name"]
                    logger.info(f"  - Setting arrival_name: {f_data['destination_name']}")
                if orig_code: flight.departure_iata = orig_code
                if dest_code: flight.arrival_iata = dest_code
                
                # Times
                if f_data.get("departure_time") and isinstance(f_data["departure_time"], datetime):
                    if f_data["status"] in ["active", "landed"]:
                        flight.actual_departure = f_data["departure_time"]
                        logger.info(f"  - Setting actual_departure: {f_data['departure_time']}")
                    else:
                        flight.scheduled_departure = f_data["departure_time"]
                        logger.info(f"  - Setting scheduled_departure: {f_data['departure_time']}")
                
                if f_data.get("arrival_time") and isinstance(f_data["arrival_time"], datetime):
                    if f_data["status"] == "landed":
                        flight.actual_arrival = f_data["arrival_time"]
                        logger.info(f"  - Setting actual_arrival: {f_data['arrival_time']}")
                    else:
                        # For active/scheduled flights, arrival is an estimate
                        flight.scheduled_arrival = f_data["arrival_time"]
                        logger.info(f"  - Setting scheduled_arrival: {f_data['arrival_time']}")
                
                flight.status = f_data["status"]
                updated_count += 1
                synced_flights.append(flight)
                continue

            # Add new flight
            new_f = Flight(
                aircraft_id=aircraft.id,
                flight_number=f_num,
                departure_iata=orig_code,
                departure_name=f_data.get("origin_name"),
                arrival_iata=dest_code,
                arrival_name=f_data.get("destination_name"),
                status=f_data["status"],
                scheduled_departure=f_data["departure_time"] if isinstance(f_data.get("departure_time"), datetime) and f_data["status"] != "active" else None,
                actual_departure=f_data["departure_time"] if isinstance(f_data.get("departure_time"), datetime) and f_data["status"] == "active" else None,
                scheduled_arrival=f_data["arrival_time"] if isinstance(f_data.get("arrival_time"), datetime) else None,
            )
            db.add(new_f)
            added_count += 1
            synced_flights.append(new_f)
            logger.info(f"Added flight {f_num} via manual FA sync")

        await db.commit()

        # Retroactively reconcile orphan positions for all added/updated flights
        from app.services.reconciliation import reconciliation_service
        for synced_flight in synced_flights:
            try:
                await db.refresh(synced_flight)
                await reconciliation_service.reconcile_orphan_positions(synced_flight, db)
            except Exception as re_err:
                logger.error(f"Failed to reconcile positions for flight {synced_flight.id} on FA sync: {re_err}")

        # Dynamic scheduler polling rate adjustment
        try:
            from app.services.tracker import tracker_service
            await tracker_service.update_tracker_polling_interval(db)
        except Exception as e:
            logger.error(f"Failed to update tracking interval on FA sync: {e}")

        return {
            "status": "success",
            "message": f"Synced {added_count} new flights",
            "count": added_count,
            "raw_count": len(flights_data)
        }
    except Exception as e:
        logger.error(f"FlightAware sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{aircraft_id}/poll", response_model=Optional[PositionResponse])
async def poll_aircraft_location(
    aircraft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger an immediate position poll for this aircraft.
    """
    from app.services.tracker import tracker_service
    pos = await tracker_service.poll_single_aircraft(aircraft_id)
    if not pos:
        return None
    return PositionResponse.model_validate(pos)
