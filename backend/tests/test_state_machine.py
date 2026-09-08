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
    resp = await client.post(f"/demo/zones/{ZONE_ID}/arm", json={"traveller_id": traveller_id})
    assert resp.status_code == 200
    assert "entry_subscription_id" in resp.json()
    return traveller_id


async def test_happy_path_closes_silently(client):
    traveller_id = await _enroll_and_arm(client)

    resp = await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    assert resp.status_code == 200

    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    assert active["state"] == "ACTIVE"
    trip_id = active["trip_id"]

    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["monitoring_window_min"] > 0
    assert trip["decision_record"]
    assert trip["notifications"] == []

    # reconnect well within the window
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": traveller_id, "reachable": True,
    })
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "EXITED"
    assert trip["notifications"] == []  # nobody was notified


async def test_overdue_escalates_through_both_tiers(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/battery", json={"traveller_id": traveller_id, "level": 18})
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

    await client.post("/demo/clock/advance", json={"seconds": 20 * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER2_ESCALATED"
    assert "tier2" in trip["notifications"]


async def test_stand_down_suppresses_tier2(client):
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

    await client.post("/demo/contact-reply", json={"trip_id": trip_id, "reply": "safe"})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "RESOLVED"

    # even after the grace window elapses, tier 2 must never fire
    await client.post("/demo/clock/advance", json={"seconds": 30 * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "RESOLVED"
    assert "tier2" not in trip["notifications"]


async def test_zones_endpoint_lists_seeded_zone(client):
    resp = await client.get("/zones")
    assert resp.status_code == 200
    zones = resp.json()
    assert len(zones) == 1
    assert zones[0]["zone_id"] == ZONE_ID
    assert "entry_gate" in zones[0] and "exit_gate" in zones[0]


async def test_duplicate_msisdn_registration_attaches_in_demo_mode(client):
    """In demo mode (see conftest — every test runs with this on), a second
    registration for the same msisdn attaches to the existing traveller
    instead of bouncing with 409 — the conductor and a test app sharing one
    fixed identity is a normal workflow, not an error. See real.py's
    register_traveller for the non-demo-mode strict 409 this skips."""
    body = {
        "msisdn": "+962790000099", "name": "Sultan",
        "contacts": [{"name": "Omar", "msisdn": "+962790000002"}],
    }
    first = await client.post("/travellers", json=body)
    assert first.status_code == 200

    second = await client.post("/travellers", json=body)
    assert second.status_code == 200
    assert second.json()["traveller_id"] == first.json()["traveller_id"]
    assert second.json()["auth_token"] == first.json()["auth_token"]


async def test_exit_gate_while_overdue_resolves_not_stuck(client):
    """Regression: a traveller who's already overdue (Tier 1 or Tier 2)
    still eventually drives out the exit gate in the ordinary case — that
    must clear the escalation, not get silently ignored."""
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]

    # push all the way through to TIER2_ESCALATED
    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})
    await client.post("/demo/clock/advance", json={"seconds": 20 * 60})
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER2_ESCALATED"

    # traveller was just running late — they drive out the exit gate for real
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "exit",
    })
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "RESOLVED"


async def test_reachability_while_tier2_escalated_resolves_not_stuck(client):
    """Same bug, the other trigger path: a reachability signal (not a gate
    crossing) arriving after Tier 2 must also clear the trip."""
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

    await client.post("/demo/simulate-reachability", json={
        "traveller_id": traveller_id, "reachable": True,
    })
    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "RESOLVED"
