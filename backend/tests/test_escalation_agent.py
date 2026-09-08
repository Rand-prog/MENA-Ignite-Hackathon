"""Proves the escalation agent (agent/escalation_graph.py) actually runs at
Tier 1 and Tier 2 — real tool calls (check_device_reachability, then
notify_contact / escalate_to_dashboard), not just the state machine's
hardcoded list-append. See state_machine.py's _run_escalation for how the
guaranteed fallback and the agent enrichment relate: the tier still fires
even if this agent fails, but when it succeeds it leaves a real trace.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ZONE_ID = "JO-H15-MUDAWWARA"


async def _enroll_and_arm(client) -> str:
    resp = await client.post("/demo/travellers", json={
        "msisdn": "+962790000001", "name": "Sultan",
        "contacts": [{"name": "Omar", "msisdn": "+962790000002"}],
    })
    traveller_id = resp.json()["traveller_id"]
    await client.post(f"/demo/zones/{ZONE_ID}/arm", json={"traveller_id": traveller_id})
    return traveller_id


async def test_tier1_escalation_record_shows_real_tool_orchestration(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]

    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER1_ALERTED"

    record = trip["escalation_record"]
    assert record is not None
    assert "escalation tier1" in record
    assert "check_device_reachability" in record
    assert "notify_contact" in record
    # conftest's fake retrieve_reachability always answers CONNECTED_DATA —
    # a real crossing that fired Tier 1 anyway is exactly the race case the
    # conditional edge in escalation_graph.py flags, without suppressing it.
    assert "CONNECTED_DATA" in record
    assert "possibly reconnected" in record


async def test_tier2_escalation_record_appends_a_second_block(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]

    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})
    await client.post("/demo/clock/advance", json={"seconds": 20 * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER2_ESCALATED"

    record = trip["escalation_record"]
    assert "escalation tier1" in record
    assert "escalation tier2" in record
    assert "escalate_to_dashboard" in record
    # both blocks present, tier1's block still there — appended, not overwritten
    assert record.index("escalation tier1") < record.index("escalation tier2")


async def test_escalation_agent_failure_still_fires_the_alarm(client, monkeypatch):
    """The guarantee that actually matters: if the escalation agent blows up
    entirely (Nokia down, whatever), the tier must still be recorded on
    trip.notifications — state_machine._run_escalation's fallback, not the
    agent, is the thing this test is really pinning down."""
    from app import nac_singleton

    async def _boom(**kw):
        raise RuntimeError("sandbox unreachable")

    monkeypatch.setattr(nac_singleton.nac_client, "retrieve_reachability", _boom)

    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]

    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER1_ALERTED"
    assert "tier1" in trip["notifications"]
