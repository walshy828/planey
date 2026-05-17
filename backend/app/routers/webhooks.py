"""
Webhooks API Router

Endpoints for external automations (like N8N) to push flight events.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Aircraft, Flight
from app.schemas import WebhookFlightFiled, WebhookFlightDeparted, WebhookFlightArrived, FlightResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

def verify_token(x_webhook_token: Optional[str] = Header(None)):
    """Optional simple token authentication."""
    if settings.webhook_token:
        if not x_webhook_token or x_webhook_token != settings.webhook_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")
    return True

def normalize_airport_code(code: Optional[str]) -> Optional[str]:
    """Normalize airport codes by removing K/C prefixes from 4-letter North American codes."""
    if not code:
        return None
    code = code.strip().upper()
    if len(code) == 4 and code[0] in ('K', 'C'):
        return code[1:]
    return code

@router.post("/flight-filed", response_model=FlightResponse, status_code=status.HTTP_201_CREATED)
async def webhook_flight_filed(
    payload: WebhookFlightFiled,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when a flight plan is filed.
    Dedupes based on same aircraft + scheduled departure (within 18h) + departure airport.
    """
    # 1. Look up aircraft
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    # Normalize airport codes
    payload.departure_iata = normalize_airport_code(payload.departure_iata)
    payload.arrival_iata = normalize_airport_code(payload.arrival_iata)

    # 2. Deduplication / Update Check
    # Check if we already have a scheduled flight for this aircraft with the same route/day (within 18 hours)
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
                existing_dep = existing.scheduled_departure.replace(tzinfo=None)
                payload_dep = payload.scheduled_departure.replace(tzinfo=None)
                diff = abs((existing_dep - payload_dep).total_seconds())
                if diff <= 18 * 3600:
                    logger.info(f"Deduplicated and updating flight plan for {payload.tail_number}. Flight already exists.")
                    # Update flight plan details in-place
                    existing.flight_number = payload.flight_number or existing.flight_number
                    existing.callsign = payload.callsign or existing.callsign
                    existing.arrival_iata = payload.arrival_iata or existing.arrival_iata
                    existing.scheduled_departure = payload.scheduled_departure or existing.scheduled_departure
                    existing.scheduled_arrival = payload.scheduled_arrival or existing.scheduled_arrival
                    existing.expected_route = payload.expected_route or existing.expected_route
                    
                    # Re-geocode the airports if details changed
                    from app.services.geocoder import geocoder
                    dep_coords = await geocoder.get_airport_coordinates(payload.departure_iata)
                    arr_coords = await geocoder.get_airport_coordinates(payload.arrival_iata)
                    if dep_coords:
                        existing.departure_lat, existing.departure_lon = dep_coords
                    if arr_coords:
                        existing.arrival_lat, existing.arrival_lon = arr_coords
                        
                    await db.commit()
                    await db.refresh(existing)
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

    # Normalize airport codes
    payload.departure_iata = normalize_airport_code(payload.departure_iata)
    payload.arrival_iata = normalize_airport_code(payload.arrival_iata)

    # Self-Healing: Check for any existing active flights that are now stale
    active_res = await db.execute(
        select(Flight)
        .where(Flight.aircraft_id == aircraft.id, Flight.status == "active")
    )
    active_flights = active_res.scalars().all()
    dep_time = payload.actual_departure or datetime.now(timezone.utc)
    for old_flight in active_flights:
        # Check if it's a duplicate webhook (same departure, within 4 hours)
        is_duplicate = False
        if old_flight.departure_iata == payload.departure_iata:
            if old_flight.actual_departure:
                old_dep = old_flight.actual_departure.replace(tzinfo=None)
                dep_time_naive = dep_time.replace(tzinfo=None)
                if abs((old_dep - dep_time_naive).total_seconds()) < 4 * 3600:
                    is_duplicate = True
        
        if not is_duplicate:
            logger.info(f"Self-healing: Reconciling and closing previous active flight {old_flight.id} for {aircraft.tail_number}")
            from app.services.reconciliation import reconciliation_service
            try:
                # Force reconciliation via FR24/FA to gracefully close out the old flight
                await reconciliation_service.reconcile_flight(old_flight.id, db)
            except Exception as e:
                logger.error(f"Failed to automatically reconcile old flight {old_flight.id} on departure: {e}")
                # Fallback: force land it manually
                old_flight.status = "landed"
                old_flight.actual_arrival = dep_time - timedelta(minutes=30)
                await db.commit()

    # 2. Find the scheduled flight
    sched_res = await db.execute(
        select(Flight)
        .where(Flight.aircraft_id == aircraft.id, Flight.status == "scheduled")
    )
    scheduled_flights = sched_res.scalars().all()

    flight = None
    matching_scheduled = []
    for s_flight in scheduled_flights:
        if s_flight.scheduled_departure:
            s_dep = s_flight.scheduled_departure.replace(tzinfo=None)
            dep_time_naive = dep_time.replace(tzinfo=None)
            diff = abs((s_dep - dep_time_naive).total_seconds())
            if diff <= 18 * 3600:
                matching_scheduled.append((diff, s_flight))

    # Sort matching scheduled flights by relevance:
    # 1. Matches departure AND arrival (best)
    # 2. Matches departure
    # 3. Matches arrival
    # 4. Closest in time
    if matching_scheduled:
        def get_match_priority(item):
            diff_val, f_obj = item
            matches_dep = (payload.departure_iata and f_obj.departure_iata == payload.departure_iata)
            matches_arr = (payload.arrival_iata and f_obj.arrival_iata == payload.arrival_iata)
            if matches_dep and matches_arr:
                return (0, diff_val)
            elif matches_dep:
                return (1, diff_val)
            elif matches_arr:
                return (2, diff_val)
            else:
                return (3, diff_val)

        matching_scheduled.sort(key=get_match_priority)
        flight = matching_scheduled[0][1]
        logger.info(f"Matched scheduled flight {flight.id} for departure of {payload.tail_number}")
        flight.status = "active"
        if payload.arrival_iata and not flight.arrival_iata:
            flight.arrival_iata = payload.arrival_iata
    else:
        # If no scheduled flight exists, dynamically create one
        logger.info(f"No scheduled flight found for {payload.tail_number}. Creating one dynamically from departed webhook.")
        flight = Flight(
            aircraft_id=aircraft.id,
            flight_number=payload.flight_number,
            departure_iata=payload.departure_iata,
            arrival_iata=payload.arrival_iata,
            status="active"
        )
        db.add(flight)

    # Set times and coordinate lookups
    flight.actual_departure = dep_time
    if payload.scheduled_arrival:
        flight.scheduled_arrival = payload.scheduled_arrival

    from app.services.geocoder import geocoder
    if flight.departure_iata and (not flight.departure_lat or not flight.departure_lon):
        dep_coords = await geocoder.get_airport_coordinates(flight.departure_iata)
        if dep_coords:
            flight.departure_lat, flight.departure_lon = dep_coords
    if flight.arrival_iata and (not flight.arrival_lat or not flight.arrival_lon):
        arr_coords = await geocoder.get_airport_coordinates(flight.arrival_iata)
        if arr_coords:
            flight.arrival_lat, flight.arrival_lon = arr_coords

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


@router.post("/flight-arrived", response_model=FlightResponse, status_code=status.HTTP_200_OK)
async def webhook_flight_arrived(
    payload: WebhookFlightArrived,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when an aircraft arrives.
    Updates the flight status to landed, geocodes coordinates,
    inserts a grounded position update, and alerts Home Assistant.
    """
    # 1. Look up aircraft
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    # Normalize airport codes
    payload.arrival_iata = normalize_airport_code(payload.arrival_iata)

    # 2. Find the active flight
    flight_res = await db.execute(
        select(Flight)
        .where(Flight.aircraft_id == aircraft.id, Flight.status == "active")
        .order_by(Flight.actual_departure.desc().nullslast())
        .limit(1)
    )
    flight = flight_res.scalars().first()

    # 3. If no active flight exists, promote a matching scheduled flight (missed departure webhook)
    arr_time = payload.actual_arrival or datetime.now(timezone.utc)
    if not flight:
        sched_res = await db.execute(
            select(Flight)
            .where(Flight.aircraft_id == aircraft.id, Flight.status == "scheduled")
        )
        scheduled_flights = sched_res.scalars().all()
        
        matching_scheduled = []
        for s_flight in scheduled_flights:
            if s_flight.scheduled_departure:
                s_dep = s_flight.scheduled_departure.replace(tzinfo=None)
                arr_time_naive = arr_time.replace(tzinfo=None)
                diff = abs((s_dep - arr_time_naive).total_seconds())
                if diff <= 18 * 3600:
                    matching_scheduled.append((diff, s_flight))
                    
        if matching_scheduled:
            def get_arr_priority(item):
                diff_val, f_obj = item
                matches_arr = (payload.arrival_iata and f_obj.arrival_iata == payload.arrival_iata)
                return (0 if matches_arr else 1, diff_val)
                
            matching_scheduled.sort(key=get_arr_priority)
            flight = matching_scheduled[0][1]
            logger.info(f"Missed departure webhook: Promoting scheduled flight {flight.id} directly to landed.")
            
            if not flight.actual_departure:
                flight.actual_departure = flight.scheduled_departure or (arr_time - timedelta(hours=1))
            flight.status = "landed"

    # 4. If still no flight found, dynamically create a landed flight record
    if not flight:
        logger.info(f"No active or scheduled flight found for {payload.tail_number}. Creating landed flight dynamically.")
        flight = Flight(
            aircraft_id=aircraft.id,
            flight_number=payload.flight_number,
            arrival_iata=payload.arrival_iata,
            status="landed"
        )
        db.add(flight)
        flight.actual_departure = arr_time - timedelta(hours=1)
    else:
        flight.status = "landed"

    # Set arrival details
    if payload.arrival_iata and not flight.arrival_iata:
        flight.arrival_iata = payload.arrival_iata
    flight.actual_arrival = arr_time

    # Ensure coordinates are geocoded
    from app.services.geocoder import geocoder
    if flight.departure_iata and (not flight.departure_lat or not flight.departure_lon):
        dep_coords = await geocoder.get_airport_coordinates(flight.departure_iata)
        if dep_coords:
            flight.departure_lat, flight.departure_lon = dep_coords
    if flight.arrival_iata and (not flight.arrival_lat or not flight.arrival_lon):
        arr_coords = await geocoder.get_airport_coordinates(flight.arrival_iata)
        if arr_coords:
            flight.arrival_lat, flight.arrival_lon = arr_coords

    # 5. Insert a grounded position update
    dest_lat = flight.arrival_lat
    dest_lon = flight.arrival_lon

    if dest_lat and dest_lon:
        from app.models import Position
        new_pos = Position(
            aircraft_id=aircraft.id,
            flight_id=flight.id,
            latitude=dest_lat,
            longitude=dest_lon,
            altitude_ft=0.0,
            ground_speed_kts=0.0,
            heading=0.0,
            vertical_rate_fpm=0.0,
            on_ground=True,
            source="webhook",
            timestamp=flight.actual_arrival,
            location_name=flight.arrival_name or flight.arrival_iata
        )
        db.add(new_pos)

    await db.commit()
    await db.refresh(flight)

    logger.info(f"Flight {flight.id} for {payload.tail_number} marked as LANDED from webhook.")

    # 6. Notify Home Assistant
    from app.services.home_assistant import ha_service
    status_str = ha_service.build_status_string(
        on_ground=True,
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
        "actual_arrival": flight.actual_arrival.isoformat() if flight.actual_arrival else None,
        "status": flight.status,
    }
    
    await ha_service.update_aircraft_sensor(
        tail_number=aircraft.tail_number,
        status=status_str,
        flight_data=flight_data,
        position_data={"on_ground": True, "latitude": dest_lat, "longitude": dest_lon, "timestamp": flight.actual_arrival.isoformat()}
    )

    return flight

