"""Live CAMARA call telemetry for the operator console.

Every real Nokia call already lands in `api_log` (see nac_singleton.py).
Until now the only reader was `/demo/api-log`, which is mounted only when
`SIGNALGUARD_DEMO_MODE=true` — so the one screen an operator actually
watches could not say whether the five network APIs this product is built
on were answering at all.

That is an operational hole, not a presentation one. This console's queue
is not filled by anything it owns: a trip appears because a CAMARA
Geofencing notification arrived, its risk numbers exist because Congestion
Insights and Location Retrieval answered, and its alarm is timed against a
reachability signal. When one of those starts returning 4xx the queue does
not go red — it goes *quiet*, which on this screen is indistinguishable
from a calm corridor. So the console has to be able to show the call layer
underneath it, in production and not only in demo mode.

Two views, both fed from `api_log`:

* a per-API roll-up — every one of the five is listed even with zero calls,
  because "we have not called Quality on Demand yet" and "Quality on Demand
  is unreachable" are different facts and an absent row states neither;
* the recent call feed, newest first, with each call attributed to the trip
  it was made for (see `attributed_to`) so a dispatcher reading a trip's
  detail panel can see the exact requests that produced the numbers in
  front of them.

Timestamps here are real wall time, deliberately — `api_log.ts` is written
from `datetime.now(timezone.utc)`, not `clock.now()`, because an HTTP call
to Nokia happens at a real instant that `/demo/clock/advance` cannot move.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiLogEntry

# The five CAMARA APIs, in the order the deck and the docs name them, with
# the GSMA Open Gateway category each sits in. This is the console's
# declaration of the network surface it depends on — not a summary of
# whatever happens to be in the log, which is why a zero-call API still
# gets a row.
CAMARA_APIS: tuple[tuple[str, str, str], ...] = (
    ("Geofencing Subscriptions", "geofencing-subscriptions v0.3", "Device intelligence"),
    ("Location Retrieval", "location-retrieval v0", "Device intelligence"),
    ("Device Reachability Status", "device-reachability-status v1 · subscriptions v0.8", "Device intelligence"),
    ("Congestion Insights", "congestion-insights v0", "Network intelligence"),
    ("Quality on Demand", "qod v0", "Programmable connectivity"),
)

_CAMARA_NAMES = {name for name, _, _ in CAMARA_APIS}

# Set for the duration of one agent tool call so the log row can name the
# trip it belongs to. A ContextVar rather than a parameter because the
# writer is NokiaClient._call, which is deliberately ignorant of trips —
# it takes an API name, a method and a path, and nothing else.
current_trip_id: ContextVar[str | None] = ContextVar("current_trip_id", default=None)


@contextlib.contextmanager
def attributed_to(trip_id: str | None) -> Iterator[None]:
    token = current_trip_id.set(trip_id)
    try:
        yield
    finally:
        current_trip_id.reset(token)


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    """`api_log.endpoint` is stored as "METHOD /path" (nokia_client._call).
    The console shows the two separately, so split once here rather than in
    JavaScript."""
    method, _, path = endpoint.partition(" ")
    return (method, path) if path else ("", endpoint)


def _row_to_dict(r: ApiLogEntry) -> dict:
    method, path = _split_endpoint(r.endpoint)
    return {
        "ts": r.ts.isoformat(),
        "api": r.api,
        "camara": r.api in _CAMARA_NAMES,
        "method": method,
        "path": path,
        "status": r.status,
        # Three outcomes, not two. status 0 is nokia_client's marker for
        # "the request never got an HTTP response at all"
        # (httpx.HTTPError); a negative status means it was never attempted
        # (whatsapp_client.NOT_ATTEMPTED — no gateway configured). An
        # unreachable API, a rejected request and a channel that was never
        # wired up each call for a different action, so the console has to
        # be able to tell them apart.
        "ok": 200 <= r.status < 400,
        "attempted": r.status >= 0,
        "latency_ms": r.latency_ms,
        "cost_usd": round(r.cost_usd, 4),
        "trip_id": r.trip_id,
    }


def _window_start(hours: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)


_OK = case((ApiLogEntry.status.between(200, 399), 1), else_=0)
_SKIPPED = case((ApiLogEntry.status < 0, 1), else_=0)


async def summary(session: AsyncSession, *, hours: int = 24) -> dict:
    """Per-API roll-up over the last `hours`, plus totals.

    One grouped query, not one query per API: this endpoint is polled on
    the same timer as the queue, and `api_log.ts` is indexed.
    """
    since = _window_start(hours)
    stmt = (
        select(
            ApiLogEntry.api,
            func.count().label("calls"),
            func.sum(_OK).label("ok"),
            func.sum(_SKIPPED).label("skipped"),
            func.avg(ApiLogEntry.latency_ms).label("avg_ms"),
            func.max(ApiLogEntry.ts).label("last_ts"),
            func.sum(ApiLogEntry.cost_usd).label("cost"),
        )
        .where(ApiLogEntry.ts >= since)
        .group_by(ApiLogEntry.api)
    )
    rows = {r.api: r for r in (await session.execute(stmt)).all()}

    # The most recent status per API, which the aggregate above cannot
    # give: "12 calls, 11 OK" does not say whether the failure was the
    # first one an hour ago or the one three seconds ago.
    last_stmt = (
        select(ApiLogEntry.api, ApiLogEntry.status)
        .where(ApiLogEntry.ts >= since)
        .order_by(ApiLogEntry.ts.desc())
    )
    last_status: dict[str, int] = {}
    for api, status in (await session.execute(last_stmt)).all():
        last_status.setdefault(api, status)

    def _entry(name: str, spec: str | None, category: str) -> dict:
        r = rows.get(name)
        calls = int(r.calls) if r else 0
        ok = int(r.ok or 0) if r else 0
        skipped = int(r.skipped or 0) if r else 0
        return {
            "api": name,
            "camara_spec": spec,
            "category": category,
            "calls": calls,
            "ok": ok,
            "skipped": skipped,
            "failed": calls - ok - skipped,
            "avg_ms": int(r.avg_ms) if r is not None and r.avg_ms is not None else None,
            "last_status": last_status.get(name),
            "last_ts": r.last_ts.isoformat() if r is not None and r.last_ts else None,
            "cost_usd": round(float(r.cost or 0.0), 4) if r is not None else 0.0,
        }

    apis = [_entry(name, spec, category) for name, spec, category in CAMARA_APIS]

    # Anything logged that is not one of the five — today that is the
    # WhatsApp/SMS gateway, which writes to the same table
    # (whatsapp_singleton.py). Listed rather than dropped: a contact
    # notification that failed to send is exactly the kind of thing this
    # panel exists to surface, and hiding it because it is not CAMARA
    # would be the wrong call. Kept out of the CAMARA totals, though.
    for name in sorted(n for n in rows if n not in _CAMARA_NAMES):
        apis.append(_entry(name, None, "Outbound notification"))

    camara_rows = [a for a in apis if a["camara_spec"] is not None]
    return {
        "window_hours": hours,
        "apis": apis,
        "totals": {
            "calls": sum(a["calls"] for a in camara_rows),
            "ok": sum(a["ok"] for a in camara_rows),
            "failed": sum(a["failed"] for a in camara_rows),
            "skipped": sum(a["skipped"] for a in camara_rows),
            "cost_usd": round(sum(a["cost_usd"] for a in camara_rows), 4),
            "apis_used": sum(1 for a in camara_rows if a["calls"]),
            "apis_total": len(CAMARA_APIS),
        },
    }


async def recent(
    session: AsyncSession, *, limit: int = 40, trip_id: str | None = None,
    hours: int | None = 24,
) -> list[dict]:
    """The newest `limit` calls, newest first. `trip_id` narrows to the
    calls made for one crossing — which is what the trip detail panel
    shows."""
    limit = max(1, min(200, limit))
    stmt = select(ApiLogEntry).order_by(ApiLogEntry.ts.desc()).limit(limit)
    if trip_id is not None:
        # A trip's own calls are worth showing however old the trip is; the
        # rolling feed is not.
        stmt = stmt.where(ApiLogEntry.trip_id == trip_id)
    elif hours is not None:
        stmt = stmt.where(ApiLogEntry.ts >= _window_start(hours))
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_dict(r) for r in rows]
