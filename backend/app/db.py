from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

is_sqlite = settings.database_url.startswith("sqlite")


class Base(DeclarativeBase):
    pass


# SQLite: a request's own session can still be mid-transaction when the
# Nokia client's log_fn opens a second, independent session to write an
# api_log row (see nac_singleton.py) — plain sqlite3 raises "database is
# locked" almost immediately on that overlap. WAL mode plus a busy timeout
# makes a genuinely brief second writer wait instead of erroring.
#
# Two things that are NOT the fix, tried and reverted in that order, worth
# recording so nobody re-tries them:
#
# 1. A StaticPool (one shared connection for every session/request) — this
#    backend genuinely has concurrent requests (the dashboard and the app
#    both poll every few seconds while a slower request may be in flight),
#    and forcing every AsyncSession onto one physical connection lets their
#    transactions interleave on it. That surfaced as a StaleDataError
#    ("UPDATE expected to update 1 row, 0 were matched") — silent
#    corruption risk, not just a slower path.
#
# 2. A process-wide asyncio lock around every session (real per-connection
#    pool restored, but every request serialized behind one lock) — this
#    doesn't corrupt anything, but it serializes the *entire* request,
#    including the several real seconds of Nokia + Gemini network calls a
#    crossing's agent run makes. One crossing then blocks every other
#    request — dashboard polls, battery reports, everything — for its
#    whole duration, which is worse than the bug it was covering for.
#
# The actual fix was structural, not a locking strategy: state_machine.py's
# simulate_gate_event used to `session.add(trip); await session.flush()`
# *before* running the agent, which opens a real SQLite writer transaction
# that then sat open across every one of the agent's slow network calls —
# each of which needs to write to api_log on its own separate connection
# (see nac_singleton.py) and blocks behind that open transaction for the
# entire agent run. The fix is to build the Trip as a plain in-memory
# object and only touch the session once, right before the single commit
# at the end — no transaction is open while the slow calls happen, so
# there's nothing for the log writer to block on. See that function's own
# comment. With that fixed, plain real connections + WAL + busy_timeout
# handle the remaining genuinely-brief overlaps fine on their own.
#
# Pooling is orthogonal to all of that: it changes how long a connection
# lives, not what a connection is. SQLAlchemy defaults a file-backed
# aiosqlite engine to NullPool, so every session checkout opened a
# brand-new SQLite connection — and aiosqlite runs each connection on its
# own worker thread, making that a thread spawn plus a file open plus the
# two PRAGMAs below, per checkout.
# The poll paths take two checkouts per request (tick() commits and releases
# before the endpoint's own query runs), so the dashboard and the app were
# each paying for two new threads every few seconds, forever.
#
# A real queue pool is NOT the StaticPool that was tried and reverted above:
# StaticPool shares ONE connection between every session, which is what let
# concurrent transactions interleave and produce StaleDataError. A queue
# pool still hands each checkout its own distinct connection with its own
# transaction — it only stops throwing that connection away afterwards.
# pool_size is small on purpose; this is one prototype process talking to a
# local file, and idle SQLite connections are not free either.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=AsyncAdaptedQueuePool if is_sqlite else None,
    pool_size=5,
    max_overflow=10,
    connect_args={"timeout": 30} if is_sqlite else {},
)

if is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def reset_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
