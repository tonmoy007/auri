"""Alembic environment configuration with async SQLAlchemy support.

All models are imported here so that ``alembic revision --autogenerate``
can detect schema changes automatically.
"""

from __future__ import annotations

import asyncio
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Alembic Config object.
config = context.config

# Set up Python logging from the INI file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import all models so autogenerate can see them ─────────────────────────
from app.config import settings
from app.models.base import Base
from app.models.confession import Confession  # noqa: F401
from app.models.user import AnonymousUser  # noqa: F401

# Convenience reference.
target_metadata = Base.metadata

# ── Migration modes ────────────────────────────────────────────────────────

# Convert the sync URL from alembic.ini to an async URL for asyncpg.
SYNC_URL = config.get_main_option("sqlalchemy.url")
ASYNC_URL = SYNC_URL.replace(
    "postgresql://", "postgresql+asyncpg://"
) if SYNC_URL else ""


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without connecting).

    The generated SQL can be executed later against any database.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Execute pending migrations on the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an async engine.

    Uses the asyncpg driver for compatibility with the application's
    async session stack.
    """
    from sqlalchemy import text

    connectable = create_async_engine(
        ASYNC_URL,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Run inside a transaction that supports run_sync.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
