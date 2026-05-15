"""
OpenSky Network Client

Fetches real-time ADS-B position data for tracked aircraft.
API Docs: https://openskynetwork.github.io/opensky-api/rest.html
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENSKY_BASE_URL = "https://opensky-network.org/api"

# Conversion factors
METERS_TO_FEET = 3.28084
MPS_TO_KNOTS = 1.94384
MPS_TO_FPM = 196.85  # meters/second to feet/minute


@dataclass
class StateVector:
    """Parsed OpenSky state vector for a single aircraft."""
    icao24: str
    callsign: Optional[str]
    origin_country: str
    time_position: Optional[int]
    last_contact: int
    longitude: Optional[float]
    latitude: Optional[float]
    baro_altitude_m: Optional[float]
    on_ground: bool
    velocity_mps: Optional[float]
    true_track: Optional[float]
    vertical_rate_mps: Optional[float]
    sensors: Optional[list]
    geo_altitude_m: Optional[float]
    squawk: Optional[str]
    spi: bool
    position_source: int

    @property
    def altitude_ft(self) -> Optional[float]:
        """Barometric altitude in feet."""
        if self.baro_altitude_m is not None:
            return round(self.baro_altitude_m * METERS_TO_FEET, 0)
        if self.geo_altitude_m is not None:
            return round(self.geo_altitude_m * METERS_TO_FEET, 0)
        return None

    @property
    def ground_speed_kts(self) -> Optional[float]:
        """Ground speed in knots."""
        if self.velocity_mps is not None:
            return round(self.velocity_mps * MPS_TO_KNOTS, 1)
        return None

    @property
    def heading(self) -> Optional[float]:
        """True track / heading in degrees."""
        return self.true_track

    @property
    def vertical_rate_fpm(self) -> Optional[float]:
        """Vertical rate in feet per minute."""
        if self.vertical_rate_mps is not None:
            return round(self.vertical_rate_mps * MPS_TO_FPM, 0)
        return None

    @property
    def timestamp(self) -> datetime:
        """Position timestamp as datetime."""
        ts = self.time_position or self.last_contact
        return datetime.fromtimestamp(ts, tz=timezone.utc)


class OpenSkyClient:
    """Client for the OpenSky Network REST API."""

    def __init__(self):
        self._auth = None
        if settings.opensky_username and settings.opensky_password:
            self._auth = (settings.opensky_username, settings.opensky_password)
            logger.info("OpenSky client initialized with authentication")
        else:
            logger.warning("OpenSky client running without authentication (tighter rate limits)")

    async def get_states(self, icao24_list: list[str]) -> list[StateVector]:
        """
        Fetch current state vectors for a list of ICAO24 hex addresses.

        Args:
            icao24_list: List of ICAO24 hex addresses (lowercase).

        Returns:
            List of StateVector objects for aircraft that have active data.
        """
        if not icao24_list:
            return []

        # Build query params - OpenSky accepts multiple icao24 params
        params = [("icao24", icao.lower()) for icao in icao24_list]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{OPENSKY_BASE_URL}/states/all",
                    params=params,
                    auth=self._auth,
                )
                response.raise_for_status()
                data = response.json()

            states = data.get("states")
            if not states:
                logger.debug(f"No state vectors returned for {len(icao24_list)} aircraft")
                return []

            result = []
            for s in states:
                try:
                    sv = StateVector(
                        icao24=s[0],
                        callsign=s[1].strip() if s[1] else None,
                        origin_country=s[2],
                        time_position=s[3],
                        last_contact=s[4],
                        longitude=s[5],
                        latitude=s[6],
                        baro_altitude_m=s[7],
                        on_ground=s[8],
                        velocity_mps=s[9],
                        true_track=s[10],
                        vertical_rate_mps=s[11],
                        sensors=s[12],
                        geo_altitude_m=s[13],
                        squawk=s[14],
                        spi=s[15],
                        position_source=s[16],
                    )
                    # Only include if we have position data
                    if sv.latitude is not None and sv.longitude is not None:
                        result.append(sv)
                except (IndexError, TypeError) as e:
                    logger.warning(f"Failed to parse state vector: {e}")
                    continue

            logger.info(f"OpenSky returned {len(result)} valid positions for {len(icao24_list)} tracked aircraft")
            return result

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("OpenSky rate limit hit, will retry next cycle")
            else:
                logger.error(f"OpenSky API error: {e.response.status_code} - {e.response.text}")
            return []
        except httpx.RequestError as e:
            logger.error(f"OpenSky request failed: {e}")
            return []

    async def get_all_states_in_bbox(
        self,
        lamin: float,
        lamax: float,
        lomin: float,
        lomax: float,
    ) -> list[StateVector]:
        """
        Fetch all state vectors within a bounding box.
        Useful for discovery/testing.
        """
        params = {
            "lamin": lamin,
            "lamax": lamax,
            "lomin": lomin,
            "lomax": lomax,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{OPENSKY_BASE_URL}/states/all",
                    params=params,
                    auth=self._auth,
                )
                response.raise_for_status()
                data = response.json()

            states = data.get("states", [])
            if not states:
                return []

            return [
                StateVector(
                    icao24=s[0],
                    callsign=s[1].strip() if s[1] else None,
                    origin_country=s[2],
                    time_position=s[3],
                    last_contact=s[4],
                    longitude=s[5],
                    latitude=s[6],
                    baro_altitude_m=s[7],
                    on_ground=s[8],
                    velocity_mps=s[9],
                    true_track=s[10],
                    vertical_rate_mps=s[11],
                    sensors=s[12],
                    geo_altitude_m=s[13],
                    squawk=s[14],
                    spi=s[15],
                    position_source=s[16],
                )
                for s in states
                if s[6] is not None and s[5] is not None
            ]
        except Exception as e:
            logger.error(f"OpenSky bbox query failed: {e}")
            return []


# Singleton instance
opensky_client = OpenSkyClient()
