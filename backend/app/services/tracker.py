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
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Aircraft, Flight, Position
from app.services.opensky import opensky_client
from app.services.flightradar import fr24_client
from app.services.flightaware import fa_client
from app.services.geocoder import geocoder
from app.services.home_assistant import ha_service
from app.services.websocket import ws_manager

logger = logging.getLogger(__name__)


class TrackerService:
    """Main tracking orchestrator."""

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

                    # Create position record
                    new_pos = Position(
                        aircraft_id=aircraft.id,
                        flight_id=flight.id if flight else None,
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

        # Update flight status if airborne
        if flight and not sv.on_ground and flight.status == "scheduled":
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
            flight_id=flight.id if flight else None,
            latitude=sv.latitude,
            longitude=sv.longitude,
            altitude_ft=sv.baro_altitude_m * 3.28084 if sv.baro_altitude_m is not None else None,
            ground_speed_kts=sv.velocity_mps * 1.94384 if sv.velocity_mps is not None else None,
            heading=sv.true_track,
            vertical_rate_fpm=sv.vertical_rate_mps * 196.85 if sv.vertical_rate_mps is not None else None,
            on_ground=sv.on_ground,
            squawk=sv.squawk,
            timestamp=datetime.fromtimestamp(sv.time_position or sv.last_contact, timezone.utc),
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
                                longitude=pos_data["latitude"],  # Wait, wait...
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
                            # Fix longitude
                            sv.longitude = pos_data["longitude"]
                            state_vectors.append(sv)
                    except Exception as e:
                        logger.error(f"FR24 fallback failed for {ac.tail_number}: {e}")

                if not state_vectors:
                    logger.debug("No positions returned from OpenSky or FR24")
                    # Still update HA for aircraft we're tracking but have no data for
                    for aircraft in aircraft_list:
                        await self._update_ha_no_position(aircraft, session)
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
                        session
                    )

                    # Store position
                    position = Position(
                        aircraft_id=aircraft.id,
                        flight_id=active_flight.id if active_flight else None,
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
                        await self._update_flight_status(active_flight, sv, session)

                    # Broadcast via WebSocket
                    await self._broadcast_position(aircraft, position, active_flight)

                    # Update Home Assistant
                    await self._update_ha(aircraft, position, active_flight)

                await session.commit()
                logger.info(f"Stored {len(state_vectors)} positions")

        except Exception as e:
            logger.error(f"Tracker poll failed: {e}", exc_info=True)

    async def _get_or_create_active_flight(
        self,
        aircraft: Aircraft,
        is_on_ground: bool,
        callsign: Optional[str],
        timestamp: datetime,
        session: AsyncSession
    ) -> Optional[Flight]:
        """
        Retrieves the active/scheduled flight. If none exists and the aircraft
        is airborne, dynamically creates a new active flight and runs position reconciliation.
        """
        flight = await self._get_active_flight(aircraft.id, session)
        if flight:
            return flight

        if not is_on_ground:
            flight_num = callsign.strip() if callsign else aircraft.tail_number
            logger.info(f"Airborne telemetry detected for {aircraft.tail_number} with no active flight. Auto-creating active flight {flight_num}.")
            
            # Ensure naive datetime for DB consistency
            ts_naive = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

            new_flight = Flight(
                aircraft_id=aircraft.id,
                flight_number=flight_num,
                callsign=flight_num,
                status="active",
                actual_departure=ts_naive
            )
            session.add(new_flight)
            await session.flush() # Populate the ID
            
            try:
                from app.services.reconciliation import reconciliation_service
                await reconciliation_service.reconcile_orphan_positions(new_flight, session)
            except Exception as e:
                logger.error(f"Failed to reconcile orphan positions for auto-created flight: {e}")
                
            return new_flight

        return None

    async def _get_active_flight(self, aircraft_id, session: AsyncSession) -> Optional[Flight]:
        """Get the currently active or most recent scheduled flight for an aircraft."""
        result = await session.execute(
            select(Flight).where(
                Flight.aircraft_id == aircraft_id,
                Flight.status.in_(["scheduled", "active"]),
            ).order_by(Flight.scheduled_departure.desc().nullslast())
        )
        return result.scalars().first()

    async def _update_flight_status(self, flight: Flight, sv, session: AsyncSession):
        """Update flight status based on position data."""
        old_status = flight.status

        if sv.on_ground:
            if old_status == "active":
                # Was flying, now on ground = landed
                flight.status = "landed"
                flight.actual_arrival = datetime.now(timezone.utc)
                logger.info(f"Flight {flight.flight_number} has landed")

                # Broadcast status change
                await ws_manager.broadcast({
                    "type": "flight_status",
                    "flight_id": str(flight.id),
                    "aircraft_id": str(flight.aircraft_id),
                    "old_status": old_status,
                    "new_status": "landed",
                })
        else:
            if old_status == "scheduled":
                # Was scheduled, now in the air = active
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
        active_flight = await self._get_active_flight(aircraft.id, session)

        flight_data = None
        if active_flight:
            flight_data = {
                "flight_number": active_flight.flight_number,
                "arrival_iata": active_flight.arrival_iata,
                "departure_iata": active_flight.departure_iata,
                "scheduled_arrival": active_flight.scheduled_arrival.isoformat() if active_flight.scheduled_arrival else None,
                "status": active_flight.status,
            }

            status_str = ha_service.build_status_string(
                on_ground=True,
                departure_iata=active_flight.departure_iata,
                arrival_iata=active_flight.arrival_iata,
                scheduled_arrival=active_flight.scheduled_arrival,
                flight_status=active_flight.status,
            )
        else:
            status_str = "ground - unknown"

        await ha_service.update_aircraft_sensor(
            tail_number=aircraft.tail_number,
            status=status_str,
            flight_data=flight_data,
        )


# Singleton instance
tracker_service = TrackerService()
