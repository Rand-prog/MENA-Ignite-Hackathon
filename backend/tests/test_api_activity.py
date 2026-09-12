"""The operator console's view of the CAMARA call layer (api_activity.py).

Two things are worth a test here. The first is attribution: a call row that
names the wrong trip, or no trip, turns the detail panel's provenance list
into a confident lie — which is worse than not having the list, because a
dispatcher would be reading calls that belong to somebody else's crossing.
The second is the zero-call case: the panel must still list all five APIs,
since "not called yet" and "failing" are different facts and an absent row
states neither.

These drive NokiaClient._call for real (through a MockTransport) rather
than the stubbed methods conftest installs — the logging happens inside
`_call`, so a test that stubs above it would assert nothing about the thing
it claims to cover.
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from sqlalchemy import select

from app.api_activity import attributed_to, recent, summary
from app.db import SessionLocal
from app.models import ApiLogEntry
from app.nac_singleton import _log_to_db
from app.nokia_client import NokiaClient


def _client(handler) -> NokiaClient:
    nac = NokiaClient(log_fn=_log_to_db)
    nac._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://nac.test"
    )
    return nac


async def _rows() -> list[ApiLogEntry]:
    async with SessionLocal() as session:
        result = await session.execute(select(ApiLogEntry).order_by(ApiLogEntry.ts))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_a_call_row_names_the_trip_it_was_made_for(client):
    nac = _client(lambda req: httpx.Response(200, json={"reachable": True, "connectivity": ["DATA"]}))
    try:
        with attributed_to("trip-abc"):
            await nac.retrieve_reachability(device_phone="+123")
        # Outside the context: arming a zone's gates happens before any trip
        # exists, and claiming one would be an invention.
        await nac.retrieve_reachability(device_phone="+123")
    finally:
        await nac.aclose()

    rows = await _rows()
    assert [r.trip_id for r in rows] == ["trip-abc", None]
    assert rows[0].api == "Device Reachability Status"
    assert rows[0].status == 200


@pytest.mark.asyncio
async def test_every_camara_api_is_listed_before_it_is_ever_called(client):
    async with SessionLocal() as session:
        data = await summary(session)
    listed = [a["api"] for a in data["apis"]]
    assert listed == [
        "Geofencing Subscriptions",
        "Location Retrieval",
        "Device Reachability Status",
        "Congestion Insights",
        "Quality on Demand",
    ]
    assert all(a["calls"] == 0 and a["last_status"] is None for a in data["apis"])
    assert data["totals"] == {
        "calls": 0, "ok": 0, "failed": 0, "skipped": 0, "cost_usd": 0.0,
        "apis_used": 0, "apis_total": 5,
    }


@pytest.mark.asyncio
async def test_a_rejected_call_and_an_unreachable_api_are_not_the_same_row(client):
    """A 4xx and a connection that never landed need different actions, so
    they must not both render as "failed" with no way to tell which."""
    nac = _client(lambda req: httpx.Response(403, json={"message": "forbidden"}))
    try:
        await nac.retrieve_location(device_phone="+123")
    finally:
        await nac.aclose()

    def _boom(req):
        raise httpx.ConnectError("no route to host", request=req)

    nac = _client(_boom)
    try:
        with pytest.raises(Exception):
            await nac.query_congestion(device_phone="+123", webhook_url="https://x.test/h")
    finally:
        await nac.aclose()

    async with SessionLocal() as session:
        data = await summary(session)
        calls = await recent(session, limit=10)

    by_api = {a["api"]: a for a in data["apis"]}
    assert by_api["Location Retrieval"]["last_status"] == 403
    assert by_api["Congestion Insights"]["last_status"] == 0
    assert data["totals"]["failed"] == 2 and data["totals"]["ok"] == 0

    statuses = {c["api"]: (c["status"], c["ok"]) for c in calls}
    assert statuses["Location Retrieval"] == (403, False)
    # Status 0 is the marker for "never got an HTTP response at all"; the
    # console renders it as ERR rather than as a status code.
    assert statuses["Congestion Insights"] == (0, False)


@pytest.mark.asyncio
async def test_the_console_endpoint_serves_the_roll_up_and_the_feed(client):
    resp = await client.get("/dashboard/api-activity?limit=5&hours=6")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 6
    assert len(body["apis"]) == 5
    assert body["calls"] == []
    assert body["totals"]["apis_total"] == 5


@pytest.mark.asyncio
async def test_a_trips_detail_carries_the_calls_made_for_it(client):
    """The provenance list is per-trip, so it has to filter by trip — a
    panel that showed the whole log would attribute every crossing's calls
    to whichever one the dispatcher happened to open."""
    zone_id = "JO-H15-MUDAWWARA"
    reg = await client.post("/demo/travellers", json={
        "msisdn": "+962790000001", "name": "Attribution Test",
        "contacts": [{"name": "Contact", "msisdn": "+962790000002"}],
    })
    assert reg.status_code == 200
    traveller_id = reg.json()["traveller_id"]
    await client.post(f"/demo/zones/{zone_id}/arm", json={"traveller_id": traveller_id})
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": zone_id, "gate": "entry",
    })

    trips = (await client.get("/dashboard/trips")).json()
    assert trips, "a gate crossing should put a trip in the queue"
    trip_id = trips[0]["trip_id"]

    # conftest stubs the client's methods above `_call`, so no rows exist
    # for this crossing — the contract under test is that the field is
    # present, per-trip and empty, not that the stub logged anything.
    detail = (await client.get(f"/dashboard/trips/{trip_id}")).json()
    assert detail["api_calls"] == []

    async with SessionLocal() as session:
        session.add(ApiLogEntry(
            ts=datetime.utcnow(), api="Location Retrieval",
            endpoint="POST /location-retrieval/v0/retrieve", latency_ms=204,
            status=200, cost_usd=0.01, trip_id=trip_id,
        ))
        session.add(ApiLogEntry(
            ts=datetime.utcnow(), api="Location Retrieval",
            endpoint="POST /location-retrieval/v0/retrieve", latency_ms=198,
            status=200, cost_usd=0.01, trip_id="some-other-trip",
        ))
        await session.commit()

    detail = (await client.get(f"/dashboard/trips/{trip_id}")).json()
    assert [c["trip_id"] for c in detail["api_calls"]] == [trip_id]
    assert detail["api_calls"][0]["method"] == "POST"
    assert detail["api_calls"][0]["path"] == "/location-retrieval/v0/retrieve"
    assert detail["api_calls"][0]["camara"] is True
