"""
Home Assistant Integration Service

Pushes aircraft sensor states and attributes to Home Assistant
via the REST API (POST /api/states/sensor.planey_*).
"""

import logging
import re
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class HomeAssistantService:
    """Manages pushing flight tracking data to Home Assistant sensors."""

    def __init__(self):
        self._enabled = settings.ha_enabled and bool(settings.ha_token)
        self._url = settings.ha_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.ha_token}",
            "Content-Type": "application/json",
        }
        if self._enabled:
            logger.info(f"Home Assistant integration enabled: {self._url}")
        else:
            logger.info("Home Assistant integration disabled")

    def _sanitize_entity_id(self, tail_number: str) -> str:
        """Convert a tail number to a valid HA entity ID component."""
        # Replace non-alphanumeric chars with underscores, lowercase
        sanitized = re.sub(r'[^a-zA-Z0-9]', '_', tail_number.lower())
        return sanitized.strip('_')

    async def update_aircraft_sensor(
        self,
        tail_number: str,
        status: str,
        flight_data: Optional[dict] = None,
        position_data: Optional[dict] = None,
    ):
        """
        Update (or create) a Home Assistant sensor for an aircraft.

        Sensor state format:
            - "ground - KJFK"      (on ground at airport)
            - "planned - KLAX, 14:30"  (scheduled flight)
            - "flight - KLAX"      (in the air)

        Args:
            tail_number: Aircraft registration (e.g., N12345)
            status: Current status string for the sensor state
            flight_data: Dict with flight info (flight_number, departure, arrival, times)
            position_data: Dict with position info (lat, lon, alt, speed, heading, etc.)
        """
        if not self._enabled:
            return

        entity_id = f"sensor.planey_{self._sanitize_entity_id(tail_number)}"

        # Build attributes
        attributes = {
            "friendly_name": f"Planey {tail_number}",
            "icon": self._get_icon(status),
            "tail_number": tail_number,
            "last_updated": None,
            "source": "planey",
        }

        # Add position attributes
        if position_data:
            attributes.update({
                "latitude": position_data.get("latitude"),
                "longitude": position_data.get("longitude"),
                "altitude_ft": position_data.get("altitude_ft"),
                "ground_speed_kts": position_data.get("ground_speed_kts"),
                "heading": position_data.get("heading"),
                "vertical_rate_fpm": position_data.get("vertical_rate_fpm"),
                "on_ground": position_data.get("on_ground", False),
                "squawk": position_data.get("squawk"),
                "location_name": position_data.get("location_name"),
                "location_city_state": position_data.get("location_city_state"),
                "last_updated": position_data.get("timestamp"),
            })

        # Add flight attributes
        if flight_data:
            flight_status = flight_data.get("status")
            attributes.update({
                "flight_number": flight_data.get("flight_number"),
                "callsign": flight_data.get("callsign"),
                "departure_airport": flight_data.get("departure_iata"),
                "departure_name": flight_data.get("departure_name"),
                "arrival_airport": flight_data.get("arrival_iata"),
                "arrival_name": flight_data.get("arrival_name"),
                "scheduled_departure": flight_data.get("scheduled_departure"),
                "scheduled_arrival": flight_data.get("scheduled_arrival"),
                "actual_departure": flight_data.get("actual_departure"),
                "actual_arrival": flight_data.get("actual_arrival"),
                "aircraft_type": flight_data.get("aircraft_type"),
                "airline": flight_data.get("airline"),
                "flight_status": flight_status,
            })

            # location_airport: show where the aircraft currently is or last was
            if flight_status == "landed":
                attributes["location_airport"] = flight_data.get("arrival_iata")
                attributes["location_airport_name"] = flight_data.get("arrival_name")
                # If no live position was provided, fall back to arrival airport coordinates
                if not position_data and flight_data.get("arrival_lat"):
                    attributes["latitude"] = flight_data["arrival_lat"]
                    attributes["longitude"] = flight_data["arrival_lon"]
            else:
                attributes["location_airport"] = flight_data.get("departure_iata")
                attributes["location_airport_name"] = flight_data.get("departure_name")

        # Remove None values from attributes
        attributes = {k: v for k, v in attributes.items() if v is not None}

        payload = {
            "state": status,
            "attributes": attributes,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self._url}/api/states/{entity_id}",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                logger.debug(f"Updated HA sensor: {entity_id} = {status}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Home Assistant authentication failed - check HA_TOKEN")
            else:
                logger.error(f"HA API error for {entity_id}: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"HA request failed for {entity_id}: {e}")

    def _get_icon(self, status: str) -> str:
        """Get an MDI icon based on aircraft status."""
        if "flight" in status.lower():
            return "mdi:airplane"
        elif "ground" in status.lower():
            return "mdi:airplane-landing"
        elif "planned" in status.lower():
            return "mdi:airplane-clock"
        elif "landing" in status.lower():
            return "mdi:airplane-landing"
        elif "takeoff" in status.lower():
            return "mdi:airplane-takeoff"
        return "mdi:airplane"

    def build_status_string(
        self,
        on_ground: bool,
        departure_iata: Optional[str] = None,
        arrival_iata: Optional[str] = None,
        departure_name: Optional[str] = None,
        arrival_name: Optional[str] = None,
        scheduled_arrival: Optional[str] = None,
        scheduled_departure: Optional[str] = None,
        flight_status: Optional[str] = None,
        location_name: Optional[str] = None,
    ) -> str:
        """
        Build the sensor state string based on user requirements:
        1. Planned: "Planned - {arrival_name or arrival_iata}" or "Planned"
        2. Ground: "ground - {arrival_name}" or "ground - {location_name}" or "ground"
        3. Flight (Airborne): "Flight - {arrival_name or arrival_iata}" or "Flight - VFR"
        """
        # 1. Planned/Scheduled Status
        if flight_status == "scheduled":
            dest = arrival_name or arrival_iata
            if dest:
                return f"Planned - {dest}"
            return "Planned"

        # 2. Ground Status
        if on_ground:
            if arrival_name:
                return f"ground - {arrival_name}"
            elif location_name:
                return f"ground - {location_name}"
            return "ground"

        # 3. Flight/Airborne Status
        dest = arrival_name or arrival_iata
        if not dest:
            return "Flight - VFR"
        return f"Flight - {dest}"


    async def remove_aircraft_sensor(self, tail_number: str):
        """Remove an aircraft sensor from Home Assistant (set to unavailable)."""
        if not self._enabled:
            return

        entity_id = f"sensor.planey_{self._sanitize_entity_id(tail_number)}"

        payload = {
            "state": "unavailable",
            "attributes": {
                "friendly_name": f"Planey {tail_number}",
                "icon": "mdi:airplane-off",
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self._url}/api/states/{entity_id}",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                logger.info(f"Set HA sensor {entity_id} to unavailable")
        except Exception as e:
            logger.error(f"Failed to remove HA sensor {entity_id}: {e}")


# Singleton instance
ha_service = HomeAssistantService()
