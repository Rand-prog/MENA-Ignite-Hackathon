"""The single most important test in the repo. Escalation is timer-driven,
not model-driven — see docs/SignalGuard_Technical_Feasibility.pdf §5.4, §6.
This proves the alarm fires on schedule even with the agent's model
switched off, using the deterministic risk model underneath."""
from __future__ import annotations

import pytest

from app.agent.fallback import clamp_window, deterministic_fallback

ZONE_ID = "JO-H15-MUDAWWARA"


def test_deterministic_fallback_never_zero_window():
    d = deterministic_fallback(nominal_crossing_min=75, congestion_tier="light", battery_pct=82)
    assert d.monitoring_window_min > 0
    assert d.reasoning == ""
    assert "UNAVAILABLE" in d.model_used


def test_clamp_window_respects_floor_and_ceiling():
    assert clamp_window(0) >= 1
    assert clamp_window(10_000) < 10_000


def test_deterministic_fallback_wider_than_nominal_model_window():
    """The doc's own worked example: nominal 75, model ~92, deterministic
    105 — the fallback is conservative by construction (1.4x nominal)."""
    d = deterministic_fallback(nominal_crossing_min=75, congestion_tier="light", battery_pct=82)
    assert d.monitoring_window_min >= 100  # 75 * 1.4 == 105, clamps aside


@pytest.mark.asyncio
async def test_escalation_fires_with_model_disabled(client):
    """test_escalation_fires_with_model_disabled — the ten seconds of video
    that proves the safety claim. The LLM is disabled before the crossing;
    the deterministic risk model still sets a window, and the alarm still
    fires on the timer."""
    resp = await client.post("/demo/agent/model", json={"enabled": False})
    assert resp.json() == {"enabled": False}

    health = (await client.get("/healthz")).json()
    assert health["agent"]["enabled"] is False

    resp = await client.post("/demo/travellers", json={
        "msisdn": "+962790000099", "name": "TestTraveller",
        "contacts": [{"name": "Contact", "msisdn": "+962790000098"}],
    })
    traveller_id = resp.json()["traveller_id"]
    await client.post(f"/demo/zones/{ZONE_ID}/arm", json={"traveller_id": traveller_id})
    await client.post("/demo/battery", json={"traveller_id": traveller_id, "level": 18})

    await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    active = (await client.get("/demo/trips/active", params={"traveller_id": traveller_id})).json()
    assert active["state"] == "ACTIVE"
    trip_id = active["trip_id"]

    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["monitoring_window_min"] > 0
    assert "UNAVAILABLE" in trip["decision_record"]
    assert "deterministic risk model" in trip["decision_record"]
    window = trip["monitoring_window_min"]

    # device stays dark for the whole window plus margin — no reachability
    # signal is ever sent. The timer must fire the alarm on its own.
    await client.post("/demo/clock/advance", json={"seconds": (window + 5) * 60})

    trip = (await client.get(f"/trips/{trip_id}")).json()
    assert trip["state"] == "TIER1_ALERTED"
    assert "tier1" in trip["notifications"]
