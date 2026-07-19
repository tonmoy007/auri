"""Data retention — purge confessions that have already served their purpose.

Per the plan's Data Privacy Design: audio is already deleted immediately
after STT/TTS (backend/app/api/v1/stt.py, tts.py never write to a
persistent path), so the only retained data is the confession DB row
itself. Confessions in ``forwarded`` or ``deleted`` status have already
been delivered or discarded — nothing further reads them — so they are
hard-deleted after ``settings.RETENTION_HOURS``. ``pending`` and
``flagged`` rows are never touched here: they are still awaiting action.

Not run automatically inside the FastAPI process — invoke this module
directly on a schedule (cron, k8s CronJob, etc.):

    python -m app.services.retention
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confession import Confession, ConfessionStatus

logger = logging.getLogger(__name__)

_PURGEABLE_STATUSES = (ConfessionStatus.forwarded, ConfessionStatus.deleted)


async def purge_stale_confessions(
    session: AsyncSession, now: datetime, retention_hours: int
) -> int:
    """Hard-delete forwarded/deleted confessions older than *retention_hours*.

    Args:
        session: Active database session (caller commits).
        now: Current time — injected rather than read directly, so this
            is deterministically testable (AGENTS.md §16.5).
        retention_hours: Age threshold in hours.

    Returns:
        The number of rows deleted.
    """
    cutoff = now - timedelta(hours=retention_hours)
    stmt = delete(Confession).where(
        Confession.status.in_(_PURGEABLE_STATUSES),
        Confession.updated_at < cutoff,
    )
    result = cast(CursorResult, await session.execute(stmt))
    deleted_count = result.rowcount or 0

    if deleted_count:
        logger.info(
            "retention: purged %d confession(s) older than %dh",
            deleted_count,
            retention_hours,
        )
    return deleted_count


async def _main() -> None:
    """CLI entrypoint — purge using real settings against the real database."""
    from app.database import async_session_factory

    logging.basicConfig(level=settings.LOG_LEVEL)

    async with async_session_factory() as session:
        deleted_count = await purge_stale_confessions(
            session, datetime.now(timezone.utc), settings.RETENTION_HOURS
        )
        await session.commit()

    logger.info("retention: run complete, %d row(s) purged", deleted_count)


if __name__ == "__main__":
    asyncio.run(_main())
