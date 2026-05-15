"""
Webhooks API Router

Endpoints for external automations (like N8N) to push flight events.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Aircraft, Flight
from app.schemas import WebhookFlightFiled, WebhookFlightDeparted, FlightResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

def verify_token(x_webhook_token: Optional[str] = Header(None)):
    """Optional simple token authentication."""
    if settings.webhook_token:
        if not x_webhook_token or x_webhook_token != settings.webhook_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")
    return True

@router.post("/flight-filed", response_model=FlightResponse, status_code=status.HTTP_201_CREATED)
async def webhook_flight_filed(
    payload: WebhookFlightFiled,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when a flight plan is filed.
    Dedupes based on same aircraft + scheduled departure + departure airport.
    """
    # 1. Look up aircraft
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    # 2. Deduplication check
    # Check if we already have a scheduled flight for this aircraft with the same route/day
    if payload.scheduled_departure and payload.departure_iata:
        existing_res = await db.execute(
            select(Flight)
            .where(
                Flight.aircraft_id == aircraft.id,
                Flight.status == "scheduled",
                Flight.departure_iata == payload.departure_iata
            )
        )
        for existing in existing_res.scalars().all():
            if existing.scheduled_departure:
                # If they are on the same day, consider it a duplicate
                if existing.scheduled_departure.date() == payload.scheduled_departure.date():
                    logger.info(f"Deduplicated flight plan for {payload.tail_number}. Flight already exists.")
                    return existing

    # 3. Geocode the airports for map plotting
    from app.services.geocoder import geocoder
    dep_coords = await geocoder.get_airport_coordinates(payload.departure_iata)
    arr_coords = await geocoder.get_airport_coordinates(payload.arrival_iata)

    # 4. Create Flight
    new_flight = Flight(
        aircraft_id=aircraft.id,
        flight_number=payload.flight_number,
        callsign=payload.callsign,
        departure_iata=payload.departure_iata,
        arrival_iata=payload.arrival_iata,
        departure_lat=dep_coords[0] if dep_coords else None,
        departure_lon=dep_coords[1] if dep_coords else None,
        arrival_lat=arr_coords[0] if arr_coords else None,
        arrival_lon=arr_coords[1] if arr_coords else None,
        scheduled_departure=payload.scheduled_departure,
        scheduled_arrival=payload.scheduled_arrival,
        expected_route=payload.expected_route,
        status="scheduled"
    )
    db.add(new_flight)
    await db.commit()
    await db.refresh(new_flight)
    
    logger.info(f"Created new scheduled flight for {payload.tail_number} from webhook.")
    return new_flight


@router.post("/flight-departed", response_model=FlightResponse, status_code=status.HTTP_200_OK)
async def webhook_flight_departed(
    payload: WebhookFlightDeparted,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when an aircraft departs.
    Updates the flight to active and alerts Home Assistant.
    """
    # 1. Look up aircraft
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    # 2. Find the scheduled flight
    # First, try to find an active flight already
    flight_res = await db.execute(
        select(Flight)
        .where(Flight.aircraft_id == aircraft.id, Flight.status.in_(["scheduled", "active"]))
        .order_by(Flight.scheduled_departure.desc().nullslast())
        .limit(1)
    )
    flight = flight_res.scalars().first()
    
    # If no scheduled flight exists, dynamically create one
    if not flight:
        logger.info(f"No scheduled flight found for {payload.tail_number}. Creating one dynamically from departed webhook.")
        flight = Flight(
            aircraft_id=aircraft.id,
            flight_number=payload.flight_number,
            departure_iata=payload.departure_iata,
            arrival_iata=payload.arrival_iata,
            status="active"
        )
        db.add(flight)
    else:
        flight.status = "active"

    # Set departure time
    if payload.actual_departure:
        flight.actual_departure = payload.actual_departure
    elif not flight.actual_departure:
        flight.actual_departure = datetime.now(timezone.utc)
    
    await db.commit()
    await db.refresh(flight)

    logger.info(f"Flight {flight.id} for {payload.tail_number} marked as ACTIVE from webhook.")

    # 3. Trigger Home Assistant alert so tracking begins in the smart home
    from app.services.home_assistant import ha_service
    status_str = ha_service.build_status_string(
        on_ground=False,
        departure_iata=flight.departure_iata,
        arrival_iata=flight.arrival_iata,
        scheduled_arrival=flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
        scheduled_departure=flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
        flight_status=flight.status,
    )
    
    flight_data = {
        "flight_number": flight.flight_number,
        "callsign": flight.callsign,
        "departure_iata": flight.departure_iata,
        "arrival_iata": flight.arrival_iata,
        "scheduled_departure": flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
        "scheduled_arrival": flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
        "actual_departure": flight.actual_departure.isoformat() if flight.actual_departure else None,
        "status": flight.status,
    }
    
    await ha_service.update_aircraft_sensor(
        tail_number=aircraft.tail_number,
        status=status_str,
        flight_data=flight_data,
        position_data={"on_ground": False}
    )

    return flight
