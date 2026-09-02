"""Real client for the five CAMARA APIs SignalGuard uses, on Nokia's Network
as Code sandbox. Shapes follow docs/nokia-api-catalog.md exactly.

Every call is logged via `log_fn` (timestamp, api, endpoint, latency, status,
approximate cost) — this feeds /demo/api-log and the demo video's network
activity pane. No secrets are ever included in a log line.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .config import settings

LogFn = Callable[[dict[str, Any]], Awaitable[None]]

# Per docs/SignalGuard_Technical_Feasibility.pdf §8 — directional, not billed.
_APPROX_COST_USD = {
    "Geofencing Subscriptions": 0.0,   # amortised, not per-crossing
    "Congestion Insights": 0.01,
    "Location Retrieval": 0.01,
    "Device Reachability Status": 0.0,  # subscription-based, near zero
    "Quality on Demand": 0.0,           # per-operator, undisclosed — not modelled
}


class NokiaClient:
    def __init__(self, log_fn: LogFn | None = None) -> None:
        headers = {}
        if settings.nac_api_key:
            headers["x-rapidapi-key"] = settings.nac_api_key
            # RapidAPI's Kong gateway routes by this listing name, which is
            # NOT the same as the URL host (network-as-code.p-eu.apihub.
            # nokia.io). Omitting it, or using the URL host instead,
            # returns a misleading 404 "API doesn't exists" even with a
            # valid key — confirmed against a working portal snippet.
            headers["x-rapidapi-host"] = "network-as-code.nokia.rapidapi.com"
        self._http = httpx.AsyncClient(
            base_url=settings.nac_base, headers=headers, timeout=30.0
        )
        self._log_fn = log_fn

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, api: str, method: str, path: str, json: dict | None = None) -> httpx.Response:
        start = time.monotonic()
        try:
            resp = await self._http.request(method, path, json=json)
            status = resp.status_code
        except httpx.HTTPError as exc:
            status = 0
            latency_ms = int((time.monotonic() - start) * 1000)
            if self._log_fn:
                await self._log_fn(
                    {
                        "ts": datetime.now(timezone.utc).replace(tzinfo=None),
                        "api": api,
                        "endpoint": f"{method} {path}",
                        "latency_ms": latency_ms,
                        "status": status,
                        "cost_usd": 0.0,
                    }
                )
            raise NokiaCallError(f"{api} unreachable: {exc}") from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        if self._log_fn:
            await self._log_fn(
                {
                    "ts": datetime.now(timezone.utc),
                    "api": api,
                    "endpoint": f"{method} {path}",
                    "latency_ms": latency_ms,
                    "status": status,
                    "cost_usd": _APPROX_COST_USD.get(api, 0.0),
                }
            )
        return resp

    # -- Geofencing Subscriptions -------------------------------------------
    async def create_geofence_subscription(
        self, *, device_phone: str, sink: str, lat: float, lon: float, radius_m: int
    ) -> dict:
        body = {
            "protocol": "HTTP",
            "sink": sink,
            "types": ["org.camaraproject.geofencing-subscriptions.v0.area-entered"],
            "config": {
                "subscriptionDetail": {
                    "device": {"phoneNumber": device_phone},
                    "area": {
                        "areaType": "CIRCLE",
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": radius_m,
                    },
                },
                "initialEvent": True,
                "subscriptionMaxEvents": 10,
            },
        }
        resp = await self._call(
            "Geofencing Subscriptions", "POST",
            "/geofencing-subscriptions/v0.3/subscriptions", json=body,
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    async def delete_geofence_subscription(self, subscription_id: str) -> None:
        await self._call(
            "Geofencing Subscriptions", "DELETE",
            f"/geofencing-subscriptions/v0.3/subscriptions/{subscription_id}",
        )

    # -- Location Retrieval ---------------------------------------------------
    async def retrieve_location(self, *, device_phone: str, max_age: int = 600) -> dict:
        resp = await self._call(
            "Location Retrieval", "POST", "/location-retrieval/v0/retrieve",
            json={"device": {"phoneNumber": device_phone}, "maxAge": max_age},
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    # -- Congestion Insights ---------------------------------------------------
    async def query_congestion(self, *, device_phone: str, webhook_url: str) -> dict:
        resp = await self._call(
            "Congestion Insights", "POST", "/congestion-insights/v0/query",
            json={
                "device": {"phoneNumber": device_phone},
                "webhook": {"notificationUrl": webhook_url},
            },
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    # -- Quality on Demand -------------------------------------------------
    async def create_qod_session(
        self, *, device_phone: str, device_ip: str = "1.1.1.1",
        app_server_ip: str = "1.1.1.1", qos_profile: str = "QOS_E", duration: int = 3600,
    ) -> dict:
        resp = await self._call(
            "Quality on Demand", "POST", "/qod/v0/sessions",
            json={
                "device": {
                    "phoneNumber": device_phone,
                    "ipv4Address": {"publicAddress": device_ip, "privateAddress": device_ip},
                },
                "applicationServer": {"ipv4Address": app_server_ip},
                "qosProfile": qos_profile,
                "duration": duration,
            },
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    # -- Device Reachability Status -----------------------------------------
    async def retrieve_reachability(self, *, device_phone: str) -> dict:
        resp = await self._call(
            "Device Reachability Status", "POST",
            "/device-status/device-reachability-status/v1/retrieve",
            json={"device": {"phoneNumber": device_phone}},
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    async def create_reachability_subscription(self, *, sink: str, access_token: str = "") -> dict:
        resp = await self._call(
            "Device Reachability Status", "POST",
            "/device-status/device-reachability-status-subscriptions/v0.8/subscriptions",
            json={
                "sink": sink,
                "sinkCredential": {"credentialType": "ACCESSTOKEN", "accessToken": access_token},
                "types": [
                    "org.camaraproject.device-reachability-status-subscriptions.v0.reachable"
                ],
            },
        )
        return resp.json() if resp.status_code < 400 else {"error": resp.text, "status": resp.status_code}

    # -- preflight -----------------------------------------------------------
    async def list_geofence_subscriptions(self) -> httpx.Response:
        return await self._call(
            "Geofencing Subscriptions", "GET",
            "/geofencing-subscriptions/v0.3/subscriptions",
        )


class NokiaCallError(RuntimeError):
    """Transport failure talking to Nokia's sandbox."""
