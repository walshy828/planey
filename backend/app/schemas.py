"""
Planey Schemas Module

Pydantic models for API request/response serialization.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field


# =============================================================================
# Aircraft Schemas
# =============================================================================

class AircraftCreate(BaseModel):
    """Schema for adding a new aircraft to track."""
    tail_number: str = Field(..., min_length=1, max_length=20, description="Aircraft registration (e.g., N12345)")
    icao24_hex: Optional[str] = Field(None, max_length=6, description="ICAO24 hex address for OpenSky tracking")
    aircraft_type: Optional[str] = Field(None, max_length=100)
    airline: Optional[str] = Field(None, max_length=100)
    display_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field("plane", description="Category: plane or helicopter")


class AircraftUpdate(BaseModel):
    """Schema for updating an aircraft."""
    tail_number: Optional[str] = Field(None, min_length=1, max_length=20)
    icao24_hex: Optional[str] = Field(None, max_length=6)
    aircraft_type: Optional[str] = Field(None, max_length=100)
    airline: Optional[str] = Field(None, max_length=100)
    display_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, description="Category: plane or helicopter")
    active: Optional[bool] = None


class AircraftResponse(BaseModel):
    """Schema for aircraft API responses."""
    id: uuid.UUID
    tail_number: str
    icao24_hex: Optional[str] = None
    aircraft_type: Optional[str] = None
    airline: Optional[str] = None
    display_name: Optional[str] = None
    category: str
    photo_url: Optional[str] = None
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AircraftWithLatest(AircraftResponse):
    """Aircraft response with latest position data."""
    latest_position: Optional["PositionResponse"] = None
    active_flight: Optional["FlightResponse"] = None


# =============================================================================
# Flight Schemas
# =============================================================================

class FlightCreate(BaseModel):
    """Schema for adding a flight to track."""
    aircraft_id: uuid.UUID
    flight_number: Optional[str] = Field(None, max_length=20)
    callsign: Optional[str] = Field(None, max_length=20)
    departure_iata: Optional[str] = Field(None, max_length=4)
    departure_icao: Optional[str] = Field(None, max_length=4)
    departure_name: Optional[str] = Field(None, max_length=200)
    arrival_iata: Optional[str] = Field(None, max_length=4)
    arrival_icao: Optional[str] = Field(None, max_length=4)
    arrival_name: Optional[str] = Field(None, max_length=200)
    departure_lat: Optional[float] = None
    departure_lon: Optional[float] = None
    arrival_lat: Optional[float] = None
    arrival_lon: Optional[float] = None
    scheduled_departure: Optional[datetime] = None
    scheduled_arrival: Optional[datetime] = None
    expected_route: Optional[str] = None
    status: str = "scheduled"


class FlightUpdate(BaseModel):
    """Schema for updating a flight."""
    flight_number: Optional[str] = None
    callsign: Optional[str] = None
    departure_iata: Optional[str] = None
    departure_icao: Optional[str] = None
    departure_name: Optional[str] = None
    departure_lat: Optional[float] = None
    departure_lon: Optional[float] = None
    arrival_iata: Optional[str] = None
    arrival_icao: Optional[str] = None
    arrival_name: Optional[str] = None
    arrival_lat: Optional[float] = None
    arrival_lon: Optional[float] = None
    scheduled_departure: Optional[datetime] = None
    scheduled_arrival: Optional[datetime] = None
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    status: Optional[str] = None
    summary_stats: Optional[dict] = None



class FlightResponse(BaseModel):
    """Schema for flight API responses."""
    id: uuid.UUID
    aircraft_id: uuid.UUID
    fa_flight_id: Optional[str] = None
    flight_number: Optional[str] = None
    callsign: Optional[str] = None
    departure_iata: Optional[str] = None
    departure_icao: Optional[str] = None
    departure_name: Optional[str] = None
    arrival_iata: Optional[str] = None
    arrival_icao: Optional[str] = None
    arrival_name: Optional[str] = None
    departure_lat: Optional[float] = None
    departure_lon: Optional[float] = None
    arrival_lat: Optional[float] = None
    arrival_lon: Optional[float] = None
    scheduled_departure: Optional[datetime] = None
    scheduled_arrival: Optional[datetime] = None
    expected_route: Optional[str] = None
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    status: str
    gate_info: Optional[dict] = None
    summary_stats: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FlightChangeHistoryResponse(BaseModel):
    """Schema for flight change history entries."""
    id: int
    flight_id: uuid.UUID
    changed_at: datetime
    change_source: str
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None

    class Config:
        from_attributes = True


class FlightWithPositions(FlightResponse):
    """Flight response with position trail."""
    positions: list["PositionResponse"] = []
    aircraft: Optional[AircraftResponse] = None
    change_history: list[FlightChangeHistoryResponse] = []


# =============================================================================
# Position Schemas
# =============================================================================

class PositionResponse(BaseModel):
    """Schema for position API responses."""
    id: int
    aircraft_id: uuid.UUID
    flight_id: Optional[uuid.UUID] = None
    latitude: float
    longitude: float
    altitude_ft: Optional[float] = None
    ground_elevation_ft: Optional[float] = None
    ground_speed_kts: Optional[float] = None
    heading: Optional[float] = None
    vertical_rate_fpm: Optional[float] = None
    on_ground: bool
    squawk: Optional[str] = None
    source: str
    location_name: Optional[str] = None
    timestamp: datetime

    @computed_field
    @property
    def agl_ft(self) -> Optional[float]:
        if self.altitude_ft is not None and self.ground_elevation_ft is not None:
            return round(self.altitude_ft - self.ground_elevation_ft, 1)
        return None

    class Config:
        from_attributes = True


class PositionUpdate(BaseModel):
    """Schema for updating a position report."""
    flight_id: Optional[uuid.UUID] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_ft: Optional[float] = None
    ground_speed_kts: Optional[float] = None
    heading: Optional[float] = None
    vertical_rate_fpm: Optional[float] = None
    on_ground: Optional[bool] = None
    squawk: Optional[str] = None
    timestamp: Optional[datetime] = None


# =============================================================================
# Lookup / Search Schemas
# =============================================================================

class FlightLookupRequest(BaseModel):
    """Schema for looking up a flight from FlightRadar24."""
    flight_number: Optional[str] = None
    tail_number: Optional[str] = None
    callsign: Optional[str] = None


class FlightLookupResponse(BaseModel):
    """Schema for flight lookup results."""
    flight_number: Optional[str] = None
    callsign: Optional[str] = None
    tail_number: Optional[str] = None
    icao24_hex: Optional[str] = None
    aircraft_type: Optional[str] = None
    airline: Optional[str] = None
    departure_iata: Optional[str] = None
    departure_name: Optional[str] = None
    arrival_iata: Optional[str] = None
    arrival_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_ft: Optional[float] = None
    ground_speed_kts: Optional[float] = None
    heading: Optional[float] = None
    status: Optional[str] = None
    photo_url: Optional[str] = None


# =============================================================================
# WebSocket Schemas
# =============================================================================

class WSPositionUpdate(BaseModel):
    """WebSocket message for position updates."""
    type: str = "position_update"
    aircraft_id: str
    tail_number: str
    flight_id: Optional[str] = None
    data: dict


class WSFlightStatusUpdate(BaseModel):
    """WebSocket message for flight status changes."""
    type: str = "flight_status"
    flight_id: str
    aircraft_id: str
    old_status: str
    new_status: str
    data: dict


# Rebuild forward refs
AircraftWithLatest.model_rebuild()
FlightWithPositions.model_rebuild()

# =============================================================================
# Webhook Schemas
# =============================================================================

class WebhookFlightFiled(BaseModel):
    """Payload from N8N when a flight plan is filed."""
    tail_number: str = Field(..., description="Aircraft registration (e.g., N12345)")
    flight_number: Optional[str] = Field(None, description="Airline flight number if applicable")
    callsign: Optional[str] = Field(None, description="ATC Callsign")
    departure_iata: Optional[str] = Field(None, description="Departure Airport IATA/ICAO")
    arrival_iata: Optional[str] = Field(None, description="Arrival Airport IATA/ICAO")
    scheduled_departure: Optional[datetime] = Field(None, description="Scheduled Departure Time (ISO 8601)")
    scheduled_arrival: Optional[datetime] = Field(None, description="Scheduled Arrival Time (ISO 8601)")
    expected_route: Optional[str] = Field(None, description="Raw expected route string")

class WebhookFlightDeparted(BaseModel):
    """Payload from N8N when an aircraft departs."""
    tail_number: str = Field(..., description="Aircraft registration (e.g., N12345)")
    flight_number: Optional[str] = Field(None, description="Airline flight number if applicable")
    departure_iata: Optional[str] = Field(None, description="Departure Airport IATA/ICAO")
    arrival_iata: Optional[str] = Field(None, description="Arrival Airport IATA/ICAO")
    actual_departure: Optional[datetime] = Field(None, description="Actual Departure Time (ISO 8601)")
    scheduled_arrival: Optional[datetime] = Field(None, description="Estimated/Scheduled Arrival Time (ISO 8601)")


class WebhookFlightArrived(BaseModel):
    """Payload from N8N when an aircraft lands/arrives."""
    tail_number: str = Field(..., description="Aircraft registration (e.g., N12345)")
    flight_number: Optional[str] = Field(None, description="Airline flight number if applicable")
    arrival_iata: Optional[str] = Field(None, description="Arrival Airport IATA/ICAO")
    actual_arrival: Optional[datetime] = Field(None, description="Actual Arrival Time (ISO 8601)")


class WebhookFlightSpotted(BaseModel):
    """Payload from N8N when FlightAware spots an aircraft in flight with no filed plan."""
    tail_number: str = Field(..., description="Aircraft registration (e.g., N12345)")
    spotted_time: Optional[datetime] = Field(None, description="Time aircraft was spotted (ISO 8601)")
    location: Optional[str] = Field(None, description="Approximate location string (e.g., 'Worcester, MA')")


class WebhookFlightTrackingStopped(BaseModel):
    """Payload from N8N when FlightAware stops tracking an aircraft."""
    tail_number: str = Field(..., description="Aircraft registration (e.g., N12345)")
    tracking_stopped_time: Optional[datetime] = Field(None, description="Time tracking stopped (ISO 8601)")
    location: Optional[str] = Field(None, description="Location where tracking stopped (e.g., 'Orange, MA')")
    from_location: Optional[str] = Field(None, description="Origin location (e.g., 'Worcester, MA')")


