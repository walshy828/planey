import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Flight, Aircraft
from app.services.flightradar import fr24_client
from app.services.home_assistant import ha_service
from app.services.geocoder import geocoder

logger = logging.getLogger(__name__)

class ReconciliationService:
    """Service to automatically close out 'stuck' flights."""
    
    async def reconcile_orphan_positions(self, flight: Flight, db: AsyncSession) -> int:
        """
        Retroactively link orphan positions (flight_id is NULL) to the flight
        by finding all positions for this aircraft within the flight's timeframe
        that are not overlapped by any other flight for the same aircraft.
        """
        from app.models import Position
        from sqlalchemy import update

        # 1. Determine baseline timeframe of this flight
        def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        flight_start = ensure_utc(flight.actual_departure or flight.scheduled_departure or flight.created_at)
        flight_end = ensure_utc(flight.actual_arrival or flight.scheduled_arrival or datetime.now(timezone.utc))

        # 2. Query other flights for the same aircraft to find boundaries
        flights_res = await db.execute(
            select(Flight)
            .where(Flight.aircraft_id == flight.aircraft_id)
            .order_by(Flight.scheduled_departure.asc().nullslast(), Flight.created_at.asc())
        )
        all_flights = flights_res.scalars().all()

        prev_end = None
        next_start = None

        # Find our index
        flight_idx = -1
        for idx, f in enumerate(all_flights):
            if f.id == flight.id:
                flight_idx = idx
                break

        if flight_idx != -1:
            # Preceding flight (closest index before us)
            if flight_idx > 0:
                p_flight = all_flights[flight_idx - 1]
                p_end = p_flight.actual_arrival or p_flight.scheduled_arrival or p_flight.actual_departure or p_flight.scheduled_departure
                if p_end:
                    prev_end = ensure_utc(p_end)

            # Succeeding flight (closest index after us)
            if flight_idx < len(all_flights) - 1:
                n_flight = all_flights[flight_idx + 1]
                n_start = n_flight.actual_departure or n_flight.scheduled_departure or n_flight.created_at
                if n_start:
                    next_start = ensure_utc(n_start)

        # 3. Calculate start/end limits with buffers
        # Preceding flight limit
        if prev_end:
            start_limit = max(prev_end, flight_start - timedelta(hours=2))
        else:
            start_limit = flight_start - timedelta(hours=12)

        # Succeeding flight limit
        if next_start:
            end_limit = min(next_start, flight_end + timedelta(hours=2))
        else:
            end_limit = flight_end + timedelta(hours=12)

        logger.info(
            f"Reconciling positions for flight {flight.id} ({flight.flight_number or flight.callsign}). "
            f"Time window limits: {start_limit} to {end_limit}"
        )

        # 4. Perform update
        q = (
            update(Position)
            .where(
                Position.aircraft_id == flight.aircraft_id,
                Position.flight_id.is_(None),
                Position.timestamp >= start_limit,
                Position.timestamp <= end_limit
            )
            .values(flight_id=flight.id)
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(q)
        rowcount = result.rowcount
        logger.info(f"Successfully retroactively linked {rowcount} orphan positions to flight {flight.id}")
        await db.commit()
        return rowcount

    
    async def reconcile_flight(self, flight_id: str, db: AsyncSession) -> dict:
        """
        Manually reconcile a single flight.
        Finds the flight's actual completed details via FR24.
        """
        # 1. Fetch flight
        result = await db.execute(
            select(Flight)
            .where(Flight.id == flight_id)
        )
        flight = result.scalars().first()
        
        if not flight:
            raise ValueError(f"Flight {flight_id} not found")
            
        if flight.status not in ["active", "scheduled"]:
            return {"status": "skipped", "message": f"Flight {flight.id} is already {flight.status}"}

        # 2. Fetch Aircraft
        result_ac = await db.execute(
            select(Aircraft).where(Aircraft.id == flight.aircraft_id)
        )
        aircraft = result_ac.scalars().first()

        if not aircraft:
            raise ValueError(f"Aircraft for flight {flight_id} not found")

        # 3. Lookup Flight History
        logger.info(f"Reconciling flight {flight.flight_number or flight.callsign} for {aircraft.tail_number}")
        
        # We will use the lookup_flight method which handles both FR24 and FA fallbacks
        fa_data = await fr24_client.lookup_flight(
            flight_number=flight.flight_number, 
            registration=aircraft.tail_number, 
            callsign=flight.callsign
        )
        
        # If FR24 failed or thinks it's still active, try the FlightAware direct JSON extraction fallback
        if not fa_data or fa_data.get("status") not in ["landed", "arrived", "arrived / delayed", "cancelled"]:
            logger.info("FR24 did not resolve flight to landed state. Falling back to direct FlightAware extraction.")
            
            import httpx
            import json
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            
            try:
                async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0) as client:
                    resp = await client.get(f"https://www.flightaware.com/live/flight/{aircraft.tail_number}")
                    start_str = "var trackpollBootstrap = "
                    if start_str in resp.text:
                        idx = resp.text.find(start_str) + len(start_str)
                        open_braces = 0
                        json_str = ""
                        for i, char in enumerate(resp.text[idx:]):
                            if char == "{":
                                open_braces += 1
                            elif char == "}":
                                open_braces -= 1
                                if open_braces == 0:
                                    json_str = resp.text[idx:idx+i+1]
                                    break
                        
                        if json_str:
                            data = json.loads(json_str)
                            flights_map = data.get("flights", {})
                            
                            # Find the first 'arrived' or 'landed' flight in the activity log
                            found_fa_flight = None
                            for k, v in flights_map.items():
                                act_flights = v.get("activityLog", {}).get("flights", [])
                                for f in act_flights:
                                    status = f.get("flightStatus", "").lower()
                                    if status in ["arrived", "landed"]:
                                        found_fa_flight = f
                                        break
                                if found_fa_flight: break
                                
                            if found_fa_flight:
                                logger.info(f"Found match in FA JSON with status {found_fa_flight.get('flightStatus')}")
                                if not fa_data: fa_data = {}
                                fa_data["status"] = "landed"
                                dest = found_fa_flight.get("destination", {})
                                fa_data["arrival_iata"] = dest.get("iata") or dest.get("friendlyName")
                                fa_data["arrival_name"] = dest.get("friendlyLocation") or fa_data.get("arrival_name")
                                
                                arr_time = found_fa_flight.get("landingTimes", {}).get("actual") or found_fa_flight.get("landingTimes", {}).get("estimated")
                                if arr_time:
                                    fa_data["actual_arrival"] = datetime.fromtimestamp(arr_time, tz=timezone.utc)
                                    
                                dep_time = found_fa_flight.get("takeoffTimes", {}).get("actual") or found_fa_flight.get("takeoffTimes", {}).get("estimated")
                                if dep_time:
                                    fa_data["actual_departure"] = datetime.fromtimestamp(dep_time, tz=timezone.utc)
                                    
                                # Extract coordinates to create a grounded position
                                coord = dest.get("coord")
                                if coord and len(coord) == 2:
                                    fa_data["dest_lon"] = coord[0]
                                    fa_data["dest_lat"] = coord[1]
            except Exception as e:
                logger.error(f"FlightAware JSON extraction failed: {e}")

        if not fa_data:
            return {"status": "failed", "message": "Could not find historical data in FR24/FA"}
            
        # 4. Update the Flight
        updated = False
        
        if fa_data.get("status") in ["landed", "arrived", "arrived / delayed"]:
            flight.status = "landed"
            
            # Update arrival info if it changed
            if fa_data.get("arrival_iata") and not flight.arrival_iata:
                flight.arrival_iata = fa_data["arrival_iata"]
            if fa_data.get("arrival_name") and not flight.arrival_name:
                flight.arrival_name = fa_data["arrival_name"]
                
            # Update times
            if fa_data.get("scheduled_arrival"):
                flight.scheduled_arrival = self._ensure_tz(fa_data["scheduled_arrival"])
            if fa_data.get("actual_arrival"):
                flight.actual_arrival = self._ensure_tz(fa_data["actual_arrival"])
            elif fa_data.get("scheduled_arrival"):
                flight.actual_arrival = self._ensure_tz(fa_data["scheduled_arrival"])
                
            if fa_data.get("actual_departure"):
                flight.actual_departure = self._ensure_tz(fa_data["actual_departure"])
                
            updated = True
            
            # Create a grounded position so the UI doesn't show it stuck mid-air
            dest_lat = fa_data.get("dest_lat")
            dest_lon = fa_data.get("dest_lon")
            
            # Fallback 1: Resolve destination airport coordinates using its IATA code
            arrival_iata = fa_data.get("arrival_iata") or flight.arrival_iata
            if (not dest_lat or not dest_lon) and arrival_iata:
                try:
                    airport_info = fr24_client.get_airport_info(arrival_iata)
                    if airport_info and airport_info.get("latitude") and airport_info.get("longitude"):
                        dest_lat = airport_info["latitude"]
                        dest_lon = airport_info["longitude"]
                        logger.info(f"Resolved arrival airport {arrival_iata} coordinates: {dest_lat}, {dest_lon}")
                except Exception as ex:
                    logger.warning(f"Failed to lookup airport coordinates for {arrival_iata}: {ex}")
            
            # Fallback 2: Fallback to aircraft's latest position coordinates in the database
            if not dest_lat or not dest_lon:
                from app.models import Position
                pos_result = await db.execute(
                    select(Position)
                    .where(Position.aircraft_id == aircraft.id)
                    .order_by(Position.timestamp.desc())
                    .limit(1)
                )
                latest_pos = pos_result.scalars().first()
                if latest_pos:
                    dest_lat = latest_pos.latitude
                    dest_lon = latest_pos.longitude
                    logger.info(f"Fallback to aircraft last known coordinates for {aircraft.tail_number}: {dest_lat}, {dest_lon}")
            
            if dest_lat and dest_lon:
                from app.models import Position
                new_pos = Position(
                    aircraft_id=aircraft.id,
                    flight_id=flight.id,
                    latitude=dest_lat,
                    longitude=dest_lon,
                    altitude_ft=0,
                    ground_speed_kts=0,
                    heading=0,
                    vertical_rate_fpm=0,
                    on_ground=True,
                    source="reconciliation",
                    timestamp=flight.actual_arrival or datetime.now(timezone.utc),
                    location_name=flight.arrival_name or flight.arrival_iata
                )
                db.add(new_pos)
            
        elif fa_data.get("status", "").lower() == "cancelled":
            flight.status = "cancelled"
            updated = True
        elif fa_data.get("status", "").lower() in ["active", "en route", "scheduled"]:
            # Maybe the external source still thinks it's active. 
            # We won't forcefully close it if it's genuinely active.
            return {"status": "skipped", "message": f"External source says status is {fa_data.get('status')}"}

        if updated:
            # 6. Notify Home Assistant
            await self._notify_ha(aircraft, flight)
            
            # Proactively calculate flight summary statistics upon landing
            if flight.status == "landed":
                try:
                    from app.services.stats_calculator import calculate_flight_stats
                    flight.summary_stats = await calculate_flight_stats(flight, db)
                    logger.info(f"Calculated flight statistics during reconciliation: {flight.summary_stats}")
                except Exception as e:
                    logger.error(f"Failed to calculate stats during reconciliation: {e}")
            
            await db.commit()
            return {"status": "success", "message": "Flight reconciled and closed"}
            
        return {"status": "skipped", "message": "No actionable updates found"}

    def _ensure_tz(self, dt):
        """Ensure a datetime object is timezone-aware (UTC)."""
        if isinstance(dt, str):
            try:
                from dateutil import parser
                dt = parser.parse(dt)
            except:
                return None
        if not dt:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def _notify_ha(self, aircraft: Aircraft, flight: Flight):
        """Send a 'Landed' state to Home Assistant."""
        # Get location string
        loc = flight.arrival_iata or flight.departure_iata or "Unknown Airport"
        status_str = ha_service.build_status_string(
            on_ground=True, # force on ground
            departure_iata=flight.departure_iata,
            arrival_iata=flight.arrival_iata,
            scheduled_arrival=flight.scheduled_arrival.isoformat() if flight.scheduled_arrival else None,
            scheduled_departure=flight.scheduled_departure.isoformat() if flight.scheduled_departure else None,
            flight_status=flight.status,
            location_name=None
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
            "aircraft_type": aircraft.aircraft_type,
            "airline": aircraft.airline,
            "status": flight.status,
        }
        
        await ha_service.update_aircraft_sensor(
            tail_number=aircraft.tail_number,
            status=status_str,
            flight_data=flight_data,
            position_data={"on_ground": True}
        )

    async def run_reconciliation_job(self):
        """Background job to reconcile stuck flights."""
        from app.database import async_session
        from app.models import Setting
        
        async with async_session() as session:
            # 1. Get Settings
            res = await session.execute(select(Setting).where(Setting.key == 'reconciliation_interval_minutes'))
            setting = res.scalars().first()
            interval_mins = int(setting.value) if setting and setting.value.isdigit() else 60
            
            res_thresh = await session.execute(select(Setting).where(Setting.key == 'stuck_flight_threshold_minutes'))
            thresh_setting = res_thresh.scalars().first()
            threshold_mins = int(thresh_setting.value) if thresh_setting and thresh_setting.value.isdigit() else 120
            
            # 2. Check if we should run (we can track last run time in settings)
            res_last = await session.execute(select(Setting).where(Setting.key == 'last_reconciliation_run'))
            last_run_setting = res_last.scalars().first()
            
            now = datetime.now(timezone.utc)
            if last_run_setting:
                try:
                    last_run = datetime.fromisoformat(last_run_setting.value)
                    if (now - last_run).total_seconds() < (interval_mins * 60):
                        return # Not time yet
                except: pass
                
            logger.info("Running stuck flight reconciliation job...")
            
            # 3. Find stuck flights
            cutoff_time = now - timedelta(minutes=threshold_mins)
            
            # Flights that are active but their aircraft hasn't had a position since cutoff
            # Simple approach: find all active flights, then check latest position
            result = await session.execute(
                select(Flight).where(Flight.status == "active")
            )
            active_flights = result.scalars().all()
            
            for f in active_flights:
                try:
                    # Look for recent positions
                    # This is slightly inefficient but safe
                    res_pos = await session.execute(
                        select(Aircraft).where(Aircraft.id == f.aircraft_id)
                    )
                    ac = res_pos.scalars().first()
                    
                    if ac:
                        # Check last position time (simplest is checking aircraft's last update or pulling position)
                        from app.models import Position
                        pos_res = await session.execute(
                            select(Position).where(Position.aircraft_id == ac.id).order_by(Position.timestamp.desc()).limit(1)
                        )
                        last_pos = pos_res.scalars().first()
                        
                        if not last_pos or last_pos.timestamp < cutoff_time:
                            logger.info(f"Flight {f.flight_number} is stuck (last pos: {last_pos.timestamp if last_pos else 'never'}). Attempting reconciliation.")
                            await self.reconcile_flight(str(f.id), session)
                            await asyncio.sleep(2) # rate limit
                except Exception as e:
                    logger.error(f"Error checking stuck flight {f.id}: {e}")
            
            # 4. Update last run
            if last_run_setting:
                last_run_setting.value = now.isoformat()
            else:
                session.add(Setting(key='last_reconciliation_run', value=now.isoformat()))
            await session.commit()

    async def reconcile_aircraft(self, aircraft_id: str, db: AsyncSession) -> dict:
        """
        Manually reconcile an aircraft's position and grounded status.
        Looks up the aircraft's latest flight/status externally.
        If the external source indicates it is on the ground (or no live flight),
        creates a grounded position and resets metrics.
        """
        # Fetch aircraft
        result_ac = await db.execute(
            select(Aircraft).where(Aircraft.id == aircraft_id)
        )
        aircraft = result_ac.scalars().first()
        if not aircraft:
            raise ValueError(f"Aircraft {aircraft_id} not found")
            
        # Get latest position
        from app.models import Position
        pos_result = await db.execute(
            select(Position)
            .where(Position.aircraft_id == aircraft.id)
            .order_by(Position.timestamp.desc())
            .limit(1)
        )
        latest_pos = pos_result.scalars().first()
        
        if not latest_pos:
            return {"status": "skipped", "message": "No position history to reconcile"}
            
        if latest_pos.on_ground:
            return {"status": "skipped", "message": "Aircraft is already on ground"}
            
        logger.info(f"Reconciling stuck airborne aircraft {aircraft.tail_number}")
        
        # Lookup latest flight info
        fa_data = await fr24_client.lookup_flight(registration=aircraft.tail_number)
        
        # If external source says on the ground, or we have no live flight info (meaning it landed)
        is_grounded = True
        dest_lat = None
        dest_lon = None
        arrival_name = None
        arrival_iata = None
        
        if fa_data:
            ext_status = fa_data.get("status", "").lower()
            # If external source explicitly says it's active or live, we don't ground it
            if ext_status in ["active", "en route", "live"]:
                is_grounded = False
                logger.info(f"Aircraft {aircraft.tail_number} is actively flying according to external source")
            else:
                arrival_iata = fa_data.get("arrival_iata")
                arrival_name = fa_data.get("arrival_name")
                dest_lat = fa_data.get("dest_lat")
                dest_lon = fa_data.get("dest_lon")
        
        if is_grounded:
            from app.models import Flight
            import uuid
            
            flight_id = None
            flight_obj = None
            
            # Find active/scheduled flight
            flight_result = await db.execute(
                select(Flight)
                .where(
                    Flight.aircraft_id == aircraft.id,
                    Flight.status.in_(["scheduled", "active"])
                )
                .order_by(Flight.created_at.desc())
                .limit(1)
            )
            active_flight = flight_result.scalars().first()
            
            if active_flight:
                flight_obj = active_flight
                flight_id = active_flight.id
                # Land the flight
                flight_obj.status = "landed"
                flight_obj.actual_arrival = datetime.now(timezone.utc)
                if arrival_iata:
                    flight_obj.arrival_iata = arrival_iata
                if arrival_name:
                    flight_obj.arrival_name = arrival_name
            elif latest_pos and latest_pos.flight_id:
                flight_id = latest_pos.flight_id
            
            if not flight_id:
                # Create reconciliation flight
                flight_obj = Flight(
                    id=uuid.uuid4(),
                    aircraft_id=aircraft.id,
                    flight_number="RECON",
                    status="landed",
                    departure_iata="???",
                    arrival_iata=arrival_iata or "???",
                    scheduled_departure=datetime.now(timezone.utc) - timedelta(hours=1),
                    actual_departure=datetime.now(timezone.utc) - timedelta(hours=1),
                    scheduled_arrival=datetime.now(timezone.utc),
                    actual_arrival=datetime.now(timezone.utc),
                )
                db.add(flight_obj)
                flight_id = flight_obj.id

            # Resolve coordinates of destination airport if missing
            if (not dest_lat or not dest_lon) and arrival_iata:
                try:
                    airport_info = fr24_client.get_airport_info(arrival_iata)
                    if airport_info and airport_info.get("latitude") and airport_info.get("longitude"):
                        dest_lat = airport_info["latitude"]
                        dest_lon = airport_info["longitude"]
                        logger.info(f"Resolved destination airport {arrival_iata} coordinates: {dest_lat}, {dest_lon}")
                except Exception as ex:
                    logger.warning(f"Failed to lookup airport coordinates: {ex}")
            
            # Fallback to last known position coordinates
            if not dest_lat or not dest_lon:
                dest_lat = latest_pos.latitude
                dest_lon = latest_pos.longitude
                logger.info(f"Fallback to aircraft last known coordinates for {aircraft.tail_number}: {dest_lat}, {dest_lon}")
                
            # Create a grounded position
            new_pos = Position(
                aircraft_id=aircraft.id,
                flight_id=flight_id,
                latitude=dest_lat,
                longitude=dest_lon,
                altitude_ft=0,
                ground_speed_kts=0,
                heading=0,
                vertical_rate_fpm=0,
                on_ground=True,
                source="reconciliation",
                timestamp=datetime.now(timezone.utc),
                location_name=arrival_name or arrival_iata or latest_pos.location_name
            )
            db.add(new_pos)
            
            # Update Home Assistant
            await ha_service.update_aircraft_sensor(
                tail_number=aircraft.tail_number,
                status="Landed",
                flight_data=None,
                position_data={"on_ground": True}
            )
            
            await db.commit()
            return {"status": "success", "message": f"Aircraft grounded at {arrival_iata or 'last known position'}"}
            
        return {"status": "skipped", "message": "External source says aircraft is still active"}

    async def reconcile_all_active_flights(self, db: AsyncSession) -> dict:
        """Manually reconcile all active and scheduled flights on demand, plus stuck airborne aircraft."""
        # Find all active or scheduled flights
        result = await db.execute(
            select(Flight).where(Flight.status.in_(["scheduled", "active"]))
        )
        open_flights = result.scalars().all()
        
        logger.info(f"Manual reconciliation triggered for {len(open_flights)} open flights")
        
        results = []
        for flight in open_flights:
            try:
                res = await self.reconcile_flight(str(flight.id), db)
                results.append({
                    "flight_id": str(flight.id),
                    "flight_number": flight.flight_number,
                    "callsign": flight.callsign,
                    "status": res.get("status"),
                    "message": res.get("message")
                })
                # Add a tiny sleep to be friendly to APIs
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to manually reconcile flight {flight.id}: {e}")
                results.append({
                    "flight_id": str(flight.id),
                    "flight_number": flight.flight_number,
                    "callsign": flight.callsign,
                    "status": "failed",
                    "message": str(e)
                })

        # Also find all aircraft currently marked as airborne in their latest position
        # but with no recent position updates (e.g. older than 15 minutes)
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        
        # Let's fetch all active aircraft
        ac_result = await db.execute(
            select(Aircraft).where(Aircraft.active == True)
        )
        all_aircraft = ac_result.scalars().all()
        
        stuck_aircraft = []
        from app.models import Position
        for ac in all_aircraft:
            # Check if aircraft already has an open flight checked above to avoid double-checking
            if any(r.get("flight_id") and any(f.aircraft_id == ac.id for f in open_flights) for r in results):
                continue
                
            pos_result = await db.execute(
                select(Position)
                .where(Position.aircraft_id == ac.id)
                .order_by(Position.timestamp.desc())
                .limit(1)
            )
            latest_pos = pos_result.scalars().first()
            
            if latest_pos and not latest_pos.on_ground and latest_pos.timestamp < cutoff_time:
                stuck_aircraft.append(ac)

        logger.info(f"Manual reconciliation identified {len(stuck_aircraft)} stuck airborne aircraft")
        
        for ac in stuck_aircraft:
            try:
                res = await self.reconcile_aircraft(str(ac.id), db)
                results.append({
                    "aircraft_id": str(ac.id),
                    "tail_number": ac.tail_number,
                    "status": res.get("status"),
                    "message": res.get("message")
                })
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to manually reconcile stuck aircraft {ac.tail_number}: {e}")
                results.append({
                    "aircraft_id": str(ac.id),
                    "tail_number": ac.tail_number,
                    "status": "failed",
                    "message": str(e)
                })

        return {
            "total_checked": len(open_flights),
            "total_aircraft_checked": len(stuck_aircraft),
            "results": results
        }


# Singleton
reconciliation_service = ReconciliationService()

