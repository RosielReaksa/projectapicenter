"""Database engine and session handling."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


if settings.is_sqlite:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": 30},
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
    )

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
