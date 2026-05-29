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

        # Never link positions to a scheduled flight that hasn't actually departed yet.
        # The 12-hour backward window would otherwise sweep up current ground positions.
        # Also guard against stale actual_departure (> 6 h before scheduled departure).
        now = datetime.now(timezone.utc)
        if flight.status == "scheduled" and flight.scheduled_departure:
            sched = ensure_utc(flight.scheduled_departure)
            if sched and sched > now:
                actual_dep = ensure_utc(flight.actual_departure) if flight.actual_departure else None
                if actual_dep is None or actual_dep < sched - timedelta(hours=6):
                    logger.info(
                        f"Skipping orphan position linkage for future scheduled flight "
                        f"{flight.id} ({flight.flight_number}) — not yet departed"
                    )
                    return 0

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

        # Never attempt external reconciliation of a scheduled flight whose departure
        # time is still in the future — it hasn't happened yet.
        # Guard also catches cases where actual_departure is set but is stale historical
        # data (> 6 h before the scheduled departure), which indicates bad data, not a
        # real departure.
        if flight.status == "scheduled" and flight.scheduled_departure:
            sched_dep = self._ensure_tz(flight.scheduled_departure)
            if sched_dep and sched_dep > datetime.now(timezone.utc):
                actual_dep = self._ensure_tz(flight.actual_departure)
                if actual_dep is None or actual_dep < sched_dep - timedelta(hours=6):
                    return {"status": "skipped", "message": "Future scheduled flight has not departed; skipping reconciliation"}

        # Recency guard: never close a flight that had airborne positions in the last 10 minutes.
        # This prevents premature closure during brief ADS-B coverage gaps, which would cause
        # the tracker to split one physical flight leg into multiple flight records.
        from app.models import Position as _Pos
        _recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        _recent_res = await db.execute(
            select(_Pos)
            .where(
                _Pos.flight_id == flight.id,
                _Pos.on_ground == False,
                _Pos.source != 'reconciliation',
                _Pos.timestamp >= _recent_cutoff,
            )
            .limit(1)
        )
        if _recent_res.scalars().first():
            logger.info(
                f"Skipping reconciliation for flight {flight.id} ({flight.flight_number}): "
                f"has airborne positions within the last 20 min"
            )
            return {"status": "skipped", "message": "Flight has recent airborne positions; skipping to avoid premature closure"}

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

                            # Find the best-matching 'arrived' or 'landed' flight in the
                            # activity log.  Match by departure time (±6 h) so we don't
                            # accidentally pick up a historical leg from a different day.
                            target_dep = flight.scheduled_departure or flight.actual_departure
                            if target_dep and target_dep.tzinfo is None:
                                target_dep = target_dep.replace(tzinfo=timezone.utc)

                            found_fa_flight = None
                            for k, v in flights_map.items():
                                act_flights = v.get("activityLog", {}).get("flights", [])
                                for f in act_flights:
                                    status = f.get("flightStatus", "").lower()
                                    if status not in ["arrived", "landed"]:
                                        continue
                                    # Time-gate: takeoff time must be within 6 h of our
                                    # scheduled departure to avoid matching historical legs.
                                    if target_dep:
                                        entry_dep_ts = (
                                            f.get("takeoffTimes", {}).get("scheduled")
                                            or f.get("takeoffTimes", {}).get("estimated")
                                            or f.get("takeoffTimes", {}).get("actual")
                                        )
                                        if entry_dep_ts:
                                            entry_dep = datetime.fromtimestamp(entry_dep_ts, tz=timezone.utc)
                                            if abs((entry_dep - target_dep).total_seconds()) > 21600:
                                                continue  # different flight leg — skip
                                    found_fa_flight = f
                                    break
                                if found_fa_flight:
                                    break
                                
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
            
        # 4. Update the Flight — record all changes for audit trail
        from app.models import record_flight_changes
        updated = False
        
        if fa_data.get("status") in ["landed", "arrived", "arrived / delayed"]:
            # Build updates dict for history tracking
            recon_updates = {"status": "landed"}
            if fa_data.get("arrival_iata") and not flight.arrival_iata:
                recon_updates["arrival_iata"] = fa_data["arrival_iata"]
            if fa_data.get("arrival_name") and not flight.arrival_name:
                recon_updates["arrival_name"] = fa_data["arrival_name"]
            if fa_data.get("scheduled_arrival"):
                recon_updates["scheduled_arrival"] = self._ensure_tz(fa_data["scheduled_arrival"])
            if fa_data.get("actual_arrival"):
                recon_updates["actual_arrival"] = self._ensure_tz(fa_data["actual_arrival"])
            elif fa_data.get("scheduled_arrival"):
                recon_updates["actual_arrival"] = self._ensure_tz(fa_data["scheduled_arrival"])
            if fa_data.get("actual_departure"):
                recon_updates["actual_departure"] = self._ensure_tz(fa_data["actual_departure"])

            await record_flight_changes(flight, recon_updates, "reconciliation", db)

            # Apply updates
            flight.status = "landed"
            if recon_updates.get("arrival_iata"):
                flight.arrival_iata = recon_updates["arrival_iata"]
            if recon_updates.get("arrival_name"):
                flight.arrival_name = recon_updates["arrival_name"]
            if recon_updates.get("scheduled_arrival"):
                flight.scheduled_arrival = recon_updates["scheduled_arrival"]
            if recon_updates.get("actual_arrival"):
                flight.actual_arrival = recon_updates["actual_arrival"]
            if recon_updates.get("actual_departure"):
                flight.actual_departure = recon_updates["actual_departure"]
                
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
            departure_name=flight.departure_name,
            arrival_name=flight.arrival_name,
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

    async def _close_flight_direct(
        self,
        flight: Flight,
        last_pos,
        aircraft: Optional[Aircraft],
        session: AsyncSession,
    ) -> None:
        """
        Close a flight directly using its last known on-ground position.
        Used when we're confident the aircraft has landed (last position was
        on-ground and old enough) without needing an external API call.
        """
        from app.models import Position, record_flight_changes
        from app.services.websocket import ws_manager

        landing_time = last_pos.timestamp
        if landing_time.tzinfo is None:
            landing_time = landing_time.replace(tzinfo=timezone.utc)

        updates: dict = {"status": "landed", "actual_arrival": str(landing_time)}

        if not flight.arrival_iata and not flight.arrival_name:
            try:
                arrival_name = await geocoder.get_location_name(last_pos.latitude, last_pos.longitude)
                if arrival_name:
                    updates["arrival_name"] = arrival_name
            except Exception:
                pass

        await record_flight_changes(flight, updates, "reconciliation", session)

        flight.status = "landed"
        flight.actual_arrival = landing_time
        if updates.get("arrival_name"):
            flight.arrival_name = updates["arrival_name"]
            flight.arrival_lat = last_pos.latitude
            flight.arrival_lon = last_pos.longitude

        try:
            from app.services.stats_calculator import calculate_flight_stats
            flight.summary_stats = await calculate_flight_stats(flight, session)
        except Exception as e:
            logger.warning(f"Stats calculation failed for direct close of {flight.id}: {e}")

        if aircraft:
            await self._notify_ha(aircraft, flight)

        await session.commit()

        await ws_manager.broadcast({
            "type": "flight_status",
            "flight_id": str(flight.id),
            "aircraft_id": str(flight.aircraft_id),
            "old_status": "active",
            "new_status": "landed",
            "summary_stats": flight.summary_stats,
        })

        logger.info(
            f"Direct close: flight {flight.flight_number or flight.id} "
            f"({aircraft.tail_number if aircraft else '?'}) landed at {landing_time.isoformat()}"
        )

    async def run_reconciliation_job(self):
        """
        Background job to detect and close stuck active flights. Runs every 5 minutes.

        Two closure paths per flight, chosen by last-position state:

        1. Last position was on-ground (tracker state lost after restart / brief coverage gap):
           Close directly — no external API call.
           Thresholds: helicopter ≥ 8 min on-ground, plane ≥ 15 min on-ground.

        2. Last position was airborne (or no positions at all):
           Call reconcile_flight() which checks FR24/FA.
           Thresholds: helicopter ≥ 15 min stale, plane ≥ 30 min stale.

        The old 60-min internal throttle has been removed. APScheduler's max_instances=1
        already prevents concurrent runs; the per-flight 10-min recency guard inside
        reconcile_flight() prevents premature closure.
        """
        from app.database import async_session
        from app.models import Setting, Position

        # Minutes of on-ground staleness before direct close (no external API)
        HELI_GROUND_CLOSE_MINS = 8
        PLANE_GROUND_CLOSE_MINS = 15

        # Minutes of no-position staleness before external reconciliation
        HELI_STALE_MINS = 15
        PLANE_STALE_MINS = 30

        now = datetime.now(timezone.utc)

        async with async_session() as session:
            result = await session.execute(select(Flight).where(Flight.status == "active"))
            active_flights = result.scalars().all()

            if not active_flights:
                return

            logger.info(f"Reconciliation job: checking {len(active_flights)} active flight(s)")

            for flight in active_flights:
                try:
                    ac_res = await session.execute(
                        select(Aircraft).where(Aircraft.id == flight.aircraft_id)
                    )
                    ac = ac_res.scalars().first()
                    is_heli = ac and ac.category == "helicopter"

                    ground_close_mins = HELI_GROUND_CLOSE_MINS if is_heli else PLANE_GROUND_CLOSE_MINS
                    stale_mins = HELI_STALE_MINS if is_heli else PLANE_STALE_MINS

                    # Last position for this specific flight
                    pos_res = await session.execute(
                        select(Position)
                        .where(Position.flight_id == flight.id)
                        .order_by(Position.timestamp.desc())
                        .limit(1)
                    )
                    last_pos = pos_res.scalars().first()

                    if last_pos:
                        last_ts = last_pos.timestamp
                        if last_ts.tzinfo is None:
                            last_ts = last_ts.replace(tzinfo=timezone.utc)
                        age_mins = (now - last_ts).total_seconds() / 60

                        if last_pos.on_ground and age_mins >= ground_close_mins:
                            # On-ground and stale — close directly without external API
                            logger.info(
                                f"{'Helicopter' if is_heli else 'Flight'} "
                                f"{flight.flight_number or flight.id} last on-ground "
                                f"{age_mins:.0f} min ago — closing directly"
                            )
                            await self._close_flight_direct(flight, last_pos, ac, session)
                            continue

                        if age_mins >= stale_mins:
                            # No positions for too long — try external reconciliation
                            logger.info(
                                f"Flight {flight.flight_number or flight.id} stale "
                                f"{age_mins:.0f} min (threshold {stale_mins}) — attempting reconciliation"
                            )
                            await self.reconcile_flight(str(flight.id), session)
                            await asyncio.sleep(1)
                    else:
                        # No positions at all — use flight creation time as age proxy
                        created = flight.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        created_age_mins = (now - created).total_seconds() / 60
                        if created_age_mins >= stale_mins:
                            logger.info(
                                f"Flight {flight.flight_number or flight.id} has no positions "
                                f"and is {created_age_mins:.0f} min old — attempting reconciliation"
                            )
                            await self.reconcile_flight(str(flight.id), session)
                            await asyncio.sleep(1)

                except Exception as e:
                    logger.error(
                        f"Reconciliation job: error processing flight {flight.id}: {e}",
                        exc_info=True,
                    )

            # Record last run timestamp for observability (no longer used for throttling)
            res_last = await session.execute(
                select(Setting).where(Setting.key == "last_reconciliation_run")
            )
            last_run_setting = res_last.scalars().first()
            if last_run_setting:
                last_run_setting.value = now.isoformat()
            else:
                session.add(Setting(key="last_reconciliation_run", value=now.isoformat()))
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
            
            # Find the in-progress flight to land. Only target active flights or scheduled
            # flights that have actually departed — never land a future scheduled flight.
            flight_result = await db.execute(
                select(Flight)
                .where(
                    Flight.aircraft_id == aircraft.id,
                    Flight.status.in_(["scheduled", "active"]),
                    Flight.actual_departure.isnot(None),
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
            status_str = ha_service.build_status_string(
                on_ground=True,
                arrival_name=arrival_name,
                arrival_iata=arrival_iata,
                location_name=latest_pos.location_name if latest_pos else None
            )
            await ha_service.update_aircraft_sensor(
                tail_number=aircraft.tail_number,
                status=status_str,
                flight_data=None,
                position_data={"on_ground": True}
            )
            
            await db.commit()
            return {"status": "success", "message": f"Aircraft grounded at {arrival_iata or 'last known position'}"}
            
        return {"status": "skipped", "message": "External source says aircraft is still active"}

    async def reconcile_all_active_flights(self, db: AsyncSession, skip_if_recent_minutes: int = 0) -> dict:
        """Reconcile all active and scheduled flights on demand, plus stuck airborne aircraft.

        skip_if_recent_minutes: if > 0, skip any flight whose last position report is fresher
        than this many minutes and not on_ground.  Used by the startup sweep to avoid
        closing flights that were clearly airborne moments before the restart.
        """
        from app.models import Position

        # Find all active or scheduled flights
        result = await db.execute(
            select(Flight).where(Flight.status.in_(["scheduled", "active"]))
        )
        open_flights = result.scalars().all()

        logger.info(f"Reconciliation triggered for {len(open_flights)} open flights "
                    f"(skip_if_recent_minutes={skip_if_recent_minutes})")

        now_utc = datetime.now(timezone.utc)
        results = []
        for flight in open_flights:
            try:
                # Skip scheduled flights whose departure is still in the future.
                # Also skip if actual_departure looks like stale historical data
                # (> 6 h before the scheduled departure).
                if flight.status == "scheduled" and flight.scheduled_departure:
                    sched_dep = flight.scheduled_departure
                    if sched_dep.tzinfo is None:
                        sched_dep = sched_dep.replace(tzinfo=timezone.utc)
                    if sched_dep > now_utc:
                        actual_dep = flight.actual_departure
                        if actual_dep and actual_dep.tzinfo is None:
                            actual_dep = actual_dep.replace(tzinfo=timezone.utc)
                        if actual_dep is None or actual_dep < sched_dep - timedelta(hours=6):
                            results.append({
                                "flight_id": str(flight.id),
                                "flight_number": flight.flight_number,
                                "status": "skipped",
                                "message": f"Future scheduled flight (departs {sched_dep.isoformat()})",
                            })
                            continue

                # Recency guard: skip flights with a recent airborne position
                if skip_if_recent_minutes > 0:
                    cutoff = datetime.now(timezone.utc) - timedelta(minutes=skip_if_recent_minutes)
                    pos_res = await db.execute(
                        select(Position)
                        .where(Position.flight_id == flight.id)
                        .order_by(Position.timestamp.desc())
                        .limit(1)
                    )
                    last_pos = pos_res.scalars().first()
                    if last_pos:
                        last_ts = last_pos.timestamp
                        if last_ts.tzinfo is None:
                            last_ts = last_ts.replace(tzinfo=timezone.utc)
                        if last_ts >= cutoff and not last_pos.on_ground:
                            logger.info(
                                f"Skipping reconciliation for flight {flight.flight_number or flight.id}: "
                                f"last position {last_ts} is within {skip_if_recent_minutes} min recency guard."
                            )
                            results.append({
                                "flight_id": str(flight.id),
                                "flight_number": flight.flight_number,
                                "callsign": flight.callsign,
                                "status": "skipped",
                                "message": f"Recent airborne position ({last_ts}); skipped by recency guard"
                            })
                            continue

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


    async def merge_flights(self, target_id: str, source_id: str, db: AsyncSession) -> dict:
        """
        Merge source flight into target flight.

        All positions from source are reassigned to target.  Target's timestamps,
        status, and summary stats are updated to reflect the combined data.
        Source flight is then deleted.

        Typical use-case: app restart caused an active flight to be closed and a
        new flight to be auto-created; this merges the two halves back into one.
        """
        from app.models import Position, record_flight_changes
        from sqlalchemy import update as sa_update

        # Load both flights
        t_res = await db.execute(select(Flight).where(Flight.id == target_id))
        target = t_res.scalars().first()
        if not target:
            raise ValueError(f"Target flight {target_id} not found")

        s_res = await db.execute(select(Flight).where(Flight.id == source_id))
        source = s_res.scalars().first()
        if not source:
            raise ValueError(f"Source flight {source_id} not found")

        if target.id == source.id:
            raise ValueError("Target and source flight must be different")

        if target.aircraft_id != source.aircraft_id:
            raise ValueError("Cannot merge flights from different aircraft")

        # Reassign all positions from source → target
        await db.execute(
            sa_update(Position)
            .where(Position.flight_id == source.id)
            .values(flight_id=target.id)
            .execution_options(synchronize_session=False)
        )

        # Also adopt any orphaned positions for this aircraft that might sit
        # between the two flights' timestamps
        combined_start = min(
            t for t in [
                target.actual_departure, target.scheduled_departure,
                source.actual_departure, source.scheduled_departure,
            ] if t is not None
        )
        combined_end = datetime.now(timezone.utc)

        start_naive = combined_start.replace(tzinfo=None) if combined_start.tzinfo else combined_start
        await db.execute(
            sa_update(Position)
            .where(
                Position.aircraft_id == target.aircraft_id,
                Position.flight_id.is_(None),
                Position.timestamp >= start_naive,
                Position.timestamp <= combined_end.replace(tzinfo=None),
            )
            .values(flight_id=target.id)
            .execution_options(synchronize_session=False)
        )

        # Merge timestamps: keep earliest departure, latest arrival
        def _tz(dt):
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        all_deps = [_tz(t) for t in [target.actual_departure, source.actual_departure] if t]
        all_arrs = [_tz(t) for t in [target.actual_arrival, source.actual_arrival] if t]

        if all_deps:
            target.actual_departure = min(all_deps)
        if all_arrs:
            target.actual_arrival = max(all_arrs)

        # Inherit the later flight's arrival metadata if the source is newer
        source_arr = _tz(source.actual_arrival)
        target_arr = _tz(target.actual_arrival)
        if source_arr and (not target_arr or source_arr > target_arr):
            if source.arrival_iata:
                target.arrival_iata = source.arrival_iata
            if source.arrival_icao:
                target.arrival_icao = source.arrival_icao
            if source.arrival_name:
                target.arrival_name = source.arrival_name
            if source.arrival_lat:
                target.arrival_lat = source.arrival_lat
            if source.arrival_lon:
                target.arrival_lon = source.arrival_lon

        # If the source was still active when merged, keep target active too
        if source.status == "active" or target.status == "active":
            target.status = "active"
            target.actual_arrival = None
        elif source.status == "landed" or target.status == "landed":
            target.status = "landed"

        # Inherit flight number / callsign from whichever has richer data
        if not target.flight_number and source.flight_number:
            target.flight_number = source.flight_number
        if not target.callsign and source.callsign:
            target.callsign = source.callsign

        # Log the merge
        await record_flight_changes(
            target,
            {"merged_from": str(source.id), "source_flight_number": source.flight_number},
            "merge",
            db,
        )

        # Recalculate summary stats
        try:
            from app.services.stats_calculator import calculate_flight_stats
            target.summary_stats = await calculate_flight_stats(target, db)
        except Exception as e:
            logger.warning(f"Stats recalculation after merge failed: {e}")

        # Delete source flight (positions already reassigned)
        await db.delete(source)
        await db.commit()

        logger.info(
            f"Merged flight {source_id} ({source.flight_number}) into "
            f"{target_id} ({target.flight_number}). Status: {target.status}"
        )
        return {
            "status": "success",
            "target_flight_id": str(target.id),
            "positions_merged": True,
        }


# Singleton
reconciliation_service = ReconciliationService()

