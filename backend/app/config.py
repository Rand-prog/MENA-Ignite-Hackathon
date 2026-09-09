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
    # Confirmed live (2026-09-04): a bare, isolated call to gemini-flash-
    # lite-latest — nothing else running, no local concurrency — swung
    # from 1.7s to 44s across a few minutes, with no pattern (not a
    # consistent slow baseline a timeout could be tuned around — 12s and
    # 20s were both tried and both still got beaten by real spikes). Most
    # likely a heavily-used free-tier key getting soft-throttled rather
    # than a stable latency figure; expected to ease off with lighter use
    # or a quota reset, not something a client-side timeout fixes. No
    # timeout here by design — see agent/graph.py's _call_gemini; the
    # deterministic fallback in fallback.py is still the safety net if
    # this call errors outright, just not if it's merely slow.

    # WhatsApp — Twilio's WhatsApp API (sandbox or an approved sender).
    # Blank sid/token means WhatsApp sending is a no-op (logged, not sent) —
    # never a startup failure, since the sandbox is opt-in per contact.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"  # Twilio sandbox default

    # Backend
    signalguard_demo_mode: bool = True
    # Seconds to hold a trip in BUFFER before the agent runs, so the app's
    # "preparing" screen is actually on screen long enough to be seen.
    #
    # Measured: without this, BUFFER lasts ~2.7s (the Nokia + Gemini calls)
    # while the app polls every 45s when it has no trip. The approach
    # screen the product's Phase 2 is built around was therefore a coin
    # flip the viewer usually lost. This is a DEMO AID and nothing else --
    # it is read only where signalguard_demo_mode is already true, and it
    # makes every real crossing slower by exactly this much, which is why
    # it defaults to 0 and has to be asked for.
    signalguard_demo_buffer_hold_sec: int = 0
    database_url: str = "sqlite+aiosqlite:///./signalguard.db"
    # Nokia's sandbox validates that the sink hostname actually resolves
    # (INVALID_SINK / "Unresolvable callback hostname" otherwise) — the
    # build prompt's api.signalguard.io is a placeholder that doesn't.
    # example.com resolves and is harmless; swap for a real deployed
    # webhook once one exists.
    signalguard_webhook_base: str = "https://example.com"
    # Bearer token the operator would issue for our notification sink, sent
    # as the CAMARA `sinkCredential` on the Device Reachability Status
    # subscription. Blank in the sandbox — see nokia_client.create_
    # reachability_subscription on why an empty value is omitted rather
    # than sent: claiming an authenticated sink we do not have is worse
    # than declaring an unauthenticated one.
    nac_sink_access_token: str = ""

    # Deterministic fallback risk model — hard floors/ceilings, §5.4 of the
    # Technical Design doc. The model can widen/narrow within these; it can
    # never set a window of zero and can never disable the safety net.
    window_floor_min: int = 30
    window_ceiling_min: int = 180
    tier1_grace_min: int = 20

    # Tier 0 — how long the traveller's own handset gets to answer before
    # a human is told. Deliberately short: this delays an alarm, and the
    # whole safety claim is that nothing can suppress one. 90s is long
    # enough for a notification to land and a thumb to reach it, short
    # enough that it costs nothing real if the phone is face-down on a
    # passenger seat. Entered only when the network says the handset is
    # reachable — a dark handset skips Tier 0 entirely.
    tier0_grace_sec: int = 90

    # Traveller-declared break (idea: the commonest false alarm is a
    # planned stop, not an emergency). Capped so a declaration can't
    # silently disable monitoring for a whole day.
    planned_stop_max_min: int = 240

    # Convoy — two travellers arming the same zone within this window are
    # treated as travelling together, so a peer who is already out the far
    # side can be asked before a contact 400km away is woken.
    convoy_window_min: int = 45

    # Learned corridor times. Below this many clean crossings for a
    # (zone, hour) bucket the registry's hand-set nominal is still used —
    # a p50 over two samples is noise wearing a statistic's clothes.
    corridor_stats_min_samples: int = 3

    # corridor_stats.py reads only crossings from the last N days rather
    # than the whole table — a corridor's timing from a year ago isn't
    # evidence about how it runs today, and the table would otherwise grow
    # every completed crossing forever with no limit on what a single
    # stats_for()/zone_summary() call reads. 120 days is generous relative
    # to corridor_stats_min_samples: a corridor with light traffic needs
    # room to actually clear the sample floor within the window.
    corridor_stats_window_days: int = 120

    # Auto-discovered dead zones: a grid cell needs this many
    # unreachable observations, and this share of its total, before it is
    # offered as a candidate zone.
    coverage_candidate_min_obs: int = 4
    coverage_candidate_min_ratio: float = 0.6

    # Retention — how long raw rows survive before the opportunistic prune
    # in retention.py deletes them. Nothing here holds a live trip open:
    # every table pruned is written once, from a trip that has already
    # closed. See docs/SignalGuard_Security_Privacy (1).pdf §6/§12, which
    # names a published retention number as a known gap in the original
    # design — these are that number, set per table rather than left
    # undecided.
    #
    # api_log — a pure operational log (Nokia call latency/cost/status).
    # Not personal data in the doc's sense, but trip-attributable and has
    # no use once the run that produced it is over.
    retention_api_log_days: int = 14
    # crossing_history — already reduced to zone/hour/duration with no
    # identity or position (see corridor_stats.py). Kept longer than
    # corridor_stats_window_days above so the prune can never delete a row
    # stats_for()/zone_summary() are still entitled to read.
    retention_crossing_history_days: int = 180
    # coverage_observations — the most sensitive of the three: a
    # (position, reachable?) reading, snapped to a coarse grid and
    # stripped of any traveller/trip id (see coverage.py), but still the
    # closest thing this system keeps to a location record. Given the
    # doc's own 30-day working assumption for an ordinary trip record,
    # there's no case for this table — more sensitive, not less — to
    # outlive it.
    retention_coverage_observations_days: int = 30
    # How often the opportunistic prune called from tick() actually issues
    # its DELETEs, versus a no-op timestamp check. tick() runs on every
    # app and dashboard poll; there's no reason for that hot path to hit
    # the DB with three DELETE statements per call.
    retention_prune_interval_min: int = 10

    def secrets(self) -> list[str]:
        """Values that must never be logged or echoed."""
        return [s for s in (self.nac_api_key, self.google_api_key, self.twilio_auth_token) if s]


settings = Settings()
