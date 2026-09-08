"""Trip/subscription state. SQLite is enough for a hackathon prototype —
see the build prompt's explicit "don't over-engineer this."

Trip state lives entirely here, never on the phone — see
docs/SignalGuard_Technical_Feasibility.pdf §3.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class TripState(str, enum.Enum):
    IDLE = "IDLE"
    BUFFER = "BUFFER"
    ACTIVE = "ACTIVE"
    EXITED = "EXITED"
    # Tier 0 — the traveller's own device gets asked before any human is
    # told. Entered only when the window expires *and* the network says
    # the handset is reachable again; a dark handset skips straight to
    # Tier 1. Timer-bounded like every other state (tier0_deadline), so
    # it can delay an alarm by at most tier0_grace_sec, never suppress it.
    TIER0_CHECKING = "TIER0_CHECKING"
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
    # A break the traveller declared before setting off, waiting for the
    # next crossing to consume it. Held here rather than on a Trip because
    # at declaration time there is no trip yet — that is the whole point of
    # declaring it in advance. See state_machine.declare_planned_stop.
    pending_stop_min: Mapped[int] = mapped_column(Integer, default=0)
    # Last reachability the network *pushed* to /hooks/reachability, and
    # when. Device Reachability Status is consumed in its subscription
    # form (docs/SignalGuard_Technical_Feasibility.pdf §2), so the current
    # answer normally arrives here on its own; holding it means the
    # escalation path can read it instead of spending a CAMARA call to ask
    # a question the network has already answered. None means nothing has
    # been pushed yet for this device — see state_machine._pushed_reachability
    # for why that falls back to the retrieve endpoint rather than to a guess.
    last_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_reachability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
    """A real CAMARA subscription created on Nokia's sandbox.

    Two kinds, both created at arm time: the pair of Geofencing
    Subscriptions that open and close the trip, and the Device
    Reachability Status subscription that reports the crossing's actual
    safety signal on change rather than being polled for it
    (docs/SignalGuard_Technical_Feasibility.pdf §2).
    """

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    traveller_id: Mapped[str] = mapped_column(ForeignKey("travellers.id"))
    # The reachability subscription is per-device, not per-zone; it carries
    # the zone it was armed alongside because arming is always per
    # (traveller, zone) and that is the row a renewal or teardown starts from.
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    gate: Mapped[str] = mapped_column(String)  # "entry" | "exit" | "reachability"
    nac_subscription_id: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Both indexed: state is filtered by tick() and active_trip_for() on
    # every poll from both the app and the dashboard, which made it the
    # hottest query in the backend and a full table scan without this.
    traveller_id: Mapped[str] = mapped_column(ForeignKey("travellers.id"), index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    state: Mapped[TripState] = mapped_column(Enum(TripState), default=TripState.IDLE, index=True)
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
    escalation_record: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Tier 0 (ask the device before you alarm a human) -----------------
    tier0_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tier0_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tier0_answer: Mapped[str | None] = mapped_column(String, nullable=True)  # "safe" | None

    # -- traveller-declared break ------------------------------------------
    # Minutes the traveller asked for before entering, or mid-crossing on a
    # brief reconnection. Additive to the agent's window, recorded
    # separately so the decision record can show the machine's judgement
    # and the human's override as two different things.
    planned_stop_min: Mapped[int] = mapped_column(Integer, default=0)

    # -- how this trip actually closed -------------------------------------
    # "reachability" is the primary signal (the phone came back), "gate" is
    # the geofence confirmation. A trip that leaves by a side road never
    # crosses the exit gate, so gate-only exit detection produces a false
    # Tier 1 — see state_machine.simulate_reachability.
    exit_signal: Mapped[str | None] = mapped_column(String, nullable=True)

    # -- Quality on Demand -------------------------------------------------
    # Spent on the *reconnection* edge, not at entry: a boost applied to a
    # handset that is about to lose signal buys nothing, while the first
    # seconds back online are when position upload and the all-clear
    # actually need the bandwidth.
    qod_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    qod_edge: Mapped[str | None] = mapped_column(String, nullable=True)  # "reconnect"
    qod_warranted: Mapped[bool] = mapped_column(Boolean, default=False)

    # -- convoy ------------------------------------------------------------
    convoy_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # -- learned corridor times feed --------------------------------------
    actual_crossing_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    traveller: Mapped[Traveller] = relationship(back_populates="trips")


class ApiLogEntry(Base):
    """Every real Nokia call, in the shape /demo/api-log needs."""

    __tablename__ = "api_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    api: Mapped[str] = mapped_column(String)
    endpoint: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    trip_id: Mapped[str | None] = mapped_column(String, nullable=True)


class CrossingHistory(Base):
    """One row per completed crossing — the feed for learned corridor times.

    `nominal_crossing_min` in the zone registry is a single hand-set
    constant (75 for Highway 15). It is the same number at 3am on an empty
    road and at 5pm in heavy traffic, which makes every monitoring window
    downstream of it a guess. This table is what turns that guess into an
    observation: after enough crossings, corridor_stats.py reads p50/p90
    per hour-of-day out of here and the agent gets a real prior instead.

    Written once, on the transition into EXITED/RESOLVED — never updated.
    """

    __tablename__ = "crossing_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    zone_id: Mapped[str] = mapped_column(String, index=True)
    hour_of_day: Mapped[int] = mapped_column(Integer, index=True)
    crossing_min: Mapped[int] = mapped_column(Integer)
    congestion_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    battery_band: Mapped[str | None] = mapped_column(String, nullable=True)
    # False when the trip escalated before it closed — a crossing that went
    # overdue is still a real duration measurement, but it is not evidence
    # of a *normal* crossing time and must not be allowed to drag the
    # learned baseline upward. corridor_stats.py reads clean rows only.
    clean: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CoverageObservation(Base):
    """A single (location, reachable?) reading, the raw material for
    auto-discovering dead zones instead of hand-seeding them.

    The zone registry today is one hardcoded Jordanian highway. But every
    crossing already makes a real Location Retrieval call and a real Device
    Reachability check — which means the network is continuously telling
    this system where its own coverage holes are, and the system currently
    throws that away. Each row is one such datapoint; coverage.py buckets
    them onto a coarse grid and surfaces any cell with a repeated,
    multi-traveller pattern of unreachability as a candidate zone.

    Deliberately NOT a location history of a person: rows carry no
    traveller id, and the grid cell is ~5.5km on a side (see
    coverage.GRID_DEG). See docs/SignalGuard_Security_Privacy §2 — this
    aggregates coverage, not people.
    """

    __tablename__ = "coverage_observations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    # Grid cell key, precomputed so the clustering query is a GROUP BY
    # rather than a full scan plus Python-side bucketing.
    cell: Mapped[str] = mapped_column(String, index=True)
    reachable: Mapped[bool] = mapped_column(Boolean)
    congestion_level: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
