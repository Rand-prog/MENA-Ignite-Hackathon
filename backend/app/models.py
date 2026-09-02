"""Trip/subscription state. SQLite is enough for a hackathon prototype —
see the build prompt's explicit "don't over-engineer this."

Trip state lives entirely here, never on the phone — see
docs/SignalGuard_Technical_Feasibility.pdf §3.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class TripState(str, enum.Enum):
    IDLE = "IDLE"
    BUFFER = "BUFFER"
    ACTIVE = "ACTIVE"
    EXITED = "EXITED"
    OVERDUE = "OVERDUE"
    TIER1_ALERTED = "TIER1_ALERTED"
    TIER2_ESCALATED = "TIER2_ESCALATED"
    RESOLVED = "RESOLVED"


class Traveller(Base):
    __tablename__ = "travellers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    msisdn: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    battery_level: Mapped[int] = mapped_column(Integer, default=100)
    auth_token: Mapped[str] = mapped_column(String, default=_uuid, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="traveller", cascade="all, delete-orphan"
    )
    trips: Mapped[list["Trip"]] = relationship(
        back_populates="traveller", cascade="all, delete-orphan"
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    traveller_id: Mapped[str] = mapped_column(ForeignKey("travellers.id"))
    name: Mapped[str] = mapped_column(String)
    msisdn: Mapped[str] = mapped_column(String)

    traveller: Mapped[Traveller] = relationship(back_populates="contacts")


class Zone(Base):
    """Zone registry — real geofence configuration. Independent of the demo
    control surface. See docs/SignalGuard_Technical_Feasibility.pdf §1."""

    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String)
    entry_lat: Mapped[float] = mapped_column(Float)
    entry_lon: Mapped[float] = mapped_column(Float)
    exit_lat: Mapped[float] = mapped_column(Float)
    exit_lon: Mapped[float] = mapped_column(Float)
    gate_radius_m: Mapped[int] = mapped_column(Integer)
    corridor_km: Mapped[int] = mapped_column(Integer)
    nominal_crossing_min: Mapped[int] = mapped_column(Integer)


class Subscription(Base):
    """A real CAMARA Geofencing Subscription created on Nokia's sandbox."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    traveller_id: Mapped[str] = mapped_column(ForeignKey("travellers.id"))
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    gate: Mapped[str] = mapped_column(String)  # "entry" | "exit"
    nac_subscription_id: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    traveller_id: Mapped[str] = mapped_column(ForeignKey("travellers.id"))
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    state: Mapped[TripState] = mapped_column(Enum(TripState), default=TripState.IDLE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tier1_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    battery_at_entry: Mapped[int | None] = mapped_column(Integer, nullable=True)
    battery_band: Mapped[str | None] = mapped_column(String, nullable=True)
    congestion_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_point: Mapped[str | None] = mapped_column(String, nullable=True)
    last_known_location: Mapped[str | None] = mapped_column(String, nullable=True)
    predicted_crossing_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitoring_window_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    notifications: Mapped[list] = mapped_column(JSON, default=list)
    model_used: Mapped[str | None] = mapped_column(String, nullable=True)

    traveller: Mapped[Traveller] = relationship(back_populates="trips")


class ApiLogEntry(Base):
    """Every real Nokia call, in the shape /demo/api-log needs."""

    __tablename__ = "api_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    api: Mapped[str] = mapped_column(String)
    endpoint: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    trip_id: Mapped[str | None] = mapped_column(String, nullable=True)
