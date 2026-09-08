"""Tests for the rungs added below and around the original ladder.

The one that matters most here is test_tier0_cannot_suppress_an_alarm.
Everything else in this file is a feature; that one is the safety claim.
Tier 0 delays an escalation by design, and a delay mechanism with a bug in
it is indistinguishable from a suppression mechanism — so it gets a test
that proves the alarm still lands when nobody answers.
"""
from __future__ import annotations

import pytest

from app.clock import clock
from app.config import settings
from app.zones import ZONE_SEEDS

ZONE = ZONE_SEEDS[0].zone_id


async def _register(client, *, msisdn="+962790000001", name="Sultan"):
    resp = await client.post("/demo/travellers", json={
        "msisdn": msisdn, "name": name,
        "contacts": [{"name": "Omar", "msisdn": "+962790000002"}],
    })
    assert resp.status_code == 200
    return resp.json()["traveller_id"]


async def _register_real(client, *, msisdn="+962790000010", name="Lina"):
    """Registration through the product surface, which returns a token —
    the demo surface deliberately doesn't."""
    resp = await client.post("/travellers", json={
        "msisdn": msisdn, "name": name,
        "contacts": [{"name": "Rami", "msisdn": "+962790000011"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    return body["traveller_id"], {"Authorization": f"Bearer {body['auth_token']}"}


async def _enter(client, traveller_id):
    resp = await client.post("/demo/simulate-gate-event", json={
        "traveller_id": traveller_id, "zone_id": ZONE, "gate": "entry",
    })
    assert resp.status_code == 200
    trip = (await client.get(f"/demo/trips/active?traveller_id={traveller_id}")).json()
    return trip["trip_id"]


async def _advance(client, seconds):
    resp = await client.post("/demo/clock/advance", json={"seconds": seconds})
    assert resp.status_code == 200


async def _trip(client, trip_id):
    resp = await client.get(f"/trips/{trip_id}")
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture(autouse=True)
def _reset_clock():
    clock.reset()
    yield
    clock.reset()


# -- Tier 0 ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier0_is_entered_when_the_handset_is_reachable(client):
    """Window expires, network says the phone is back -> ask the phone,
    tell nobody."""
    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    # Just past the monitoring window, but not past the Tier 0 grace.
    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    trip = await _trip(client, trip_id)

    assert trip["state"] == "TIER0_CHECKING"
    # The whole point: no human has been told anything.
    assert "tier1" not in trip["notifications"]
    assert "tier2" not in trip["notifications"]


@pytest.mark.asyncio
async def test_tier0_answer_closes_the_trip_with_nobody_contacted(client):
    tid, headers = await _register_real(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    assert (await _trip(client, trip_id))["state"] == "TIER0_CHECKING"

    resp = await client.post(
        "/travellers/me/tier0-response", json={"answer": "safe"}, headers=headers,
    )
    assert resp.status_code == 200

    trip = await _trip(client, trip_id)
    assert trip["state"] == "EXITED"
    assert "tier1" not in trip["notifications"]
    assert trip["exit_signal"] == "tier0_self_report"


@pytest.mark.asyncio
async def test_tier0_cannot_suppress_an_alarm(client):
    """THE test in this file.

    Tier 0 delays escalation. If it can delay it indefinitely — a traveller
    who never answers, an app that never polls, a bug in the deadline
    arithmetic — then it is not a delay, it is a way for the alarm to never
    fire, and the entire safety claim of the product goes with it.

    Nobody answers here. Tier 1 must still land."""
    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    await _advance(client, trip["monitoring_window_min"] * 60 + settings.tier0_grace_sec + 5)
    trip = await _trip(client, trip_id)

    assert trip["state"] == "TIER1_ALERTED"
    assert "tier1" in trip["notifications"]


@pytest.mark.asyncio
async def test_tier0_still_reaches_tier2_on_the_original_ladder(client):
    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    await _advance(
        client,
        trip["monitoring_window_min"] * 60
        + settings.tier0_grace_sec
        + settings.tier1_grace_min * 60
        + 5,
    )
    trip = await _trip(client, trip_id)
    assert trip["state"] == "TIER2_ESCALATED"
    assert "tier2" in trip["notifications"]


# -- planned stop ------------------------------------------------------------

@pytest.mark.asyncio
async def test_planned_stop_declared_before_entry_widens_the_window(client):
    tid, headers = await _register_real(client, msisdn="+962790000020", name="Rami")

    resp = await client.post(
        "/travellers/me/planned-stop", json={"minutes": 45}, headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["applied_to"] == "next_trip"

    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)
    assert trip["planned_stop_min"] == 45

    # Past the agent's own window, but inside the declared break: still
    # ACTIVE, nobody alarmed.
    await _advance(client, trip["monitoring_window_min"] * 60 + 60)
    trip = await _trip(client, trip_id)
    assert trip["state"] == "ACTIVE"
    assert trip["notifications"] == []


@pytest.mark.asyncio
async def test_planned_stop_is_clamped(client):
    _, headers = await _register_real(client, msisdn="+962790000021", name="Dana")
    resp = await client.post(
        "/travellers/me/planned-stop", json={"minutes": 99999}, headers=headers,
    )
    assert resp.json()["planned_stop_min"] == settings.planned_stop_max_min


@pytest.mark.asyncio
async def test_planned_stop_mid_trip_pulls_a_trip_back_out_of_tier0(client):
    """The traveller answers the Tier 0 ping with "I'm stopping" rather
    than "I'm done" — the trip should go back to being in progress, not
    close."""
    tid, headers = await _register_real(client, msisdn="+962790000022", name="Yara")
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    assert (await _trip(client, trip_id))["state"] == "TIER0_CHECKING"

    resp = await client.post(
        "/travellers/me/planned-stop", json={"minutes": 60}, headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["applied_to"] == "trip"

    trip = await _trip(client, trip_id)
    assert trip["state"] == "ACTIVE"
    assert trip["notifications"] == ["tier0"]


# -- exit signals ------------------------------------------------------------

@pytest.mark.asyncio
async def test_reachability_is_the_primary_exit_signal(client):
    """A traveller who leaves by a side road never crosses the exit gate."""
    tid = await _register(client)
    trip_id = await _enter(client, tid)

    resp = await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": True,
    })
    assert resp.status_code == 200

    trip = await _trip(client, trip_id)
    assert trip["state"] == "EXITED"
    assert trip["exit_signal"] == "reachability"


@pytest.mark.asyncio
async def test_going_dark_is_not_a_state_change(client):
    tid = await _register(client)
    trip_id = await _enter(client, tid)

    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": False,
    })
    trip = await _trip(client, trip_id)
    assert trip["state"] == "ACTIVE"


# -- QoD on the reconnection edge -------------------------------------------

@pytest.mark.asyncio
async def test_qod_is_not_spent_at_entry(client):
    """A boost applied to a handset that is seconds from losing signal
    buys nothing. The judgement is recorded at entry; the session is not."""
    tid = await _register(client)
    await client.post("/demo/battery", json={"traveller_id": tid, "level": 15})
    trip_id = await _enter(client, tid)

    trip = await _trip(client, trip_id)
    assert trip["qod_warranted"] is True
    assert trip["qod_edge"] is None
    assert "QoD warranted" in trip["decision_record"]


@pytest.mark.asyncio
async def test_qod_is_spent_on_reconnection(client):
    tid = await _register(client)
    await client.post("/demo/battery", json={"traveller_id": tid, "level": 15})
    trip_id = await _enter(client, tid)

    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": True,
    })
    trip = await _trip(client, trip_id)
    assert trip["qod_edge"] == "reconnect"


# -- learned corridor times --------------------------------------------------

@pytest.mark.asyncio
async def test_completed_crossings_build_corridor_history(client):
    """Three clean crossings entering at the same hour of day fill one
    bucket and put it into use.

    The 24-hour spacing is not incidental: buckets are keyed on hour of
    day, so three crossings 40 minutes apart would land in three different
    buckets and none of them would reach the sample threshold. That is the
    honest cost of hour-bucketing — history accumulates per hour, not per
    corridor — and it is why corridor_stats falls back to the registry
    nominal rather than pooling hours together to reach a number sooner."""
    tid = await _register(client)
    for _ in range(3):
        trip_id = await _enter(client, tid)
        await _advance(client, 10 * 60)
        await client.post("/demo/simulate-reachability", json={
            "traveller_id": tid, "reachable": True,
        })
        assert (await _trip(client, trip_id))["actual_crossing_min"] == 10
        # Back round to the same hour of day for the next crossing.
        await _advance(client, 24 * 3600 - 10 * 60)

    stats = (await client.get(f"/dashboard/zones/{ZONE}/stats")).json()
    assert stats["clean_crossings"] == 3
    in_use = [b for b in stats["buckets"] if b["in_use"]]
    assert len(in_use) == 1
    assert in_use[0]["samples"] == 3
    assert in_use[0]["p50_min"] == 10


@pytest.mark.asyncio
async def test_history_below_the_sample_floor_is_recorded_but_not_used(client):
    """Two crossings is not a statistic. The bucket exists and is
    reportable, but the agent keeps planning against the nominal."""
    tid = await _register(client)
    for _ in range(2):
        await _enter(client, tid)
        await _advance(client, 10 * 60)
        await client.post("/demo/simulate-reachability", json={
            "traveller_id": tid, "reachable": True,
        })
        await _advance(client, 24 * 3600 - 10 * 60)

    stats = (await client.get(f"/dashboard/zones/{ZONE}/stats")).json()
    assert stats["clean_crossings"] == 2
    assert all(not b["in_use"] for b in stats["buckets"])

    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)
    assert "none yet" in trip["decision_record"]


@pytest.mark.asyncio
async def test_learned_history_reaches_the_decision_record(client):
    tid = await _register(client)
    for _ in range(3):
        await _enter(client, tid)
        await _advance(client, 10 * 60)
        await client.post("/demo/simulate-reachability", json={
            "traveller_id": tid, "reachable": True,
        })
        await _advance(client, 24 * 3600 - 10 * 60)

    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)
    assert "3 past crossings at this hour" in trip["decision_record"]
    assert "get_corridor_history" in trip["decision_record"]


@pytest.mark.asyncio
async def test_escalated_crossings_do_not_pollute_the_baseline(client):
    """A crossing that went overdue is a real duration but not evidence of
    a normal one. Counting it would make the system less likely to alarm
    the more often it had to — exactly backwards."""
    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)
    await _advance(client, trip["monitoring_window_min"] * 60 + settings.tier0_grace_sec + 5)
    assert (await _trip(client, trip_id))["state"] == "TIER1_ALERTED"

    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": True,
    })
    assert (await _trip(client, trip_id))["state"] == "RESOLVED"

    stats = (await client.get(f"/dashboard/zones/{ZONE}/stats")).json()
    assert stats["total_crossings"] == 1
    assert stats["clean_crossings"] == 0


# -- convoy ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_travellers_in_the_same_window_form_a_convoy(client):
    a = await _register(client, msisdn="+962790000030", name="A")
    b = await _register(client, msisdn="+962790000031", name="B")
    await _enter(client, a)
    await _enter(client, b)

    trips = (await client.get("/dashboard/trips")).json()
    convoys = {t["convoy_id"] for t in trips}
    assert len(trips) == 2
    assert len(convoys) == 1 and None not in convoys


@pytest.mark.asyncio
async def test_convoy_peer_who_already_exited_lands_in_the_escalation_record(client):
    a = await _register(client, msisdn="+962790000040", name="A")
    b = await _register(client, msisdn="+962790000041", name="B")
    await _enter(client, a)
    b_trip = await _enter(client, b)

    # A gets out; B goes overdue.
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": a, "reachable": True,
    })
    trip = await _trip(client, b_trip)
    await _advance(client, trip["monitoring_window_min"] * 60 + settings.tier0_grace_sec + 5)

    trip = await _trip(client, b_trip)
    assert trip["state"] == "TIER1_ALERTED"
    assert "escalation convoy" in (trip["escalation_record"] or "")


# -- auto-discovered zones ---------------------------------------------------

@pytest.mark.asyncio
async def test_a_registered_corridor_is_never_offered_as_a_candidate(client):
    """The fake location fixture returns the registered zone's own entry
    gate, so every observation lands inside a known corridor. Rediscovering
    Highway 15 is noise, not a finding."""
    tid = await _register(client)
    for _ in range(5):
        await _enter(client, tid)
        await client.post("/demo/simulate-reachability", json={
            "traveller_id": tid, "reachable": False,
        })
        await client.post("/demo/simulate-reachability", json={
            "traveller_id": tid, "reachable": True,
        })

    candidates = (await client.get("/zones/candidates")).json()
    assert candidates == []


@pytest.mark.asyncio
async def test_promoting_a_candidate_creates_a_real_zone(client):
    resp = await client.post(
        "/zones/candidates/595:719/promote",
        json={"zone_id": "AUTO-TEST-1", "corridor_km": 40, "nominal_crossing_min": 30},
    )
    assert resp.status_code == 200
    zones = (await client.get("/zones")).json()
    ids = {z["zone_id"] for z in zones}
    assert "AUTO-TEST-1" in ids
    promoted = next(z for z in zones if z["zone_id"] == "AUTO-TEST-1")
    # Never silently presented as surveyed.
    assert "unsurveyed" in promoted["label"]


# -- position uncertainty ----------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_reports_a_position_range_not_a_point(client):
    tid = await _register(client)
    await _enter(client, tid)
    await _advance(client, 30 * 60)

    trips = (await client.get("/dashboard/trips")).json()
    est = trips[0]["position_estimate"]
    assert est is not None
    assert est["progress_min"] < est["progress_max"]
    assert est["spread_km"] > 0


@pytest.mark.asyncio
async def test_uncertainty_widens_with_time(client):
    tid = await _register(client)
    await _enter(client, tid)

    await _advance(client, 10 * 60)
    early = (await client.get("/dashboard/trips")).json()[0]["position_estimate"]
    await _advance(client, 30 * 60)
    later = (await client.get("/dashboard/trips")).json()[0]["position_estimate"]

    assert later["spread_km"] > early["spread_km"]


@pytest.mark.asyncio
async def test_best_guess_stops_claiming_meaning_once_overdue(client):
    tid = await _register(client)
    await _enter(client, tid)
    trips = (await client.get("/dashboard/trips")).json()
    predicted = trips[0]["predicted_crossing_min"]

    await _advance(client, (predicted + 30) * 60)
    est = (await client.get("/dashboard/trips")).json()[0]["position_estimate"]
    assert est["best_guess_meaningful"] is False


# -- history view ------------------------------------------------------------

@pytest.mark.asyncio
async def test_closed_trips_leave_the_queue_and_land_in_history(client):
    tid = await _register(client)
    await _enter(client, tid)
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": True,
    })

    assert (await client.get("/dashboard/trips")).json() == []
    history = (await client.get("/dashboard/history")).json()
    assert len(history) == 1
    assert history[0]["state"] == "EXITED"


# -- battery reporting -------------------------------------------------------

@pytest.mark.asyncio
async def test_battery_reports_in_the_body(client):
    _, headers = await _register_real(client, msisdn="+962790000050", name="Nour")
    resp = await client.post(
        "/travellers/me/battery", json={"level": 42}, headers=headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_battery_query_param_still_accepted(client):
    """An app build from before the change must not silently stop
    reporting — a battery report that never arrives looks, to the risk
    model, like a phone that never had a low battery."""
    _, headers = await _register_real(client, msisdn="+962790000051", name="Zaid")
    resp = await client.post("/travellers/me/battery?level=42", headers=headers)
    assert resp.status_code == 200


# -- input bounds ------------------------------------------------------------

@pytest.mark.asyncio
async def test_traveller_name_is_length_bounded(client):
    resp = await client.post("/travellers", json={
        "msisdn": "+962790000060", "name": "x" * 500, "contacts": [],
    })
    assert resp.status_code == 422


# -- Device Reachability Status: the subscription form -----------------------
#
# docs/SignalGuard_Technical_Feasibility.pdf §2 is explicit that reachability
# is consumed "on change, via subscription" rather than polled, and §8 prices
# it at ~0 per crossing on that basis. These cover both halves of that: the
# subscription is really created, and a pushed answer is used instead of a
# call.

@pytest.mark.asyncio
async def test_arming_creates_a_real_reachability_subscription(client):
    tid = await _register(client)
    resp = await client.post(f"/demo/zones/{ZONE}/arm", json={"traveller_id": tid})
    assert resp.status_code == 200
    body = resp.json()

    # The two contract keys are untouched.
    assert body["entry_subscription_id"]
    assert body["exit_subscription_id"]
    # And the third subscription the docs actually run the safety signal on.
    assert body["reachability_subscription_id"] == "sub_reach_fake"


@pytest.mark.asyncio
async def test_reachability_subscription_body_matches_nokias_reference(client, monkeypatch):
    """The catalog's sample omits `protocol` and the device, so a body built
    from it would never say which line to watch."""
    from app import nac_singleton

    captured: dict = {}

    async def _capture(**kw):
        captured.update(kw)
        return {"id": "sub_reach_captured"}

    monkeypatch.setattr(nac_singleton.nac_client, "create_reachability_subscription", _capture)

    tid = await _register(client)
    await client.post(f"/demo/zones/{ZONE}/arm", json={"traveller_id": tid})

    assert captured["device_phone"] == settings.nac_device
    assert captured["sink"].endswith("/hooks/reachability")


@pytest.mark.asyncio
async def test_a_pushed_dark_handset_skips_tier0_without_a_camara_call(client, monkeypatch):
    """A push that says the line is unreachable is the answer. Spending a
    retrieve to ask again delays the alarm by the Tier 0 grace for nothing.

    Asserted against the Tier 0 gate itself rather than a count of retrieve
    calls: the escalation agent makes its own, deliberate reachability call
    while composing the Tier 1 record (agent/escalation_graph.py), and that
    one is not what this is about.
    """
    from app import state_machine as sm

    probes = 0
    real_probe = sm._handset_reachable

    async def _counting_probe(*a, **kw):
        nonlocal probes
        probes += 1
        return await real_probe(*a, **kw)

    monkeypatch.setattr(sm, "_handset_reachable", _counting_probe)

    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    # The subscription pushes "gone dark" as the traveller enters the corridor.
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": False,
    })

    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    trip = await _trip(client, trip_id)

    assert trip["state"] in ("OVERDUE", "TIER1_ALERTED")
    assert "tier0" not in trip["notifications"]
    assert probes == 0, "pushed state should have cost no CAMARA call"


@pytest.mark.asyncio
async def test_no_push_falls_back_to_the_retrieve_endpoint(client):
    """The redundancy path: a lapsed subscription or a lost notification
    must not be able to hold Tier 0 shut."""
    tid = await _register(client)
    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)

    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    trip = await _trip(client, trip_id)

    # conftest's fake retrieve answers CONNECTED_DATA, so Tier 0 is offered.
    assert trip["state"] == "TIER0_CHECKING"


@pytest.mark.asyncio
async def test_a_stale_push_from_before_the_crossing_is_not_trusted(client):
    """A notification from before the trip opened describes a device that
    was on a road with coverage. Reading it as current would offer Tier 0
    to a handset that is dark."""
    tid = await _register(client)

    # Pushed before any trip exists.
    await client.post("/demo/simulate-reachability", json={
        "traveller_id": tid, "reachable": True,
    })

    trip_id = await _enter(client, tid)
    trip = await _trip(client, trip_id)
    await _advance(client, trip["monitoring_window_min"] * 60 + 10)
    trip = await _trip(client, trip_id)

    # Fell through to the retrieve rather than trusting the stale push.
    assert trip["state"] == "TIER0_CHECKING"
