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


async def test_dashboard_lists_active_trip_with_traveller_identity(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })

    resp = await client.get("/dashboard/trips")
    assert resp.status_code == 200
    trips = resp.json()
    assert len(trips) == 1
    trip = trips[0]
    assert trip["state"] == "ACTIVE"
    assert trip["traveller_name"] == "Sultan"
    assert trip["traveller_msisdn"] == "+962790000001"
    assert trip["elapsed_min"] is not None
    assert trip["zone_id"] == ZONE_ID


async def test_dashboard_clears_exited_trip_from_queue(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": traveller_id, "reachable": True,
    })

    resp = await client.get("/dashboard/trips")
    assert resp.json() == []


async def test_dashboard_resolve_clears_overdue_trip(client):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]
    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})

    resp = await client.post(f"/dashboard/trips/{trip_id}/resolve")
    assert resp.status_code == 200
    assert resp.json()["state"] == "RESOLVED"

    trips = (await client.get("/dashboard/trips")).json()
    assert trips == []
