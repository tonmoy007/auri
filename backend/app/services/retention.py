"""Data retention — purge confessions that have already served their purpose.

Per the plan's Data Privacy Design: audio is already deleted immediately
after STT/TTS (backend/app/api/v1/stt.py, tts.py never write to a
persistent path), so the only retained data is the confession DB row
itself. Confessions in ``forwarded`` or ``deleted`` status have already
been delivered or discarded — nothing further reads them — so they are
removed after ``settings.RETENTION_HOURS``. ``pending`` and ``flagged`` rows
are never touched here: they are still awaiting action.

**Replies outlive the confession.** A forwarded confession can carry an HR
reply the confessor has not read yet. When such a row falls due it is not
deleted: it is emptied to a *reply-only shell* — transcript, summary,
category, sentiment, department, counselor text, severity and the reviewer
ids are cleared, and ``purged_at`` is stamped. What remains is the reply, its
timestamps and the device hash, because that hash is the only way the
confessor's own device can ask for the reply. The shell is hard-deleted
``settings.REPLY_RETENTION_DAYS`` after the reply was first saved, so the
device hash is kept longer for replied rows only, and by a bounded, published
amount. Reply writes leave ``updated_at`` unchanged (see ``hr_reply_service``),
so a save never restarts the 24-hour clock.

Not run automatically inside the FastAPI process — invoke this module
directly on a schedule (cron, k8s CronJob, etc.):

    python -m app.services.retention
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import CursorResult, Delete, Update, delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity

logger = logging.getLogger(__name__)

_PURGEABLE_STATUSES = (ConfessionStatus.forwarded, ConfessionStatus.deleted)


@dataclass(frozen=True)
class RetentionResult:
    """How many rows one retention run touched, by what it did to them."""

    deleted: int
    emptied_to_shell: int
    expired_replies: int


async def _execute_bulk(session: AsyncSession, statement: Delete | Update) -> int:
    """Run a bulk DELETE/UPDATE and return how many rows it touched.

    ``synchronize_session=False``: this is a batch job over rows nothing else
    holds. Letting the ORM re-evaluate the criteria against any objects that
    happen to be in the session could evict one whose in-memory copy is stale.
    """
    result = await session.execute(
        statement.execution_options(synchronize_session=False)
    )
    return cast(CursorResult, result).rowcount or 0


async def purge_stale_confessions(
    session: AsyncSession, now: datetime, retention_hours: int
) -> int:
    """Hard-delete stale confessions that have no reply to keep.

    Withdrawn (``deleted``) confessions are deleted along with any reply: the
    confessor withdrew it, so there is nothing to show them. A ``forwarded``
    confession that carries a reply is *not* deleted here — see
    :func:`empty_replied_confessions`.

    Args:
        session: Active database session (caller commits).
        now: Current time — injected rather than read directly, so this
            is deterministically testable (AGENTS.md §16.5).
        retention_hours: Age threshold in hours.

    Returns:
        The number of rows deleted.
    """
    cutoff = now - timedelta(hours=retention_hours)
    keeps_reply = (
        Confession.status == ConfessionStatus.forwarded,
        Confession.hr_reply.is_not(None),
    )
    stmt = delete(Confession).where(
        Confession.status.in_(_PURGEABLE_STATUSES),
        Confession.updated_at < cutoff,
        ~(keeps_reply[0] & keeps_reply[1]),
    )
    deleted_count = await _execute_bulk(session, stmt)

    if deleted_count:
        logger.info(
            "retention: purged %d confession(s) older than %dh",
            deleted_count,
            retention_hours,
        )
    return deleted_count


async def empty_replied_confessions(
    session: AsyncSession, now: datetime, retention_hours: int
) -> int:
    """Empty stale forwarded confessions that carry a reply down to a shell.

    Clears everything that describes the confession and keeps only what the
    confessor's device needs to read the reply. Idempotent: a row already
    emptied is skipped.

    Args:
        session: Active database session (caller commits).
        now: Current time, injected.
        retention_hours: Age threshold in hours, as for the plain purge.

    Returns:
        The number of rows emptied.
    """
    cutoff = now - timedelta(hours=retention_hours)
    stmt = (
        update(Confession)
        .where(
            Confession.status == ConfessionStatus.forwarded,
            Confession.hr_reply.is_not(None),
            Confession.purged_at.is_(None),
            Confession.updated_at < cutoff,
        )
        .values(
            transcript="",
            ai_summary=None,
            category=None,
            sentiment=None,
            recipient_dept=None,
            counselor_response=None,
            severity=ModerationSeverity.none.value,
            reviewed_by=None,
            reviewed_at=None,
            acknowledged_by=None,
            acknowledged_at=None,
            purged_at=now,
        )
    )
    emptied = await _execute_bulk(session, stmt)
    if emptied:
        logger.info("retention: emptied %d replied confession(s) to shells", emptied)
    return emptied


async def expire_old_replies(
    session: AsyncSession,
    now: datetime,
    reply_retention_days: int,
    retention_hours: int,
) -> int:
    """Hard-delete forwarded confessions whose reply is past its retention.

    Measured from when the reply was *first* saved, so editing a reply never
    extends how long the device hash is kept. Only rows that are already
    emptied, or are stale enough that ordinary retention is due anyway, are
    eligible: HR may reply to a *pending* confession, and if the confessor
    forwards it long afterwards the reply is older than the retention but the
    confession is live and undelivered — deleting it then would lose it.

    Args:
        session: Active database session (caller commits).
        now: Current time, injected.
        reply_retention_days: How long a reply is kept after it was written.
        retention_hours: The ordinary age after which a forwarded row is due.

    Returns:
        The number of rows deleted.
    """
    reply_cutoff = now - timedelta(days=reply_retention_days)
    stale_cutoff = now - timedelta(hours=retention_hours)
    stmt = delete(Confession).where(
        Confession.status == ConfessionStatus.forwarded,
        Confession.hr_replied_at.is_not(None),
        Confession.hr_replied_at < reply_cutoff,
        or_(Confession.purged_at.is_not(None), Confession.updated_at < stale_cutoff),
    )
    expired = await _execute_bulk(session, stmt)
    if expired:
        logger.info(
            "retention: deleted %d repl(ies) older than %dd",
            expired,
            reply_retention_days,
        )
    return expired


async def run_retention(
    session: AsyncSession,
    now: datetime,
    retention_hours: int,
    reply_retention_days: int,
) -> RetentionResult:
    """Run every retention step, in an order that never loses a live reply.

    Expired replies go first, then confessions with nothing to keep, then the
    remaining replied ones are emptied — so a row is never emptied only to be
    deleted in the same run.

    Args:
        session: Active database session (caller commits).
        now: Current time, injected.
        retention_hours: How long a confession lives after its last change.
        reply_retention_days: How long a reply lives after it was written.

    Returns:
        What the run did, by kind.
    """
    expired = await expire_old_replies(
        session, now, reply_retention_days, retention_hours
    )
    deleted = await purge_stale_confessions(session, now, retention_hours)
    emptied = await empty_replied_confessions(session, now, retention_hours)
    return RetentionResult(
        deleted=deleted, emptied_to_shell=emptied, expired_replies=expired
    )


async def _main() -> None:
    """CLI entrypoint — run retention using real settings against the real database."""
    from app.database import async_session_factory

    logging.basicConfig(level=settings.LOG_LEVEL)

    async with async_session_factory() as session:
        result = await run_retention(
            session,
            datetime.now(timezone.utc),
            settings.RETENTION_HOURS,
            settings.REPLY_RETENTION_DAYS,
        )
        await session.commit()

    logger.info(
        "retention: run complete, %d deleted, %d emptied to shells, %d replies expired",
        result.deleted,
        result.emptied_to_shell,
        result.expired_replies,
    )


if __name__ == "__main__":
    asyncio.run(_main())
