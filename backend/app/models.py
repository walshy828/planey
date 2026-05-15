"""
Planey Database Models

SQLAlchemy ORM models for Aircraft, Flight, and Position tracking.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    BigInteger,
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow():
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


class Setting(Base):
    """System settings stored in the database."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

class Aircraft(Base):
    """
    Represents an aircraft being tracked.
    Identified by tail number (registration) and/or ICAO24 hex address.
    """

    __tablename__ = "aircraft"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tail_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    icao24_hex: Mapped[str | None] = mapped_column(
        String(6), nullable=True, index=True
    )
    aircraft_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    airline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Relationships
    flights: Mapped[list["Flight"]] = relationship(
        "Flight", back_populates="aircraft", cascade="all, delete-orphan"
    )
    positions: Mapped[list["Position"]] = relationship(
        "Position", back_populates="aircraft", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Aircraft {self.tail_number} ({self.aircraft_type})>"


class Flight(Base):
    """
    Represents a specific flight (e.g., AA100 on 2026-05-14).
    Links to the aircraft and contains schedule/status information.
    """

    __tablename__ = "flights"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    aircraft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    flight_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    callsign: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # Departure info
    departure_iata: Mapped[str | None] = mapped_column(String(4), nullable=True)
    departure_icao: Mapped[str | None] = mapped_column(String(4), nullable=True)
    departure_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    departure_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    departure_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Arrival info
    arrival_iata: Mapped[str | None] = mapped_column(String(4), nullable=True)
    arrival_icao: Mapped[str | None] = mapped_column(String(4), nullable=True)
    arrival_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    arrival_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    arrival_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Times
    scheduled_departure: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_arrival: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_departure: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_arrival: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Status: scheduled, active, landed, cancelled, diverted, unknown
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)

    # Additional data
    gate_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expected_route: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Relationships
    aircraft: Mapped["Aircraft"] = relationship("Aircraft", back_populates="flights")
    positions: Mapped[list["Position"]] = relationship(
        "Position", back_populates="flight", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Flight {self.flight_number} {self.departure_iata}→{self.arrival_iata} [{self.status}]>"


class Position(Base):
    """
    A single position report for an aircraft at a specific moment in time.
    Captured approximately every 60 seconds from OpenSky Network.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aircraft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    flight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flights.id", ondelete="SET NULL"), nullable=True
    )

    # Position data
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    ground_speed_kts: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    vertical_rate_fpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_ground: Mapped[bool] = mapped_column(Boolean, default=False)
    squawk: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # Source of the data
    source: Mapped[str] = mapped_column(String(20), default="opensky")
    location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Relationships
    aircraft: Mapped["Aircraft"] = relationship("Aircraft", back_populates="positions")
    flight: Mapped["Flight | None"] = relationship("Flight", back_populates="positions")

    # Composite index for efficient route queries
    __table_args__ = (
        Index("ix_positions_aircraft_timestamp", "aircraft_id", "timestamp"),
        Index("ix_positions_flight_timestamp", "flight_id", "timestamp"),
    )

    def __repr__(self):
        return f"<Position {self.latitude},{self.longitude} alt={self.altitude_ft}ft>"
