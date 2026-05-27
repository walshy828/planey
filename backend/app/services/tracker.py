"""
Tracker Service

Core orchestrator that runs on a schedule to:
1. Fetch latest positions from OpenSky Network
2. Store positions in the database
3. Update flight status based on position data
4. Broadcast updates via WebSocket
5. Sync state to Home Assistant
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Aircraft, Flight, Position, record_flight_changes
from app.services.opensky import opensky_client
from app.services.flightradar import fr24_client
from app.services.flightaware import fa_client
from app.services.geocoder import geocoder
from app.services.home_assistant import ha_service
from app.services.websocket import ws_manager

logger = logging.getLogger(__name__)


class TrackerService:
    """Main tracking orchestrator."""

    def __init__(self):
        self.last_poll_time = None
        self.last_poll_status = "never"
        self.is_airborne_mode = False
        self.current_interval = 300
        # Landing confirmation: track per-flight on-ground state
        # Key: flight_id (str), Value: {"count": int, "first_ts": datetime}
        self._landing_states: dict = {}

    async def _broadcast_tracker_status(self):
        try:
            last_poll_iso = self.last_poll_time.isoformat() if self.last_poll_time else None
            await ws_manager.broadcast({
                "type": "tracker_status",
                "last_poll_time": last_poll_iso,
                "last_poll_status": self.last_poll_status,
                "is_airborne_mode": self.is_airborne_mode,
                "current_interval": self.current_interval,
            })
        except Exception as ws_err:
            logger.warning(f"Failed to broadcast tracker status via WS: {ws_err}")

    async def sync_flight_schedules(self):
        """
        Background job to sync flight schedules for all active aircraft.
        Runs less frequently (e.g., every 30-60 minutes).
        """
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Aircraft).where(Aircraft.active == True)
                )
                aircraft_list = result.scalars().all()

                logger.info(f"Syncing flight schedules for {len(aircraft_list)} aircraft")

                for ac in aircraft_list:
                    try:
                        # 1. Get schedule from external sources
                        # This will check FlightAware first, then FR24
                        data = await fr24_client.lookup_by_registration(ac.tail_number)
                        
                        if not data:
                            continue

                        # Update aircraft metadata if missing
                        if not ac.aircraft_type and data.get("aircraft_type"):
                            ac.aircraft_type = data["aircraft_type"]
                        if not ac.airline and data.get("airline"):
                            ac.airline = data["airline"]
                        if not ac.icao24_hex and data.get("icao24_hex"):
                            ac.icao24_hex = data["icao24_hex"]

                        # 2. Check if we already have this flight
                        flight_num = data.get("flight_number")
                        if not flight_num:
                            continue

                        existing_flight = await session.execute(
                            select(Flight).where(
                                Flight.aircraft_id == ac.id,
                                Flight.flight_number == flight_num,
                                Flight.status.in_(["scheduled", "active"])
                            )
                        )
                        if existing_flight.scalars().first():
                            continue # Already have it

                        # 3. Add new flight if found
                        new_flight = Flight(
                            aircraft_id=ac.id,
                            flight_number=flight_num,
                            callsign=data.get("callsign") or flight_num,
                            departure_iata=data.get("departure_iata"),
                            arrival_iata=data.get("arrival_iata"),
                            status=data.get("status") or "scheduled",
                            scheduled_departure=data.get("scheduled_departure"),
                            scheduled_arrival=data.get("scheduled_arrival"),
                        )
                        session.add(new_flight)
                        logger.info(f"Auto-discovered flight {flight_num} for {ac.tail_number}")

                    except Exception as e:
                        logger.warning(f"Failed to sync schedule for {ac.tail_number}: {e}")

                await session.commit()

        except Exception as e:
            logger.error(f"Flight schedule sync failed: {e}", exc_info=True)

    async def poll_single_aircraft(self, aircraft_id: uuid.UUID):
        """Poll OpenSky for a specific aircraft immediately."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Aircraft).where(Aircraft.id == aircraft_id)
                )
                aircraft = result.scalars().first()
                if not aircraft or not aircraft.icao24_hex:
                    return None

                logger.info(f"Manual poll triggered for {aircraft.tail_number}")
                
                # 1. Try OpenSky first
                if aircraft.icao24_hex:
                    state_vectors = await opensky_client.get_states([aircraft.icao24_hex.lower()])
                    if state_vectors:
                        logger.info(f"OpenSky found position for {aircraft.tail_number}")
                        return await self._process_state_vector(state_vectors[0], aircraft, session)

                # 2. Try FR24 fallback
                logger.info(f"OpenSky failed. Trying FR24 fallback for {aircraft.tail_number}")
                fr24_pos = await fr24_client.get_position_by_registration(aircraft.tail_number)
                if fr24_pos:
                    logger.info(f"FR24 found position for {aircraft.tail_number}")
                    # Find or dynamically create active flight
                    flight = await self._get_or_create_active_flight(
                        aircraft,
                        fr24_pos["on_ground"],
                        fr24_pos.get("callsign") or fr24_pos.get("flight_number"),
                        fr24_pos["timestamp"],
                        session
                    )

                    if not flight:
                        logger.warning(f"Skipping position for {aircraft.tail_number}: no active flight found or could be created.")
                        return None

                    # Validate timestamp within flight range
                    if not self.is_timestamp_within_flight_range(flight, fr24_pos["timestamp"]):
                        logger.warning(f"Skipping position for {aircraft.tail_number}: timestamp {fr24_pos['timestamp']} outside flight {flight.flight_number} range.")
                        return None

                    # Create position record
                    new_pos = Position(
                        aircraft_id=aircraft.id,
                        flight_id=flight.id,
                        latitude=fr24_pos["latitude"],
                        longitude=fr24_pos["longitude"],
                        altitude_ft=fr24_pos["altitude_ft"],
                        ground_speed_kts=fr24_pos["ground_speed_kts"],
                        heading=fr24_pos["heading"],
                        vertical_rate_fpm=fr24_pos["vertical_rate_fpm"],
                        on_ground=fr24_pos["on_ground"],
                        timestamp=fr24_pos["timestamp"],
                        source="flightradar24",
                        location_name=await geocoder.get_location_name(fr24_pos["latitude"], fr24_pos["longitude"])
                    )
                    session.add(new_pos)
                    
                    # Broadcast
                    await ws_manager.broadcast({
                        "type": "position_update",
                        "aircraft_id": str(aircraft.id),
                        "tail_number": aircraft.tail_number,
                        "data": {
                            "latitude": new_pos.latitude,
                            "longitude": new_pos.longitude,
                            "altitude_ft": new_pos.altitude_ft,
                            "ground_speed_kts": new_pos.ground_speed_kts,
                            "heading": new_pos.heading,
                            "on_ground": new_pos.on_ground,
                            "timestamp": new_pos.timestamp.isoformat()
                        }
                    })
                    await session.commit()
                    return new_pos

                return None
        except Exception as e:
            logger.error(f"Manual poll failed for {aircraft_id}: {e}")
            return None

    async def _process_state_vector(self, sv, aircraft, session):
        """Internal helper to process a single state vector and save to DB."""
        # Find or dynamically create active flight
        flight = await self._get_or_create_active_flight(
            aircraft,
            sv.on_ground,
            sv.callsign,
            sv.timestamp,
            session
        )

        if not flight:
            logger.warning(f"Skipping position for {aircraft.tail_number}: no active flight found or could be created.")
            return None

        # Validate timestamp within flight range
        pos_time = datetime.fromtimestamp(sv.time_position or sv.last_contact, timezone.utc)
        if not self.is_timestamp_within_flight_range(flight, pos_time):
            logger.warning(f"Skipping position for {aircraft.tail_number}: timestamp {pos_time} outside flight {flight.flight_number} range.")
            return None

        # Update flight status if airborne
        if not sv.on_ground and flight.status == "scheduled":
            flight.status = "active"
            flight.actual_departure = datetime.now(timezone.utc)
            await ws_manager.broadcast({
                "type": "flight_status",
                "aircraft_id": str(aircraft.id),
                "tail_number": aircraft.tail_number,
                "old_status": "scheduled",
                "new_status": "active"
            })

        # Create position record
        new_pos = Position(
            aircraft_id=aircraft.id,
            flight_id=flight.id,
            latitude=sv.latitude,
            longitude=sv.longitude,
            altitude_ft=sv.baro_altitude_m * 3.28084 if sv.baro_altitude_m is not None else None,
            ground_speed_kts=sv.velocity_mps * 1.94384 if sv.velocity_mps is not None else None,
            heading=sv.true_track,
            vertical_rate_fpm=sv.vertical_rate_mps * 196.85 if sv.vertical_rate_mps is not None else None,
            on_ground=sv.on_ground,
            squawk=sv.squawk,
            timestamp=pos_time,
            location_name=await geocoder.get_location_name(sv.latitude, sv.longitude)
        )
        session.add(new_pos)
        
        # Broadcast update
        await ws_manager.broadcast({
            "type": "position_update",
            "aircraft_id": str(aircraft.id),
            "tail_number": aircraft.tail_number,
            "data": {
                "latitude": new_pos.latitude,
                "longitude": new_pos.longitude,
                "altitude_ft": new_pos.altitude_ft,
                "ground_speed_kts": new_pos.ground_speed_kts,
                "heading": new_pos.heading,
                "vertical_rate_fpm": new_pos.vertical_rate_fpm,
                "on_ground": new_pos.on_ground,
                "timestamp": new_pos.timestamp.isoformat()
            }
        })
        
        await session.commit()
        return new_pos

    async def poll_positions(self):
        """
        Main polling cycle — called every POLLING_INTERVAL_SECONDS.

        1. Get all active aircraft with ICAO24 addresses
        2. Batch-fetch positions from OpenSky
        3. Store positions, update flights, broadcast, sync HA
        """
        try:
            self.last_poll_time = datetime.now(timezone.utc)
            self.last_poll_status = "polling"
            await self._broadcast_tracker_status()

            async with async_session() as session:
                # Get all active aircraft with ICAO24 addresses
                result = await session.execute(
                    select(Aircraft).where(
                        Aircraft.active == True,
                        Aircraft.icao24_hex.isnot(None),
                    )
                )
                aircraft_list = result.scalars().all()

                if not aircraft_list:
                    logger.debug("No active aircraft with ICAO24 to track")
                    self.last_poll_status = "no_aircraft"
                    await self._broadcast_tracker_status()
                    return

                # Build ICAO24 → Aircraft mapping
                icao_map = {a.icao24_hex.lower(): a for a in aircraft_list}
                icao_list = list(icao_map.keys())

                logger.info(f"Polling OpenSky for {len(icao_list)} aircraft")

                # Fetch positions from OpenSky
                state_vectors = await opensky_client.get_states(icao_list)

                if not state_vectors:
                    logger.debug("No positions returned from OpenSky (possibly rate limited). Falling back to FR24...")
                    state_vectors = []

                # Find which aircraft we got data for
                found_icaos = {sv.icao24.lower() for sv in state_vectors}
                missing_aircraft = [ac for ac in aircraft_list if ac.icao24_hex.lower() not in found_icaos]

                # Fallback to FlightRadar24 for missing aircraft
                for ac in missing_aircraft:
                    try:
                        pos_data = await fr24_client.get_position_by_registration(ac.tail_number)
                        if pos_data:
                            logger.info(f"Got fallback FR24 position for {ac.tail_number}")
                            
                            # Create a fake OpenSky StateVector to reuse the existing pipeline
                            from app.services.opensky import StateVector
                            sv = StateVector(
                                icao24=ac.icao24_hex.lower(),
                                callsign=ac.tail_number,
                                origin_country="",
                                time_position=int(pos_data["timestamp"].timestamp()),
                                last_contact=int(pos_data["timestamp"].timestamp()),
                                longitude=pos_data["longitude"],
                                latitude=pos_data["latitude"],
                                baro_altitude_m=pos_data["altitude_ft"] / 3.28084 if pos_data.get("altitude_ft") else None,
                                on_ground=pos_data.get("on_ground", False),
                                velocity_mps=pos_data["ground_speed_kts"] / 1.94384 if pos_data.get("ground_speed_kts") else None,
                                true_track=pos_data.get("heading"),
                                vertical_rate_mps=pos_data["vertical_rate_fpm"] / 196.85 if pos_data.get("vertical_rate_fpm") else None,
                                sensors=[],
                                geo_altitude_m=None,
                                squawk=None,
                                spi=False,
                                position_source=0
                            )
                            state_vectors.append(sv)
                    except Exception as e:
                        logger.error(f"FR24 fallback failed for {ac.tail_number}: {e}")

                if not state_vectors:
                    logger.debug("No positions returned from OpenSky or FR24")
                    self.last_poll_status = "no_data"
                    # Still update HA for aircraft we're tracking but have no data for
                    for aircraft in aircraft_list:
                        await self._update_ha_no_position(aircraft, session)
                    await self.update_tracker_polling_interval(session)
                    await self._broadcast_tracker_status()
                    return

                # Process each state vector
                for sv in state_vectors:
                    aircraft = icao_map.get(sv.icao24.lower())
                    if not aircraft:
                        continue

                    # Find or dynamically create active flight for this aircraft
                    active_flight = await self._get_or_create_active_flight(
                        aircraft,
                        sv.on_ground,
                        sv.callsign,
                        sv.timestamp,
                        session,
                        latitude=sv.latitude,
                        longitude=sv.longitude,
                    )

                    if not active_flight:
                        logger.warning(f"Skipping position for {aircraft.tail_number}: no active flight found or could be created.")
                        continue

                    # Validate timestamp within flight range
                    if not self.is_timestamp_within_flight_range(active_flight, sv.timestamp):
                        logger.warning(f"Skipping position for {aircraft.tail_number}: timestamp {sv.timestamp} outside flight {active_flight.flight_number} range.")
                        continue

                    # Store position
                    position = Position(
                        aircraft_id=aircraft.id,
                        flight_id=active_flight.id,
                        latitude=sv.latitude,
                        longitude=sv.longitude,
                        altitude_ft=sv.altitude_ft,
                        ground_speed_kts=sv.ground_speed_kts,
                        heading=sv.heading,
                        vertical_rate_fpm=sv.vertical_rate_fpm,
                        on_ground=sv.on_ground,
                        squawk=sv.squawk,
                        source="opensky",
                        timestamp=sv.timestamp,
                        location_name=await geocoder.get_location_name(sv.latitude, sv.longitude)
                    )
                    session.add(position)

                    # Update flight status if needed
                    if active_flight:
                        await self._update_flight_status(active_flight, sv, session, aircraft.category)

                    # Broadcast via WebSocket
                    await self._broadcast_position(aircraft, position, active_flight)

                    # Update Home Assistant
                    await self._update_ha(aircraft, position, active_flight)

                await session.commit()
                logger.info(f"Stored {len(state_vectors)} positions")
                self.last_poll_status = "success"
                await self.update_tracker_polling_interval(session)
                await self._broadcast_tracker_status()

        except Exception as e:
            logger.error(f"Tracker poll failed: {e}", exc_info=True)
            self.last_poll_status = "error"
            await self._broadcast_tracker_status()

    async def _get_or_create_active_flight(
        self,
        aircraft: Aircraft,
        is_on_ground: bool,
        callsign: Optional[str],
        timestamp: datetime,
        session: AsyncSession,
        latitude: float = None,
        longitude: float = None,
    ) -> Optional[Flight]:
        """
        Retrieves the active/scheduled flight. If none exists and the aircraft
        is airborne, dynamically creates a new active flight and runs position reconciliation.
        Captures departure coordinates from the first position for helicopter/VFR flights.
        """
        flight = await self._get_active_flight(aircraft.id, timestamp, session)
        if flight:
            return flight

        if not is_on_ground:
            # Before creating a new flight, check if a recent flight was prematurely closed
            # by reconciliation while the aircraft was still airborne. If so, reopen it instead
            # of creating a duplicate flight record for the same physical leg.
            ts_aware = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            recent_res = await session.execute(
                select(Flight)
                .where(
                    Flight.aircraft_id == aircraft.id,
                    Flight.status == "landed",
                    Flight.actual_arrival.isnot(None),
                )
                .order_by(Flight.actual_arrival.desc())
                .limit(1)
            )
            reopen_candidate = recent_res.scalars().first()

            if reopen_candidate and reopen_candidate.actual_arrival:
                arr = reopen_candidate.actual_arrival
                if arr.tzinfo is None:
                    arr = arr.replace(tzinfo=timezone.utc)
                gap_seconds = (ts_aware - arr).total_seconds()

                if 0 < gap_seconds < 900:  # closed within the last 15 min
                    last_real_res = await session.execute(
                        select(Position)
                        .where(
                            Position.flight_id == reopen_candidate.id,
                            Position.source != 'reconciliation',
                        )
                        .order_by(Position.timestamp.desc())
                        .limit(1)
                    )
                    last_real = last_real_res.scalars().first()

                    if last_real and not last_real.on_ground:
                        logger.info(
                            f"Reopening prematurely-closed flight {reopen_candidate.id} for "
                            f"{aircraft.tail_number}: closed {gap_seconds / 60:.1f} min ago "
                            f"but last real position was airborne at {last_real.timestamp}"
                        )
                        reopen_candidate.status = "active"
                        reopen_candidate.actual_arrival = None
                        await record_flight_changes(
                            reopen_candidate,
                            {"status": "active", "actual_arrival": None},
                            "tracker_reopen",
                            session,
                        )
                        return reopen_candidate

            flight_num = callsign.strip() if callsign else aircraft.tail_number
            logger.info(f"Airborne telemetry detected for {aircraft.tail_number} with no active flight. Auto-creating active flight {flight_num}.")

            # Geocode departure location from first airborne position
            departure_name = None
            if latitude is not None and longitude is not None:
                try:
                    departure_name = await geocoder.get_location_name(latitude, longitude)
                except Exception as e:
                    logger.warning(f"Failed to geocode departure for auto-created flight: {e}")

            new_flight = Flight(
                aircraft_id=aircraft.id,
                flight_number=flight_num,
                callsign=flight_num,
                status="active",
                actual_departure=timestamp,
                departure_lat=latitude,
                departure_lon=longitude,
                departure_name=departure_name,
                raw_data={"source": "auto-detected", "first_position_timestamp": timestamp.isoformat()}
            )
            session.add(new_flight)
            await session.flush()  # Populate the ID

            # Record the auto-creation in change history
            await record_flight_changes(new_flight, {
                "status": "active",
                "departure_name": departure_name,
            }, "tracker_auto_create", session)

            try:
                from app.services.reconciliation import reconciliation_service
                await reconciliation_service.reconcile_orphan_positions(new_flight, session)
            except Exception as e:
                logger.error(f"Failed to reconcile orphan positions for auto-created flight: {e}")

            # Check if this takeoff follows a short stop at the same location (fuel stop / heli stop)
            await self._check_and_tag_fuel_stop(aircraft, latitude, longitude, timestamp, session)

            return new_flight

        return None

    async def _check_and_tag_fuel_stop(
        self,
        aircraft: Aircraft,
        latitude: Optional[float],
        longitude: Optional[float],
        departure_ts: datetime,
        session: AsyncSession,
    ) -> None:
        """
        If the aircraft just took off within 45 min of its last landing at the same location,
        tag the landed flight as a fuel/technical stop in raw_data.
        """
        try:
            recent_res = await session.execute(
                select(Flight)
                .where(
                    Flight.aircraft_id == aircraft.id,
                    Flight.status == "landed",
                    Flight.actual_arrival.isnot(None),
                )
                .order_by(Flight.actual_arrival.desc())
                .limit(1)
            )
            recent = recent_res.scalars().first()
            if not recent or not recent.actual_arrival:
                return

            arr = recent.actual_arrival
            if arr.tzinfo is None:
                arr = arr.replace(tzinfo=timezone.utc)
            dep = departure_ts
            if dep.tzinfo is None:
                dep = dep.replace(tzinfo=timezone.utc)

            gap_minutes = (dep - arr).total_seconds() / 60
            if not (0 < gap_minutes < 45):
                return

            # Check proximity — takeoff coords must be within 5 NM of landing coords
            if latitude is not None and longitude is not None and recent.arrival_lat and recent.arrival_lon:
                from app.services.stats_calculator import haversine_distance
                dist_nm = haversine_distance(latitude, longitude, recent.arrival_lat, recent.arrival_lon)
                if dist_nm > 5.0:
                    return

            raw = recent.raw_data or {}
            raw["stop_type"] = "fuel_stop"
            raw["fuel_stop_gap_minutes"] = round(gap_minutes, 1)
            recent.raw_data = raw
            logger.info(
                f"Position-based fuel stop: {aircraft.tail_number} took off "
                f"{gap_minutes:.0f} min after landing at "
                f"{recent.arrival_name or recent.arrival_iata or 'unknown'}. "
                f"Tagged flight {recent.id}."
            )
        except Exception as e:
            logger.warning(f"Fuel stop check failed for {aircraft.tail_number}: {e}")

    async def _get_active_flight(self, aircraft_id, timestamp: datetime, session: AsyncSession) -> Optional[Flight]:
        """Get the currently active or closest scheduled flight for an aircraft.
        
        Selects the flight closest in time to the given position timestamp,
        rather than just the most recently scheduled one. This prevents future
        flights from stealing positions meant for current/previous flights.
        """
        result = await session.execute(
            select(Flight).where(
                Flight.aircraft_id == aircraft_id,
                Flight.status.in_(["scheduled", "active"]),
            )
        )
        candidates = result.scalars().all()
        if not candidates:
            return None

        # Always prefer active flights first
        active = [f for f in candidates if f.status == "active"]
        if active:
            return active[0]

        # For scheduled flights, pick the one closest in time to the position timestamp
        from sqlalchemy import func
        ts = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        def flight_proximity(f):
            dep = f.scheduled_departure or f.actual_departure
            if dep is None:
                return float('inf')
            dep_naive = dep.replace(tzinfo=None) if dep.tzinfo else dep
            return abs((ts - dep_naive).total_seconds())

        candidates.sort(key=flight_proximity)
        return candidates[0]

    async def _update_flight_status(self, flight: Flight, sv, session: AsyncSession, aircraft_category: str = 'plane'):
        """Update flight status based on position data.

        Landing confirmation thresholds (prevents false positives from touch-and-gos):
        - Helicopters: 3 consecutive on-ground positions OR first_on_ground + 5 min
        - Fixed-wing:  5 consecutive on-ground positions OR first_on_ground + 8 min
        """
        old_status = flight.status
        flight_key = str(flight.id)

        is_helicopter = aircraft_category == 'helicopter'
        count_threshold = 3 if is_helicopter else 5
        time_threshold_min = 5 if is_helicopter else 8

        if sv.on_ground:
            if old_status == "active":
                pos_time = sv.timestamp if sv.timestamp.tzinfo else sv.timestamp.replace(tzinfo=timezone.utc)

                if flight_key not in self._landing_states:
                    self._landing_states[flight_key] = {"count": 1, "first_ts": pos_time}
                else:
                    self._landing_states[flight_key]["count"] += 1

                state = self._landing_states[flight_key]
                count = state["count"]
                elapsed_min = (pos_time - state["first_ts"]).total_seconds() / 60

                confirmed_by_count = count >= count_threshold
                confirmed_by_time = elapsed_min >= time_threshold_min

                if not confirmed_by_count and not confirmed_by_time:
                    logger.debug(
                        f"Flight {flight.flight_number} on-ground {count}/{count_threshold} "
                        f"({elapsed_min:.1f}/{time_threshold_min} min) — awaiting landing confirmation"
                    )
                    return

                # Landing confirmed
                del self._landing_states[flight_key]
                logger.info(
                    f"Flight {flight.flight_number} landing confirmed: "
                    f"{'time-based' if confirmed_by_time and not confirmed_by_count else 'count-based'} "
                    f"({count} positions, {elapsed_min:.1f} min on ground)"
                )

                # Populate arrival_name from the last position if no arrival airport
                arrival_name = flight.arrival_name
                if not flight.arrival_iata and not flight.arrival_name:
                    try:
                        arrival_name = await geocoder.get_location_name(sv.latitude, sv.longitude)
                    except Exception:
                        pass

                # Record change history
                landing_updates = {
                    "status": "landed",
                    "actual_arrival": str(datetime.now(timezone.utc)),
                }
                if arrival_name and arrival_name != flight.arrival_name:
                    landing_updates["arrival_name"] = arrival_name
                await record_flight_changes(flight, landing_updates, "tracker", session)

                flight.status = "landed"
                flight.actual_arrival = datetime.now(timezone.utc)
                if arrival_name:
                    flight.arrival_name = arrival_name
                    flight.arrival_lat = sv.latitude
                    flight.arrival_lon = sv.longitude
                logger.info(f"Flight {flight.flight_number} has landed")

                # Proactively calculate flight summary statistics upon landing
                try:
                    from app.services.stats_calculator import calculate_flight_stats
                    flight.summary_stats = await calculate_flight_stats(flight, session)
                    logger.info(f"Proactively calculated statistics for landed flight {flight.flight_number}: {flight.summary_stats}")
                except Exception as e:
                    logger.error(f"Failed to calculate statistics during live tracking landing: {e}")

                # Broadcast status change
                await ws_manager.broadcast({
                    "type": "flight_status",
                    "flight_id": str(flight.id),
                    "aircraft_id": str(flight.aircraft_id),
                    "old_status": old_status,
                    "new_status": "landed",
                    "summary_stats": flight.summary_stats,
                })
        else:
            # Aircraft is airborne — reset any pending landing confirmation
            if flight_key in self._landing_states:
                state = self._landing_states.pop(flight_key)
                logger.debug(
                    f"Flight {flight.flight_number} back airborne — "
                    f"resetting landing state (was {state['count']} on-ground readings)"
                )

            if old_status == "scheduled":
                # Was scheduled, now in the air = active
                await record_flight_changes(flight, {"status": "active"}, "tracker", session)
                flight.status = "active"
                flight.actual_departure = datetime.now(timezone.utc)
                logger.info(f"Flight {flight.flight_number} has departed")

                await ws_manager.broadcast({
                    "type": "flight_status",
                    "flight_id": str(flight.id),
                    "aircraft_id": str(flight.aircraft_id),
                    "old_status": old_status,
                    "new_status": "active",
                })

    async def _broadcast_position(self, aircraft: Aircraft, position: Position, flight: Optional[Flight]):
        """Broadcast position update via WebSocket."""
        await ws_manager.broadcast({
            "type": "position_update",
            "aircraft_id": str(aircraft.id),
            "tail_number": aircraft.tail_number,
            "flight_id": str(flight.id) if flight else None,
            "data": {
                "latitude": position.latitude,
                "longitude": position.longitude,
                "altitude_ft": position.altitude_ft,
                "ground_speed_kts": position.ground_speed_kts,
                "heading": position.heading,
                "vertical_rate_fpm": position.vertical_rate_fpm,
                "on_ground": position.on_ground,
                "squawk": position.squawk,
                "timestamp": position.timestamp.isoformat() if position.timestamp else None,
            },
        })

    @staticmethod
    def is_timestamp_within_flight_range(flight: Flight, timestamp: datetime) -> bool:
        """
        Validate that a position report timestamp lies within the allowed flight timeframe:
        - Active/Scheduled flights: t >= min(scheduled_departure, actual_departure) - 15 minutes
        - Completed/Landed/Cancelled flights: min - 15 minutes <= t <= max(scheduled_arrival, actual_arrival) + 15 minutes
        """
        # Normalize timestamp to naive for comparison with naive DB datetimes
        if timestamp.tzinfo is not None:
            t_naive = timestamp.replace(tzinfo=None)
        else:
            t_naive = timestamp

        # Extract departure times
        departures = [d for d in [flight.scheduled_departure, flight.actual_departure] if d is not None]
        if departures:
            dep_naive = min(departures)
            if dep_naive.tzinfo is not None:
                dep_naive = dep_naive.replace(tzinfo=None)
            lower_bound = dep_naive - timedelta(minutes=15)
            if t_naive < lower_bound:
                return False

        # For completed/landed/cancelled flights, also enforce upper bound
        if flight.status in ["landed", "cancelled"]:
            arrivals = [a for a in [flight.scheduled_arrival, flight.actual_arrival] if a is not None]
            if arrivals:
                arr_naive = max(arrivals)
                if arr_naive.tzinfo is not None:
                    arr_naive = arr_naive.replace(tzinfo=None)
                upper_bound = arr_naive + timedelta(minutes=15)
                if t_naive > upper_bound:
                    return False

        return True

    async def _update_ha(self, aircraft: Aircraft, position: Position, flight: Optional[Flight]):
        """Update Home Assistant sensor for this aircraft."""
        flight_data = None
        if flight:
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
                "aircraft_type": aircraft.aircraft_type,
                "airline": aircraft.airline,
                "status": flight.status,
            }

        position_data = {
            "latitude": position.latitude,
            "longitude": position.longitude,
            "altitude_ft": position.altitude_ft,
            "ground_speed_kts": position.ground_speed_kts,
            "heading": position.heading,
            "vertical_rate_fpm": position.vertical_rate_fpm,
            "on_ground": position.on_ground,
            "squawk": position.squawk,
            "timestamp": position.timestamp.isoformat() if position.timestamp else None,
        }

        status_str = ha_service.build_status_string(
            on_ground=position.on_ground,
            departure_iata=flight.departure_iata if flight else None,
            arrival_iata=flight.arrival_iata if flight else None,
            departure_name=flight.departure_name if flight else None,
            arrival_name=flight.arrival_name if flight else None,
            scheduled_arrival=flight.scheduled_arrival if flight else None,
            scheduled_departure=flight.scheduled_departure if flight else None,
            flight_status=flight.status if flight else None,
            location_name=position.location_name
        )

        await ha_service.update_aircraft_sensor(
            tail_number=aircraft.tail_number,
            status=status_str,
            flight_data=flight_data,
            position_data=position_data,
        )

    async def _update_ha_no_position(self, aircraft: Aircraft, session: AsyncSession):
        """Update HA when we have no position data (aircraft not broadcasting)."""
        active_flight = await self._get_active_flight(aircraft.id, datetime.now(timezone.utc), session)

        flight_data = None
        if active_flight:
            flight_data = {
                "flight_number": active_flight.flight_number,
                "callsign": active_flight.callsign,
                "departure_iata": active_flight.departure_iata,
                "departure_name": active_flight.departure_name,
                "arrival_iata": active_flight.arrival_iata,
                "arrival_name": active_flight.arrival_name,
                "scheduled_departure": active_flight.scheduled_departure.isoformat() if active_flight.scheduled_departure else None,
                "scheduled_arrival": active_flight.scheduled_arrival.isoformat() if active_flight.scheduled_arrival else None,
                "actual_departure": active_flight.actual_departure.isoformat() if active_flight.actual_departure else None,
                "actual_arrival": active_flight.actual_arrival.isoformat() if active_flight.actual_arrival else None,
                "aircraft_type": aircraft.aircraft_type,
                "airline": aircraft.airline,
                "status": active_flight.status,
            }

            status_str = ha_service.build_status_string(
                on_ground=True,
                departure_iata=active_flight.departure_iata,
                arrival_iata=active_flight.arrival_iata,
                departure_name=active_flight.departure_name,
                arrival_name=active_flight.arrival_name,
                scheduled_arrival=active_flight.scheduled_arrival,
                scheduled_departure=active_flight.scheduled_departure,
                flight_status=active_flight.status,
            )
        else:
            status_str = ha_service.build_status_string(on_ground=True)

        await ha_service.update_aircraft_sensor(
            tail_number=aircraft.tail_number,
            status=status_str,
            flight_data=flight_data,
        )

    async def update_tracker_polling_interval(self, db: AsyncSession = None):
        """
        Dynamically adjusts the OpenSky/FR24 polling interval of the APScheduler job
        based on active flights or manual override.
        """
        if db is None:
            async with async_session() as session:
                await self._update_tracker_polling_interval_impl(session)
        else:
            await self._update_tracker_polling_interval_impl(db)

    async def _update_tracker_polling_interval_impl(self, db: AsyncSession):
        try:
            from app.models import Flight, Setting
            from app.config import settings as app_settings
            
            now = datetime.now(timezone.utc)
            
            # Fetch all settings
            res_settings = await db.execute(select(Setting))
            db_settings = {s.key: s.value for s in res_settings.scalars().all()}
            
            airborne_interval = int(db_settings.get('polling_interval_seconds', app_settings.polling_interval_seconds))
            passive_interval = int(db_settings.get('polling_interval_passive_seconds', 300))
            passive_interval = max(passive_interval, airborne_interval)
            
            manual_airborne = db_settings.get('manual_airborne_mode', 'false') == 'true'
            manual_set_at_str = db_settings.get('manual_airborne_mode_set_at')
            
            # Check for active flights
            result_active = await db.execute(select(Flight).where(Flight.status == "active"))
            has_active_flights = result_active.scalars().first() is not None
            
            # Handle manual airborne mode timeout (30 minutes)
            if manual_airborne and not has_active_flights:
                if manual_set_at_str:
                    try:
                        set_at = datetime.fromisoformat(manual_set_at_str)
                        if set_at.tzinfo is None:
                            set_at = set_at.replace(tzinfo=timezone.utc)
                        else:
                            set_at = set_at.astimezone(timezone.utc)
                        
                        if (now - set_at) >= timedelta(minutes=30):
                            logger.info("30 minutes elapsed in manual airborne mode with no active flights. Reverting to passive mode.")
                            manual_airborne = False
                            
                            # Update setting in DB
                            res_mode = await db.execute(select(Setting).where(Setting.key == 'manual_airborne_mode'))
                            mode_setting = res_mode.scalars().first()
                            if mode_setting:
                                mode_setting.value = 'false'
                            else:
                                db.add(Setting(key='manual_airborne_mode', value='false'))
                            await db.commit()
                    except Exception as parse_err:
                        logger.error(f"Failed to parse manual_airborne_mode_set_at: {parse_err}")
            
            is_airborne_mode = has_active_flights or manual_airborne
            target_interval = airborne_interval if is_airborne_mode else passive_interval
            
            self.is_airborne_mode = is_airborne_mode
            self.current_interval = target_interval
            await self._broadcast_tracker_status()
            
            # Reschedule the job
            from app.main import scheduler
            job = scheduler.get_job("poll_positions")
            if job:
                current_interval = job.trigger.interval.total_seconds()
                if int(current_interval) != target_interval:
                    scheduler.reschedule_job("poll_positions", trigger="interval", seconds=target_interval)
                    logger.info(
                        f"Dynamic Polling Interval Adjustment: Switched to "
                        f"{'Airborne' if is_airborne_mode else 'Passive'} mode. "
                        f"Reason: {'Active Flight' if has_active_flights else 'Manual Override' if manual_airborne else 'All Grounded/Timeout'}. "
                        f"Rescheduled 'poll_positions' from {int(current_interval)}s to {target_interval}s."
                    )
            else:
                logger.warning("Could not find APScheduler job 'poll_positions' to reschedule.")
                
        except Exception as e:
            logger.error(f"Failed to dynamically adjust tracker polling interval: {e}")


# Singleton instance
tracker_service = TrackerService()
