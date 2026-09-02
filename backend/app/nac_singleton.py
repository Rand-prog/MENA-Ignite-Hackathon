"""Process-wide NokiaClient, wired to log every real call into api_log via
its own short-lived session — independent of whichever request session
triggered the call. This is what feeds /demo/api-log."""
from __future__ import annotations

from typing import Any

from .db import SessionLocal
from .models import ApiLogEntry
from .nokia_client import NokiaClient


async def _log_to_db(entry: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        session.add(ApiLogEntry(**entry))
        await session.commit()


nac_client = NokiaClient(log_fn=_log_to_db)
