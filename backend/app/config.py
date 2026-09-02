"""Env-driven config. Secrets only ever come from environment variables —
never hardcoded, never logged. See docs/SignalGuard_Security_Privacy (1).pdf §10."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Nokia Network as Code sandbox
    nac_base: str = "https://network-as-code.p-eu.apihub.nokia.io"
    nac_api_key: str = ""
    nac_device: str = "+99999991000"

    # Gemini / Google AI Studio (mandatory AI Resource & Tooling Guide)
    google_api_key: str = ""
    # Google retired the pinned "gemini-2.5-*" model IDs for new API keys
    # (confirmed live: 404 "no longer available to new users" on both
    # flash and pro). The hackathon's own mandatory requirement only names
    # "Gemini via Google AI Studio" — it never pins a version — so these
    # "-latest" aliases satisfy the actual rule and stay current
    # automatically. gemini-pro-latest has zero free-tier quota on a fresh
    # AI Studio key (confirmed live: 429, limit 0) and gemini-flash-latest
    # was 503-overloaded repeatedly; flash-lite-latest is the one that
    # actually answers on the free tier, so both stages use it for now —
    # swap gemini_risk_model back to a pro tier once billing is attached.
    # See README.md's Nokia/Gemini integration notes.
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_risk_model: str = "gemini-flash-lite-latest"

    # WhatsApp — Twilio's WhatsApp API (sandbox or an approved sender).
    # Blank sid/token means WhatsApp sending is a no-op (logged, not sent) —
    # never a startup failure, since the sandbox is opt-in per contact.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"  # Twilio sandbox default

    # Backend
    signalguard_demo_mode: bool = True
    database_url: str = "sqlite+aiosqlite:///./signalguard.db"
    # Nokia's sandbox validates that the sink hostname actually resolves
    # (INVALID_SINK / "Unresolvable callback hostname" otherwise) — the
    # build prompt's api.signalguard.io is a placeholder that doesn't.
    # example.com resolves and is harmless; swap for a real deployed
    # webhook once one exists.
    signalguard_webhook_base: str = "https://example.com"

    # Deterministic fallback risk model — hard floors/ceilings, §5.4 of the
    # Technical Design doc. The model can widen/narrow within these; it can
    # never set a window of zero and can never disable the safety net.
    window_floor_min: int = 30
    window_ceiling_min: int = 180
    tier1_grace_min: int = 20

    def secrets(self) -> list[str]:
        """Values that must never be logged or echoed."""
        return [s for s in (self.nac_api_key, self.google_api_key, self.twilio_auth_token) if s]


settings = Settings()
