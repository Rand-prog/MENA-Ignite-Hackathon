"""Outbound WhatsApp messaging via Twilio's WhatsApp API. Not part of the
mandatory CAMARA/agent tooling — this is a plain notification channel, same
category as the SMS the docs describe, just over WhatsApp instead.

Twilio's WhatsApp *sandbox* (the free, no-business-verification option) is
what a hackathon prototype actually uses: each contact number joins once by
sending the sandbox's join code to its WhatsApp number, then the sandbox
number can message them. An approved production sender works identically
here — only TWILIO_WHATSAPP_FROM changes.

Inbound replies (a contact texting back) are NOT wired up — that needs a
public HTTPS webhook Twilio can reach, which is out of scope for a
localhost demo backend. Standing a trip down still goes through the
dashboard's resolve action or /demo/contact-reply, exactly as before this
was added.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .config import settings

LogFn = Callable[[dict[str, Any]], Awaitable[None]]


class WhatsAppClient:
    def __init__(self, log_fn: LogFn | None = None) -> None:
        self._log_fn = log_fn
        self._http = httpx.AsyncClient(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(settings.twilio_account_sid and settings.twilio_auth_token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, *, to_msisdn: str, body: str) -> dict:
        """to_msisdn is a plain phone number like +962790000002 — the
        whatsapp: prefix is applied here, callers never need to know it."""
        start = time.monotonic()
        to = to_msisdn if to_msisdn.startswith("whatsapp:") else f"whatsapp:{to_msisdn}"

        if not self.enabled:
            # No credentials configured — log it as a no-op rather than
            # silently dropping it or crashing the trip flow over a
            # notification channel that was never wired up.
            await self._log(
                endpoint=f"POST (skipped, no Twilio creds) -> {to}",
                latency_ms=0, status=0, cost_usd=0.0,
            )
            return {"sent": False, "reason": "twilio not configured"}

        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        try:
            resp = await self._http.post(
                url,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                data={"From": settings.twilio_whatsapp_from, "To": to, "Body": body},
            )
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            await self._log(endpoint=f"POST -> {to}", latency_ms=latency_ms, status=0, cost_usd=0.0)
            return {"sent": False, "reason": str(exc)}

        latency_ms = int((time.monotonic() - start) * 1000)
        # Twilio's WhatsApp price varies by country/conversation type; not
        # worth modelling precisely for a demo log — flat estimate.
        await self._log(
            endpoint=f"POST -> {to}", latency_ms=latency_ms,
            status=resp.status_code, cost_usd=0.005 if resp.status_code < 400 else 0.0,
        )
        return {"sent": resp.status_code < 400, "status": resp.status_code, "body": resp.text[:300]}

    async def _log(self, *, endpoint: str, latency_ms: int, status: int, cost_usd: float) -> None:
        if not self._log_fn:
            return
        await self._log_fn({
            "ts": datetime.now(timezone.utc).replace(tzinfo=None),
            "api": "WhatsApp", "endpoint": endpoint,
            "latency_ms": latency_ms, "status": status, "cost_usd": cost_usd,
        })
