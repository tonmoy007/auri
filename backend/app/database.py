"""Async SQLAlchemy engine and session factory using asyncpg."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

# Prefer an explicit DATABASE_URL (e.g. set by CI); otherwise build it from parts.
DATABASE_URL: str = settings.DATABASE_URL or (
    f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,  # Disable pooling for serverless-friendly behaviour.
    echo=settings.SQL_ECHO,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session; rolls back on any exception.

    Yields:
        An :class:`AsyncSession` bound to the engine.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# One declaration, used by every route and sub-dependency.
#
# ``scope="function"`` makes the session's exit code (the commit) run right after
# the handler returns, BEFORE the response is sent. The default would run it
# afterwards, so a commit that failed could not change what the caller had
# already been told: a confession reported saved and lost, a decision
# acknowledged and never stored. FastAPI includes the scope in its dependency
# cache key, so a single shared object also guarantees a request gets exactly one
# session: mixing scopes would silently give it two.
session_dependency = Depends(get_async_session, scope="function")


async def check_db_connected() -> bool:
    """Return ``True`` if the database engine can execute a simple query."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — deliberate fail-safe boundary around the DB connectivity check; any failure here (driver, network, auth) should degrade to "unhealthy", not crash the caller
        logger.error("Database connectivity check failed: %s", exc)
        return False
