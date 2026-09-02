"""WhatsApp fires at all three moments the user chose: dead-zone entry,
overdue, and safe exit — a deliberate deviation from the docs' silent
happy-path design (see app/notifications.py's docstring)."""
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


async def test_whatsapp_sent_on_entry(client, sent_whatsapp_messages):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    assert len(sent_whatsapp_messages) == 1
    assert sent_whatsapp_messages[0]["to"] == "+962790000002"
    assert "entered a low-coverage zone" in sent_whatsapp_messages[0]["body"]


async def test_whatsapp_sent_on_safe_exit(client, sent_whatsapp_messages):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    sent_whatsapp_messages.clear()  # only care about the exit message here

    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "exit",
    })
    assert len(sent_whatsapp_messages) == 1
    assert "made it through" in sent_whatsapp_messages[0]["body"]
    assert "safely" in sent_whatsapp_messages[0]["body"]


async def test_whatsapp_sent_on_overdue(client, sent_whatsapp_messages):
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip = (await client.get(f"/trips/{active['trip_id']}")).json()
    window = trip["monitoring_window_min"]
    sent_whatsapp_messages.clear()  # only care about the overdue message here

    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})

    assert len(sent_whatsapp_messages) == 1
    assert "hasn't reconnected" in sent_whatsapp_messages[0]["body"]


async def test_no_duplicate_overdue_message_on_repeated_polling(client, sent_whatsapp_messages):
    """tick() runs on every trip read — the overdue message must fire once,
    not once per poll."""
    traveller_id = await _enroll_and_arm(client)
    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    trip_id = active["trip_id"]
    trip = (await client.get(f"/trips/{trip_id}")).json()
    window = trip["monitoring_window_min"]
    sent_whatsapp_messages.clear()

    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})
    for _ in range(3):
        await client.get(f"/trips/{trip_id}")

    overdue_msgs = [m for m in sent_whatsapp_messages if "hasn't reconnected" in m["body"]]
    assert len(overdue_msgs) == 1
