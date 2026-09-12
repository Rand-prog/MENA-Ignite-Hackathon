"""Process-wide NokiaClient, wired to log every real call into api_log via
its own short-lived session — independent of whichever request session
triggered the call. This is what feeds /demo/api-log and the operator
console's network-activity panel (api_activity.py)."""
from __future__ import annotations

from typing import Any

from .api_activity import current_trip_id
from .db import SessionLocal
from .models import ApiLogEntry
from .nokia_client import NokiaClient


async def _log_to_db(entry: dict[str, Any]) -> None:
    # NokiaClient knows an API name, a method and a path, and deliberately
    # nothing about trips — so the trip a call belongs to is read from the
    # ambient context the tool layer sets (see api_activity.attributed_to).
    # None is the correct answer for a call made outside any crossing:
    # arming a zone's gates happens before a trip exists.
    async with SessionLocal() as session:
        session.add(ApiLogEntry(trip_id=current_trip_id.get(), **entry))
        await session.commit()


nac_client = NokiaClient(log_fn=_log_to_db)
