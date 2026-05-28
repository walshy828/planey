"""
Webhooks API Router

Endpoints for external automations (like N8N) to push flight events.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Aircraft, Flight, Position, Setting, record_flight_changes
from app.schemas import (
    WebhookFlightFiled, WebhookFlightDeparted, WebhookFlightArrived,
    WebhookFlightSpotted, WebhookFlightTrackingStopped, FlightResponse,
)

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
    # Check if we already have a scheduled or active flight for this aircraft within 18 hours
    ref_time = payload.scheduled_departure or datetime.now(timezone.utc)
    existing_res = await db.execute(
        select(Flight)
        .where(
            Flight.aircraft_id == aircraft.id,
            Flight.status.in_(["scheduled", "active"])
        )
    )
    for existing in existing_res.scalars().all():
        # Match condition:
        # 1. Same departure airport OR existing has no departure airport (dynamically created)
        # 2. Within 18 hours of scheduled departure
        is_match = False
        if existing.departure_iata == payload.departure_iata or existing.departure_iata is None:
            existing_time = existing.actual_departure or existing.scheduled_departure or existing.created_at
            if existing_time:
                existing_time_naive = existing_time.replace(tzinfo=None)
                ref_time_naive = ref_time.replace(tzinfo=None)
                diff = abs((existing_time_naive - ref_time_naive).total_seconds())
                if diff <= 18 * 3600:
                    is_match = True

        if is_match:
            logger.info(f"Deduplicated and updating flight plan for {payload.tail_number}. Status: {existing.status}")
            # Record change history before applying updates
            updates = {}
            if payload.flight_number and payload.flight_number != existing.flight_number:
                updates["flight_number"] = payload.flight_number
            if payload.callsign and payload.callsign != existing.callsign:
                updates["callsign"] = payload.callsign
            if payload.departure_iata and payload.departure_iata != existing.departure_iata:
                updates["departure_iata"] = payload.departure_iata
            if payload.arrival_iata and payload.arrival_iata != existing.arrival_iata:
                updates["arrival_iata"] = payload.arrival_iata
            if payload.scheduled_departure and payload.scheduled_departure != existing.scheduled_departure:
                updates["scheduled_departure"] = payload.scheduled_departure
            if payload.scheduled_arrival and payload.scheduled_arrival != existing.scheduled_arrival:
                updates["scheduled_arrival"] = payload.scheduled_arrival
            if payload.expected_route and payload.expected_route != existing.expected_route:
                updates["expected_route"] = payload.expected_route

            await record_flight_changes(existing, updates, "webhook_filed", db)

            # Update details in-place
            existing.flight_number = payload.flight_number or existing.flight_number
            existing.callsign = payload.callsign or existing.callsign
            existing.departure_iata = payload.departure_iata or existing.departure_iata
            existing.arrival_iata = payload.arrival_iata or existing.arrival_iata
            existing.scheduled_departure = payload.scheduled_departure or existing.scheduled_departure
            existing.scheduled_arrival = payload.scheduled_arrival or existing.scheduled_arrival
            existing.expected_route = payload.expected_route or existing.expected_route
            
            # Re-geocode the airports if details changed
            from app.services.geocoder import geocoder
            dep_coords = await geocoder.get_airport_coordinates(existing.departure_iata)
            arr_coords = await geocoder.get_airport_coordinates(existing.arrival_iata)
            if dep_coords:
                existing.departure_lat, existing.departure_lon = dep_coords
            if arr_coords:
                existing.arrival_lat, existing.arrival_lon = arr_coords
                
            await db.commit()
            await db.refresh(existing)

            # Retroactively reconcile any captured orphan positions
            from app.services.reconciliation import reconciliation_service
            await reconciliation_service.reconcile_orphan_positions(existing, db)

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
    
    # Retroactively reconcile any captured orphan positions
    from app.services.reconciliation import reconciliation_service
    await reconciliation_service.reconcile_orphan_positions(new_flight, db)

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
    duplicate_flight = None

    # Fuel stop detection: check if a recently-landed flight ended at the same
    # airport we're now departing from (gap < 30 min = fuel stop)
    recent_landed_res = await db.execute(
        select(Flight)
        .where(
            Flight.aircraft_id == aircraft.id,
            Flight.status == "landed",
            Flight.actual_arrival.isnot(None),
        )
        .order_by(Flight.actual_arrival.desc())
        .limit(1)
    )
    recent_landed = recent_landed_res.scalars().first()
    if recent_landed and recent_landed.actual_arrival:
        arr_naive = recent_landed.actual_arrival.replace(tzinfo=None) if recent_landed.actual_arrival.tzinfo else recent_landed.actual_arrival
        dep_naive = dep_time.replace(tzinfo=None) if dep_time.tzinfo else dep_time
        gap_minutes = (dep_naive - arr_naive).total_seconds() / 60
        same_airport = (
            payload.departure_iata and recent_landed.arrival_iata
            and payload.departure_iata == recent_landed.arrival_iata
        )
        if same_airport and 0 < gap_minutes < 45:
            logger.info(
                f"Fuel stop detected: {aircraft.tail_number} landed at "
                f"{recent_landed.arrival_iata} {gap_minutes:.0f}min ago, now departing same airport. "
                f"Tagging flight {recent_landed.id} as fuel_stop."
            )
            # Tag the previous flight for audit/display purposes
            raw = recent_landed.raw_data or {}
            raw["fuel_stop"] = True
            raw["fuel_stop_gap_minutes"] = round(gap_minutes, 1)
            recent_landed.raw_data = raw

    for old_flight in active_flights:
        # A tracker-auto-created flight has departure_iata=None; treat it as matching
        # the same departure (the webhook enriches it rather than duplicating it).
        dep_airports_match = (
            old_flight.departure_iata == payload.departure_iata
            or old_flight.departure_iata is None
        )
        # Check if it's a duplicate webhook (same/compatible departure, within 4 hours)
        is_duplicate = False
        if dep_airports_match:
            ref_dep = old_flight.actual_departure or old_flight.created_at
            if ref_dep:
                old_dep = ref_dep.replace(tzinfo=None) if ref_dep.tzinfo else ref_dep
                dep_time_naive = dep_time.replace(tzinfo=None) if dep_time.tzinfo else dep_time
                if abs((old_dep - dep_time_naive).total_seconds()) < 4 * 3600:
                    is_duplicate = True
                    duplicate_flight = old_flight

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
                # Proactively calculate flight summary statistics upon landing
                try:
                    from app.services.stats_calculator import calculate_flight_stats
                    old_flight.summary_stats = await calculate_flight_stats(old_flight, db)
                    logger.info(f"Self-healed flight statistics: {old_flight.summary_stats}")
                except Exception as e:
                    logger.error(f"Failed to calculate statistics during departure self-healing: {e}")
                await db.commit()

    if duplicate_flight:
        logger.info(
            f"Departure webhook matches existing active flight {duplicate_flight.id} for {aircraft.tail_number}. "
            f"Enriching with webhook data."
        )
        # Enrich the tracker-created flight with airport/route data from the webhook
        updates = {}
        if payload.flight_number and not duplicate_flight.flight_number:
            duplicate_flight.flight_number = payload.flight_number
            updates["flight_number"] = payload.flight_number
        if payload.callsign and not duplicate_flight.callsign:
            duplicate_flight.callsign = payload.callsign
            updates["callsign"] = payload.callsign
        if payload.departure_iata and not duplicate_flight.departure_iata:
            duplicate_flight.departure_iata = payload.departure_iata
            updates["departure_iata"] = payload.departure_iata
        if payload.arrival_iata and not duplicate_flight.arrival_iata:
            duplicate_flight.arrival_iata = payload.arrival_iata
            updates["arrival_iata"] = payload.arrival_iata
        if payload.scheduled_arrival and not duplicate_flight.scheduled_arrival:
            duplicate_flight.scheduled_arrival = payload.scheduled_arrival
            updates["scheduled_arrival"] = payload.scheduled_arrival
        if updates:
            await record_flight_changes(duplicate_flight, updates, "webhook_departed", db)
        # Geocode any newly-set airports
        from app.services.geocoder import geocoder
        if duplicate_flight.departure_iata and (not duplicate_flight.departure_lat or not duplicate_flight.departure_lon):
            dep_coords = await geocoder.get_airport_coordinates(duplicate_flight.departure_iata)
            if dep_coords:
                duplicate_flight.departure_lat, duplicate_flight.departure_lon = dep_coords
        if duplicate_flight.arrival_iata and (not duplicate_flight.arrival_lat or not duplicate_flight.arrival_lon):
            arr_coords = await geocoder.get_airport_coordinates(duplicate_flight.arrival_iata)
            if arr_coords:
                duplicate_flight.arrival_lat, duplicate_flight.arrival_lon = arr_coords
        await db.commit()
        await db.refresh(duplicate_flight)
        return duplicate_flight

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

    # Record status change history
    status_updates = {"status": "active"}
    if payload.arrival_iata and not flight.arrival_iata:
        status_updates["arrival_iata"] = payload.arrival_iata
    await record_flight_changes(flight, status_updates, "webhook_departed", db)

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

    # Dynamic scheduler polling rate adjustment
    try:
        from app.services.tracker import tracker_service
        await tracker_service.update_tracker_polling_interval(db)
    except Exception as e:
        logger.error(f"Failed to update tracking interval: {e}")

    logger.info(f"Flight {flight.id} for {payload.tail_number} marked as ACTIVE from webhook.")

    # 3. Trigger Home Assistant alert so tracking begins in the smart home
    from app.services.home_assistant import ha_service
    status_str = ha_service.build_status_string(
        on_ground=False,
        departure_iata=flight.departure_iata,
        arrival_iata=flight.arrival_iata,
        departure_name=flight.departure_name,
        arrival_name=flight.arrival_name,
        scheduled_arrival=flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
        scheduled_departure=flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
        flight_status=flight.status,
    )
    
    flight_data = {
        "flight_number": flight.flight_number,
        "callsign": flight.callsign,
        "departure_iata": flight.departure_iata,
        "departure_name": flight.departure_name,
        "arrival_iata": flight.arrival_iata,
        "arrival_name": flight.arrival_name,
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

    # Retroactively reconcile any captured orphan positions
    from app.services.reconciliation import reconciliation_service
    await reconciliation_service.reconcile_orphan_positions(flight, db)

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

    arr_time = payload.actual_arrival or datetime.now(timezone.utc)

    # Deduplication / Check if already arrived recently (within 4 hours)
    if not flight:
        recent_landed_res = await db.execute(
            select(Flight)
            .where(
                Flight.aircraft_id == aircraft.id,
                Flight.status == "landed"
            )
            .order_by(Flight.actual_arrival.desc().nullslast())
            .limit(1)
        )
        recent_landed = recent_landed_res.scalars().first()
        if recent_landed and recent_landed.actual_arrival:
            recent_arr = recent_landed.actual_arrival.replace(tzinfo=None)
            arr_time_naive = arr_time.replace(tzinfo=None)
            if abs((recent_arr - arr_time_naive).total_seconds()) < 4 * 3600:
                logger.info(f"Duplicate arrival webhook detected. Returning existing landed flight {recent_landed.id}.")
                return recent_landed

    # 3. If no active flight exists, promote a matching scheduled flight (missed departure webhook)
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
                sched_dep = flight.scheduled_departure
                if sched_dep:
                    if sched_dep.tzinfo is None:
                        sched_dep = sched_dep.replace(tzinfo=timezone.utc)
                    if sched_dep < arr_time:
                        flight.actual_departure = sched_dep
                    else:
                        flight.actual_departure = arr_time - timedelta(hours=1)
                else:
                    flight.actual_departure = arr_time - timedelta(hours=1)
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

    # Record change history for arrival
    arrival_updates = {"status": "landed", "actual_arrival": str(arr_time)}
    if payload.arrival_iata:
        arrival_updates["arrival_iata"] = payload.arrival_iata
    await record_flight_changes(flight, arrival_updates, "webhook_arrived", db)

    # Ensure coordinates are geocoded
    from app.services.geocoder import geocoder
    if flight.departure_iata and (not flight.departure_lat or not flight.departure_lon):
        dep_coords = await geocoder.get_airport_coordinates(flight.departure_iata)
        if dep_coords:
            flight.departure_lat, flight.departure_lon = dep_coords

    arr_coords = None
    if flight.arrival_iata:
        if flight.arrival_lat is not None and flight.arrival_lon is not None:
            arr_coords = (flight.arrival_lat, flight.arrival_lon)
        else:
            arr_coords = await geocoder.get_airport_coordinates(flight.arrival_iata)

    # Validate or fall back to the flight's last known telemetry position
    last_pos_res = await db.execute(
        select(Position)
        .where(Position.flight_id == flight.id)
        .order_by(Position.timestamp.desc())
        .limit(1)
    )
    last_pos = last_pos_res.scalars().first()

    if last_pos:
        from app.services.stats_calculator import haversine_distance
        if arr_coords:
            # Check distance in NM between geocoded coordinates and the last known telemetry position
            dist = haversine_distance(arr_coords[0], arr_coords[1], last_pos.latitude, last_pos.longitude)
            if dist > 50.0:
                logger.warning(
                    f"Geocoded arrival coordinates for {flight.arrival_iata} ({arr_coords}) "
                    f"are {dist:.1f} NM away from last known position ({last_pos.latitude}, {last_pos.longitude}). "
                    f"Using last known position coordinates instead."
                )
                arr_coords = (last_pos.latitude, last_pos.longitude)
        else:
            # Geocoding failed entirely, use last known position coordinates as fallback
            logger.info(
                f"Geocoding failed for {flight.arrival_iata}. "
                f"Falling back to last known position coordinates ({last_pos.latitude}, {last_pos.longitude})."
            )
            arr_coords = (last_pos.latitude, last_pos.longitude)

    if arr_coords:
        flight.arrival_lat, flight.arrival_lon = arr_coords

    # Reverse geocode the landing site name if missing
    if flight.arrival_lat is not None and flight.arrival_lon is not None and not flight.arrival_name:
        try:
            flight.arrival_name = await geocoder.get_location_name(flight.arrival_lat, flight.arrival_lon)
        except Exception as e:
            logger.error(f"Failed to reverse geocode landing site name: {e}")

    # 5. Insert a grounded position update
    dest_lat = flight.arrival_lat
    dest_lon = flight.arrival_lon

    if dest_lat and dest_lon:
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

    # Proactively calculate flight summary statistics upon landing
    try:
        from app.services.stats_calculator import calculate_flight_stats
        flight.summary_stats = await calculate_flight_stats(flight, db)
        logger.info(f"Arrived webhook flight statistics: {flight.summary_stats}")
    except Exception as e:
        logger.error(f"Failed to calculate statistics inside arrival webhook: {e}")

    await db.commit()
    await db.refresh(flight)

    # Retroactively reconcile any captured orphan positions
    from app.services.reconciliation import reconciliation_service
    await reconciliation_service.reconcile_orphan_positions(flight, db)

    # Dynamic scheduler polling rate adjustment
    try:
        from app.services.tracker import tracker_service
        await tracker_service.update_tracker_polling_interval(db)
    except Exception as e:
        logger.error(f"Failed to update tracking interval: {e}")

    logger.info(f"Flight {flight.id} for {payload.tail_number} marked as LANDED from webhook.")

    # 6. Notify Home Assistant
    from app.services.home_assistant import ha_service
    status_str = ha_service.build_status_string(
        on_ground=True,
        departure_iata=flight.departure_iata,
        arrival_iata=flight.arrival_iata,
        departure_name=flight.departure_name,
        arrival_name=flight.arrival_name,
        scheduled_arrival=flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
        scheduled_departure=flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
        flight_status=flight.status,
    )
    
    flight_data = {
        "flight_number": flight.flight_number,
        "callsign": flight.callsign,
        "departure_iata": flight.departure_iata,
        "departure_name": flight.departure_name,
        "arrival_iata": flight.arrival_iata,
        "arrival_name": flight.arrival_name,
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


@router.post("/flight-spotted", status_code=status.HTTP_200_OK)
async def webhook_flight_spotted(
    payload: WebhookFlightSpotted,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when FlightAware spots an aircraft in flight with no filed plan.
    Triggers an immediate position poll and activates fast polling mode.
    """
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    # If already actively tracked, nothing more to do
    active_res = await db.execute(
        select(Flight).where(Flight.aircraft_id == aircraft.id, Flight.status == "active")
    )
    active_flight = active_res.scalars().first()
    if active_flight:
        logger.info(f"flight-spotted: {payload.tail_number} already has active flight {active_flight.id}")
        return active_flight

    # Activate fast polling mode so the scheduler switches to airborne interval immediately
    now = datetime.now(timezone.utc)
    for key, value in [("manual_airborne_mode", "true"), ("manual_airborne_mode_set_at", now.isoformat())]:
        res = await db.execute(select(Setting).where(Setting.key == key))
        setting = res.scalars().first()
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    await db.commit()

    logger.info(
        f"flight-spotted: {payload.tail_number} spotted near {payload.location or 'unknown location'} "
        f"at {payload.spotted_time or now}. Activating fast polling and triggering immediate poll."
    )

    # Fire an immediate poll to grab real GPS coordinates — let the tracker pipeline
    # create the flight record from live telemetry
    try:
        from app.services.tracker import tracker_service
        await tracker_service.update_tracker_polling_interval(db)
        await tracker_service.poll_single_aircraft(aircraft.id)
    except Exception as e:
        logger.error(f"Immediate poll failed for {payload.tail_number}: {e}")

    # Return the flight if the poll just created one
    active_res = await db.execute(
        select(Flight).where(Flight.aircraft_id == aircraft.id, Flight.status == "active")
    )
    active_flight = active_res.scalars().first()
    if active_flight:
        return active_flight

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "tracking_queued",
            "tail_number": payload.tail_number,
            "location": payload.location,
            "message": "Fast polling activated. Position will be captured at next poll cycle.",
        }
    )


@router.post("/flight-tracking-stopped", response_model=FlightResponse, status_code=status.HTTP_200_OK)
async def webhook_flight_tracking_stopped(
    payload: WebhookFlightTrackingStopped,
    db: AsyncSession = Depends(get_db),
    _authorized: bool = Depends(verify_token)
):
    """
    Called by N8N when FlightAware stops tracking an aircraft.
    Marks the active flight as landed using the last known telemetry position for coordinates.
    """
    ac_result = await db.execute(select(Aircraft).where(Aircraft.tail_number == payload.tail_number.upper()))
    aircraft = ac_result.scalars().first()
    if not aircraft:
        raise HTTPException(status_code=404, detail=f"Aircraft {payload.tail_number} not found")

    stop_time = payload.tracking_stopped_time or datetime.now(timezone.utc)

    # Find the active flight
    flight_res = await db.execute(
        select(Flight)
        .where(Flight.aircraft_id == aircraft.id, Flight.status == "active")
        .order_by(Flight.actual_departure.desc().nullslast())
        .limit(1)
    )
    flight = flight_res.scalars().first()

    # Dedup: if already recently landed (within 4 hours), return it
    if not flight:
        recent_res = await db.execute(
            select(Flight)
            .where(Flight.aircraft_id == aircraft.id, Flight.status == "landed")
            .order_by(Flight.actual_arrival.desc().nullslast())
            .limit(1)
        )
        recent = recent_res.scalars().first()
        if recent and recent.actual_arrival:
            recent_arr = recent.actual_arrival.replace(tzinfo=None) if recent.actual_arrival.tzinfo else recent.actual_arrival
            stop_naive = stop_time.replace(tzinfo=None) if stop_time.tzinfo else stop_time
            if abs((recent_arr - stop_naive).total_seconds()) < 4 * 3600:
                logger.info(f"flight-tracking-stopped: duplicate for {payload.tail_number}, returning existing landed flight {recent.id}")
                return recent

    # Dynamically create a landed flight if nothing active exists
    if not flight:
        logger.info(f"flight-tracking-stopped: no active flight for {payload.tail_number}, creating landed record dynamically")
        flight = Flight(
            aircraft_id=aircraft.id,
            status="landed",
            actual_departure=stop_time - timedelta(hours=1),
        )
        db.add(flight)
        await db.flush()
    else:
        flight.status = "landed"

    flight.actual_arrival = stop_time

    # Use location strings as names if we don't already have them
    if payload.location and not flight.arrival_name:
        flight.arrival_name = payload.location
    if payload.from_location and not flight.departure_name:
        flight.departure_name = payload.from_location

    # Use last known telemetry position for arrival coordinates — more accurate
    # than forward-geocoding a city string like "Orange, MA"
    last_pos_res = await db.execute(
        select(Position)
        .where(Position.flight_id == flight.id)
        .order_by(Position.timestamp.desc())
        .limit(1)
    )
    last_pos = last_pos_res.scalars().first()
    if last_pos and (not flight.arrival_lat or not flight.arrival_lon):
        flight.arrival_lat = last_pos.latitude
        flight.arrival_lon = last_pos.longitude

    # Record change history
    await record_flight_changes(
        flight,
        {"status": "landed", "actual_arrival": str(stop_time), "arrival_name": flight.arrival_name},
        "webhook_tracking_stopped",
        db,
    )

    # Calculate flight summary statistics
    try:
        from app.services.stats_calculator import calculate_flight_stats
        flight.summary_stats = await calculate_flight_stats(flight, db)
    except Exception as e:
        logger.error(f"Failed to calculate stats in tracking-stopped webhook: {e}")

    await db.commit()
    await db.refresh(flight)

    logger.info(
        f"Flight {flight.id} for {payload.tail_number} marked LANDED via tracking-stopped webhook. "
        f"Location: {payload.location or 'unknown'}, from: {payload.from_location or 'unknown'}"
    )

    # Switch back to passive polling now that the flight is done
    try:
        from app.services.tracker import tracker_service
        await tracker_service.update_tracker_polling_interval(db)
    except Exception as e:
        logger.error(f"Failed to update polling interval: {e}")

    # Notify Home Assistant
    from app.services.home_assistant import ha_service
    status_str = ha_service.build_status_string(
        on_ground=True,
        departure_iata=flight.departure_iata,
        arrival_iata=flight.arrival_iata,
        departure_name=flight.departure_name,
        arrival_name=flight.arrival_name,
        scheduled_arrival=flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
        scheduled_departure=flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
        flight_status=flight.status,
    )
    flight_data = {
        "flight_number": flight.flight_number,
        "callsign": flight.callsign,
        "departure_iata": flight.departure_iata,
        "departure_name": flight.departure_name,
        "arrival_iata": flight.arrival_iata,
        "arrival_name": flight.arrival_name,
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
        position_data={"on_ground": True, "latitude": flight.arrival_lat, "longitude": flight.arrival_lon},
    )

    return flight

