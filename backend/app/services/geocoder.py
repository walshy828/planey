import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

US_STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}


def abbreviate_location(location_name: str) -> Optional[str]:
    """Convert 'City, Full State Name' to 'City, ST' for US locations."""
    if not location_name or ", " not in location_name:
        return location_name
    city, state = location_name.rsplit(", ", 1)
    abbr = US_STATE_ABBR.get(state.strip())
    if abbr:
        return f"{city}, {abbr}"
    return location_name


class GeocoderService:
    """Service for reverse geocoding GPS coordinates to city/state names."""
    
    def __init__(self):
        self._cache = {}

    async def get_location_name(self, lat: float, lon: float) -> str:
        """
        Convert lat/lon to a human readable string.
        Uses OpenStreetMap Nominatim API.
        """
        if lat is None or lon is None:
            return "Unknown"
            
        # Cache to avoid API limits (1 req/sec max for Nominatim)
        # We round to 2 decimal places to cache nearby points (~1km accuracy)
        key = f"{round(lat, 2)},{round(lon, 2)}"
        
        if key in self._cache:
            return self._cache[key]

        try:
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
            headers = {'User-Agent': 'Planey Flight Tracker'}
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                
                if resp.status_code != 200:
                    return "Unknown"
                    
                data = resp.json()
                
                name = "Unknown"
                if data and "address" in data:
                    addr = data["address"]
                    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
                    state = addr.get("state") or addr.get("country")
                    
                    if city and state:
                        name = f"{city}, {state}"
                    elif city:
                        name = city
                    elif state:
                        name = state
                
                self._cache[key] = name
                return name
                
        except Exception as e:
            logger.error(f"Geocoding failed for {lat},{lon}: {e}")
            return "Unknown"

    async def get_airport_coordinates(self, code: str) -> Optional[tuple[float, float]]:
        """
        Attempt to find coordinates for an airport by IATA or ICAO code.
        Search order: aeroway tag → airport name suffix → ICAO lookup (for K-prefix codes).
        Returns None if no reliable aeroway result is found.
        """
        if not code:
            return None
        code = code.strip().upper()
        headers = {'User-Agent': 'Planey Flight Tracker'}

        async def _search(query: str) -> Optional[tuple[float, float]]:
            try:
                url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=5"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        for item in resp.json():
                            if item.get('class') == 'aeroway':
                                return float(item['lat']), float(item['lon'])
            except Exception as e:
                logger.error(f"Geocode search failed for '{query}': {e}")
            return None

        # Strategy 1: raw code (works for well-known IATA codes)
        result = await _search(code)
        if result:
            return result

        # Strategy 2: "<code> airport" suffix
        result = await _search(f"{code} airport")
        if result:
            return result

        # Strategy 3: for US ICAO codes (K + 3 chars), also try the 3-letter suffix alone
        if len(code) == 4 and code.startswith('K'):
            result = await _search(f"{code[1:]} airport")
            if result:
                return result

        logger.warning(f"Could not resolve airport coordinates for '{code}'")
        return None

# Singleton instance
geocoder = GeocoderService()
