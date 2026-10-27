"""Database engine and session handling."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import settings
from app.core import metrics

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class InstrumentedPool(AsyncAdaptedQueuePool):
    """QueuePool that records how long callers waited for a connection.

    LOAD_FIX requirement 5/6: any remaining starvation must be visible as a
    metric and a log line, not just felt as latency.
    """

    def _do_get(self):  # type: ignore[override]
        started = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            metrics.observe_pool_wait(time.perf_counter() - started)


# The data plane never holds a connection across a provider call (see
# app/core/execute.py), so the pool only ever needs to cover short bursts of
# fast queries. A short checkout timeout makes any remaining starvation fail
# loudly in seconds instead of turning into a 30-second latency outage.
if settings.is_sqlite:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        poolclass=InstrumentedPool,
        pool_timeout=settings.db_pool_timeout,
        connect_args={"timeout": 30},
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        poolclass=InstrumentedPool,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )


def pool_stats() -> dict:
    """Checked-out / idle / capacity counts, for /metrics and the overload gate."""
    pool = engine.pool
    stats: dict = {"pool_class": type(pool).__name__}
    for name in ("size", "checkedin", "checkedout"):
        fn = getattr(pool, name, None)
        if fn is None:
            continue
        try:
            stats[name] = int(fn())
        except (TypeError, ValueError):  # pragma: no cover - pool without counts
            continue

    # pool.overflow() is NEGATIVE while base connections are still idle (it
    # reports size - checkedout in that range), so it cannot be used as the
    # overflow count. The true limit lives on the configured max.
    max_overflow = getattr(pool, "_max_overflow", None)
    stats["max_overflow"] = int(max_overflow) if isinstance(max_overflow, int) else 0
    stats["overflow"] = max(0, stats.get("checkedout", 0) - stats.get("size", 0))

    size = int(stats.get("size", 0))
    stats["capacity"] = size + max(0, stats["max_overflow"]) if size else 0
    return stats

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - setup only
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create missing tables.

    Alembic owns schema changes; this keeps a fresh checkout runnable and is
    what the test suite uses.
    """
    from app import models  # noqa: F401  (registers metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ping_db() -> bool:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("Database health check failed")
        return False


async def dispose_db() -> None:
    await engine.dispose()
