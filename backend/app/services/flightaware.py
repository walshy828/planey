import logging
import httpx
from typing import Optional, List
import os

logger = logging.getLogger(__name__)

class FlightAwareClient:
    """Client for FlightAware AeroAPI (v4)."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("AEROAPI_KEY")
        self.base_url = "https://aeroapi.flightaware.com/aeroapi"

    @property
    def is_enabled(self) -> bool:
        return bool(self.api_key)

    async def get_aircraft_flights(self, registration: str) -> List[dict]:
        """Get recent and upcoming flights for an aircraft by registration."""
        if not self.is_enabled:
            return []

        try:
            url = f"{self.base_url}/aircraft/{registration}/flights"
            headers = {"x-apikey": self.api_key}
            
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("flights", [])
                elif resp.status_code == 401:
                    logger.error("FlightAware AeroAPI: Unauthorized (Invalid API Key)")
                else:
                    logger.warning(f"FlightAware AeroAPI error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"FlightAware AeroAPI request failed: {e}")
        
        return []

    async def lookup_registration(self, registration: str) -> Optional[dict]:
        """
        Look up aircraft details and current/next flight via FlightAware.
        Returns a standardized dict or None.
        """
        if not self.is_enabled:
            return None

        flights = await self.get_aircraft_flights(registration)
        if not flights:
            return None

        # Find the most relevant flight (enroute or scheduled)
        # AeroAPI returns flights sorted by time
        best_flight = None
        for f in flights:
            status = f.get("status", "").lower()
            if "en route" in status or "scheduled" in status or "on time" in status:
                best_flight = f
                break
        
        # Fallback to the first one
        flight = best_flight or flights[0]
        
        return {
            "tail_number": registration.upper(),
            "flight_number": flight.get("ident"),
            "callsign": flight.get("ident"), # AeroAPI uses ident for callsign/flight_number
            "aircraft_type": flight.get("aircraft_type"),
            "airline": flight.get("operator"),
            "icao24_hex": flight.get("hexid"), # Note: hexid is often provided in v4
            "departure_iata": flight.get("origin", {}).get("code_iata"),
            "arrival_iata": flight.get("destination", {}).get("code_iata"),
            "status": flight.get("status"),
            "scheduled_departure": flight.get("scheduled_out") or flight.get("scheduled_off"),
            "scheduled_arrival": flight.get("scheduled_in") or flight.get("scheduled_on"),
        }

# Singleton instance
fa_client = FlightAwareClient()
