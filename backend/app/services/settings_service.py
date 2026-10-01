"""Live-configurable settings, DB-first with a Settings()/.env fallback.

The admin dashboard writes here so LLM provider config, STT model, and
voice-mask effect chains can change without restarting the backend.
Reads never hit the DB directly — an in-memory cache is loaded once at
startup (``load_cache``) and kept in sync on every write (``set_config``),
so request-path reads (``get_config``) stay a plain dict lookup.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}


async def load_cache(session: AsyncSession) -> None:
    """Populate the in-memory config cache from the DB. Call once at startup."""
    result = await session.execute(select(AppSetting.key, AppSetting.value))
    _cache.clear()
    _cache.update({key: value for key, value in result.all()})
    logger.info("Loaded %d live config override(s) from DB", len(_cache))


def get_config(key: str, default: str) -> str:
    """Return *key*'s live value: DB override if one exists, else *default*."""
    return _cache.get(key, default)


def is_overridden(key: str) -> bool:
    """Return ``True`` if *key* has a DB override (vs. falling back to default)."""
    return key in _cache


def get_config_json(key: str, default: object) -> object:
    """Like :func:`get_config`, but JSON-decodes the stored value.

    Falls back to *default* if unset or if the stored value is invalid JSON
    (a hand-edited row should degrade to the built-in default, not crash
    the request that reads it).
    """
    raw = _cache.get(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed JSON for config key %r", key)
        return default


async def set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert *key*=*value* in the DB and update the live cache immediately."""
    stmt = insert(AppSetting).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppSetting.key], set_={"value": value}
    )
    await session.execute(stmt)
    await session.commit()
    _cache[key] = value


async def stage_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert *key*=*value* in the session without committing or touching the cache.

    For a caller that commits it together with an audit row; it then calls
    :func:`cache_config` once that commit has succeeded.
    """
    stmt = insert(AppSetting).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppSetting.key], set_={"value": value}
    )
    await session.execute(stmt)


async def stage_clear(session: AsyncSession, key: str) -> None:
    """Delete *key*'s override in the session, without committing or touching the cache."""
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)


def cache_config(key: str, value: str | None) -> None:
    """Make *key* live in the cache (``None`` drops the override); call after the commit."""
    if value is None:
        _cache.pop(key, None)
    else:
        _cache[key] = value


async def clear_config(session: AsyncSession, key: str) -> None:
    """Remove *key*'s DB override, reverting it to the ``Settings()`` default."""
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()
    _cache.pop(key, None)
