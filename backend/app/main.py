from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .nac_singleton import nac_client
from .routers import real
from .whatsapp_singleton import whatsapp_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.signalguard_demo_mode:
        # seed on first boot too, not only on /demo/reset
        from .db import SessionLocal
        from .models import Zone
        from .zones import ZONE_SEEDS

        async with SessionLocal() as session:
            existing = await session.get(Zone, ZONE_SEEDS[0].zone_id)
            if existing is None:
                for z in ZONE_SEEDS:
                    session.add(Zone(
                        zone_id=z.zone_id, label=z.label,
                        entry_lat=z.entry_lat, entry_lon=z.entry_lon,
                        exit_lat=z.exit_lat, exit_lon=z.exit_lon,
                        gate_radius_m=z.gate_radius_m, corridor_km=z.corridor_km,
                        nominal_crossing_min=z.nominal_crossing_min,
                    ))
                await session.commit()
    yield
    await nac_client.aclose()
    await whatsapp_client.aclose()


app = FastAPI(title="SignalGuard Backend", lifespan=lifespan)

# Prototype-only: the dashboard is a separate static origin with no auth of
# its own, so it needs to reach this API cross-origin. Not something to
# ship this wide in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(real.router)

if settings.signalguard_demo_mode:
    from .routers import demo

    app.include_router(demo.router)
