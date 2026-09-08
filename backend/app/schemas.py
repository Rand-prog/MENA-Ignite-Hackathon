from __future__ import annotations

from pydantic import BaseModel, Field


# Names are rendered into the emergency-centre dashboard. They are
# escaped there (dashboard/js/app.js builds rows with textContent, never
# innerHTML) — this bound is the second layer, not the first: a value that
# is never long enough to be a payload is one less thing depending on
# every future render site getting escaping right.
_NAME = Field(min_length=1, max_length=80)
_MSISDN = Field(min_length=3, max_length=20)


class ContactIn(BaseModel):
    name: str = _NAME
    msisdn: str = _MSISDN


class TravellerIn(BaseModel):
    msisdn: str = _MSISDN
    name: str = _NAME
    contacts: list[ContactIn] = []


class ArmZoneIn(BaseModel):
    traveller_id: str


class SimulateGateEventIn(BaseModel):
    traveller_id: str
    zone_id: str
    gate: str  # "entry" | "exit"


class SimulateReachabilityIn(BaseModel):
    traveller_id: str
    reachable: bool


class BatteryIn(BaseModel):
    traveller_id: str
    level: int


class AgentModelIn(BaseModel):
    enabled: bool


class ContactReplyIn(BaseModel):
    trip_id: str
    reply: str = "safe"


class ClockAdvanceIn(BaseModel):
    seconds: int


class BatteryReportIn(BaseModel):
    level: int


class PlannedStopIn(BaseModel):
    """A traveller-declared break. Clamped server-side too (see
    state_machine.declare_planned_stop) — this bound is a courtesy to the
    client, not the guard."""

    minutes: int


class Tier0ResponseIn(BaseModel):
    answer: str = "safe"


class PromoteCandidateIn(BaseModel):
    zone_id: str
    label: str | None = None
    gate_radius_m: int = 8000
    corridor_km: int = 50
    nominal_crossing_min: int = 40
