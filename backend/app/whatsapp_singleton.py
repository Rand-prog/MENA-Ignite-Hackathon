"""Process-wide WhatsAppClient, logged into the same api_log table Nokia
calls use — /demo/api-log then shows outbound messages alongside CAMARA
calls, which is genuinely useful on the demo video, not just convenient."""
from __future__ import annotations

from typing import Any

from .db import SessionLocal
from .models import ApiLogEntry
from .whatsapp_client import WhatsAppClient


async def _log_to_db(entry: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        session.add(ApiLogEntry(**entry))
        await session.commit()


whatsapp_client = WhatsAppClient(log_fn=_log_to_db)
