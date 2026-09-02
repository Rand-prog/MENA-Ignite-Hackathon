from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# Env vars must be set before anything under app/ is imported, since
# Settings() reads them at import time.
_tmp_db = Path(tempfile.gettempdir()) / "signalguard_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.as_posix()}"
os.environ.setdefault("SIGNALGUARD_DEMO_MODE", "true")
os.environ.setdefault("NAC_API_KEY", "")
os.environ.setdefault("GOOGLE_API_KEY", "")  # forces the deterministic fallback by default

import httpx  # noqa: E402

from app.db import reset_db  # noqa: E402
from app.main import app  # noqa: E402
from app.nac_singleton import nac_client  # noqa: E402
from app.whatsapp_singleton import whatsapp_client  # noqa: E402
from app.zones import ZONE_SEEDS  # noqa: E402


async def _fake_create_geofence_subscription(**kw):
    return {"id": f"sub_{kw.get('lat')}_{kw.get('lon')}"}


async def _fake_retrieve_location(**kw):
    z = ZONE_SEEDS[0]
    return {
        "lastLocationTime": "2026-08-30T12:00:00Z",
        "area": {"areaType": "CIRCLE",
                 "center": {"latitude": z.entry_lat, "longitude": z.entry_lon},
                 "radius": 1000},
    }


async def _fake_query_congestion(**kw):
    return {"congestionLevel": "light"}


async def _fake_create_qod_session(**kw):
    return {"sessionId": "qod_fake_session", "qosStatus": "REQUESTED"}


async def _fake_retrieve_reachability(**kw):
    return {"connectivityStatus": "CONNECTED_DATA"}


@pytest.fixture(autouse=True)
def _patch_nokia(monkeypatch):
    """No test should ever hit the real Nokia sandbox. Every real-call
    seam on the process-wide client is stubbed with canned CAMARA-shaped
    responses so the state machine and agent are exercised for real."""
    monkeypatch.setattr(nac_client, "create_geofence_subscription", _fake_create_geofence_subscription)
    monkeypatch.setattr(nac_client, "retrieve_location", _fake_retrieve_location)
    monkeypatch.setattr(nac_client, "query_congestion", _fake_query_congestion)
    monkeypatch.setattr(nac_client, "create_qod_session", _fake_create_qod_session)
    monkeypatch.setattr(nac_client, "retrieve_reachability", _fake_retrieve_reachability)
    yield


@pytest.fixture
def sent_whatsapp_messages(monkeypatch):
    """Records every WhatsApp send attempt (never a real Twilio call — no
    credentials in the test env, so the client would no-op anyway) so
    tests can assert a message fired at the right trip transition."""
    sent: list[dict] = []

    async def _fake_send(*, to_msisdn: str, body: str):
        sent.append({"to": to_msisdn, "body": body})
        return {"sent": True}

    monkeypatch.setattr(whatsapp_client, "send", _fake_send)
    return sent


@pytest_asyncio.fixture
async def client(sent_whatsapp_messages):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        await reset_db()
        resp = await ac.post("/demo/reset")
        assert resp.status_code == 200
        yield ac
