import math
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Flight, Position
from app.services.geocoder import geocoder

logger = logging.getLogger(__name__)

def haversine_distance(lat1: Optional[float], lon1: Optional[float], lat2: Optional[float], lon2: Optional[float]) -> float:
    """
    Calculate the great-circle distance between two points on the Earth
    in Nautical Miles (NM).
    """
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    
    # Earth's radius in Nautical Miles
    R = 3440.065
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2.0) ** 2) + \
        (math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
        
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

async def calculate_flight_stats(flight: Flight, db: AsyncSession) -> dict:
    """
    Calculate summary stats for a flight based on position reports.
    """
    # 1. Fetch all position reports sorted by timestamp
    result = await db.execute(
        select(Position)
        .where(Position.flight_id == flight.id)
        .order_by(Position.timestamp.asc())
    )
    positions = result.scalars().all()
    
    # Initialize default stats structure
    distance_nm = 0.0
    avg_speed_kts = 0.0
    max_speed_kts = 0.0
    max_altitude_ft = 0.0
    duration_seconds = 0
    
    # 2. Cumulative flown path calculation if telemetry is present
    if len(positions) > 1:
        # Sum successive coordinates distances
        for i in range(len(positions) - 1):
            p1 = positions[i]
            p2 = positions[i+1]
            dist = haversine_distance(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
            # Avoid massive spikes from coordinate glitches (limit maximum realistic segment to 150 NM between successive updates)
            if dist < 150.0:
                distance_nm += dist
            
        # Calculate duration
        dep_time = flight.actual_departure or positions[0].timestamp
        arr_time = flight.actual_arrival or positions[-1].timestamp
        if dep_time and arr_time:
            duration_seconds = int((arr_time - dep_time).total_seconds())
            
        # Extract speeds and altitudes
        speeds = [p.ground_speed_kts for p in positions if p.ground_speed_kts is not None]
        alts = [p.altitude_ft for p in positions if p.altitude_ft is not None]
        
        if speeds:
            max_speed_kts = max(speeds)
        if alts:
            max_altitude_ft = max(alts)
            
        # Average ground speed: pilot standard - average of airborne speeds (speed > 40 kts, not on_ground)
        airborne_speeds = [
            p.ground_speed_kts for p in positions 
            if not p.on_ground and p.ground_speed_kts is not None and p.ground_speed_kts > 40.0
        ]
        if airborne_speeds:
            avg_speed_kts = sum(airborne_speeds) / len(airborne_speeds)
        elif speeds:
            avg_speed_kts = sum(speeds) / len(speeds)
            
    else:
        # 3. Sparse/No Positions Fallback: Great Circle direct distance between airport coordinates
        dep_lat = flight.departure_lat
        dep_lon = flight.departure_lon
        arr_lat = flight.arrival_lat
        arr_lon = flight.arrival_lon
        
        # Self-heal departure coordinates if missing and we have IATA code
        if (dep_lat is None or dep_lon is None) and flight.departure_iata:
            coords = await geocoder.get_airport_coordinates(flight.departure_iata)
            if coords:
                dep_lat, dep_lon = coords
                flight.departure_lat = dep_lat
                flight.departure_lon = dep_lon
                
        # Self-heal arrival coordinates if missing and we have IATA code
        if (arr_lat is None or arr_lon is None) and flight.arrival_iata:
            coords = await geocoder.get_airport_coordinates(flight.arrival_iata)
            if coords:
                arr_lat, arr_lon = coords
                flight.arrival_lat = arr_lat
                flight.arrival_lon = arr_lon
                
        if dep_lat is not None and dep_lon is not None and arr_lat is not None and arr_lon is not None:
            distance_nm = haversine_distance(dep_lat, dep_lon, arr_lat, arr_lon)
            
        # Calculate fallback duration
        dep_time = flight.actual_departure or flight.scheduled_departure
        arr_time = flight.actual_arrival or flight.scheduled_arrival
        if dep_time and arr_time:
            duration_seconds = int((arr_time - dep_time).total_seconds())
            
        # Fallback cruising speed based on duration
        if duration_seconds > 0 and distance_nm > 0:
            avg_speed_kts = distance_nm / (duration_seconds / 3600.0)
            max_speed_kts = avg_speed_kts

    # 4. Routing Efficiency Ratio (Direct Line / Flown Path)
    direct_distance_nm = distance_nm
    dep_lat = flight.departure_lat
    dep_lon = flight.departure_lon
    arr_lat = flight.arrival_lat
    arr_lon = flight.arrival_lon
    
    if dep_lat is not None and dep_lon is not None and arr_lat is not None and arr_lon is not None:
        direct_distance_nm = haversine_distance(dep_lat, dep_lon, arr_lat, arr_lon)
        
    efficiency_ratio = 1.0
    if direct_distance_nm > 0 and distance_nm > 0:
        efficiency_ratio = direct_distance_nm / distance_nm
        # Clamp efficiency ratio between 0.0 and 1.0 safely
        efficiency_ratio = min(max(efficiency_ratio, 0.0), 1.0)
        
    distance_sm = distance_nm * 1.15078
    
    stats = {
        "duration_seconds": max(duration_seconds, 0),
        "distance_nm": round(distance_nm, 1),
        "distance_sm": round(distance_sm, 1),
        "direct_distance_nm": round(direct_distance_nm, 1),
        "avg_ground_speed_kts": round(avg_speed_kts, 1),
        "max_ground_speed_kts": round(max_speed_kts, 1),
        "max_altitude_ft": int(max_altitude_ft),
        "efficiency_ratio": round(efficiency_ratio, 3)
    }
    return stats

async def update_flight_stats_if_needed(flight: Flight, db: AsyncSession) -> bool:
    """
    If the flight is landed and summary_stats is missing, calculate and save it.
    """
    if flight.status == "landed" and (flight.summary_stats is None or not flight.summary_stats):
        try:
            stats = await calculate_flight_stats(flight, db)
            flight.summary_stats = stats
            logger.info(f"Calculated flight summary stats for flight {flight.id}: {stats}")
            return True
        except Exception as e:
            logger.error(f"Error calculating stats for flight {flight.id}: {e}")
    return False
