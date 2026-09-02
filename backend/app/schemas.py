from __future__ import annotations

from pydantic import BaseModel


class ContactIn(BaseModel):
    name: str
    msisdn: str


class TravellerIn(BaseModel):
    msisdn: str
    name: str
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
