"""Data retention — purge confessions that have already served their purpose.

Per the plan's Data Privacy Design: audio is already deleted immediately
after STT/TTS (backend/app/api/v1/stt.py, tts.py never write to a
persistent path), so the only retained data is the confession DB row
itself. Every confession is removed ``settings.RETENTION_HOURS`` after its
last change, whatever its status. ``pending`` and ``flagged`` rows used to be
kept until someone acted on them, which in practice meant for ever; since plan
14.10 (owner decision 2026-10-01) they follow the same window, and each run
records how many flagged items, and how many crisis items nobody had
acknowledged, it removed.

**Replies outlive the confession.** A confession (other than a withdrawn one)
can carry an HR
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

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Delete,
    Update,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.user import AnonymousUser
from app.services.insight_rollup import fold_leaving

logger = logging.getLogger(__name__)


def _not_withdrawn() -> ColumnElement[bool]:
    """A confession the confessor did not withdraw: its reply is worth keeping."""
    return Confession.status != ConfessionStatus.deleted


def _withdrawn_or_unreplied_due(cutoff: datetime) -> ColumnElement[bool]:
    """Rows to delete outright: stale, and with no reply worth keeping."""
    keeps_reply = and_(_not_withdrawn(), Confession.hr_reply.is_not(None))
    return and_(Confession.updated_at < cutoff, ~keeps_reply)


def _emptying_due(cutoff: datetime) -> ColumnElement[bool]:
    """Rows to empty to a shell: stale, not withdrawn, replied, not yet emptied."""
    return and_(
        _not_withdrawn(),
        Confession.hr_reply.is_not(None),
        Confession.purged_at.is_(None),
        Confession.updated_at < cutoff,
    )


def _expiry_due(reply_cutoff: datetime, stale_cutoff: datetime) -> ColumnElement[bool]:
    """Rows whose reply is past retention and that are emptied or stale anyway."""
    return and_(
        _not_withdrawn(),
        Confession.hr_replied_at.is_not(None),
        Confession.hr_replied_at < reply_cutoff,
        or_(Confession.purged_at.is_not(None), Confession.updated_at < stale_cutoff),
    )


@dataclass(frozen=True)
class RetentionResult:
    """How many rows one retention run touched, by what it did to them."""

    deleted: int
    emptied_to_shell: int
    expired_replies: int
    expired_devices: int = 0
    flagged_removed: int = 0
    unacknowledged_crisis_removed: int = 0


@dataclass(frozen=True)
class DueCounts:
    """How many rows the next run would act on, by what it would do to them."""

    to_delete: int
    to_empty: int
    to_expire: int


async def _count(session: AsyncSession, predicate: ColumnElement[bool]) -> int:
    """Count the confession rows matching *predicate*."""
    return await session.scalar(select(func.count()).where(predicate)) or 0


async def count_due(
    session: AsyncSession,
    now: datetime,
    retention_hours: int,
    reply_retention_days: int,
) -> DueCounts:
    """Count what a run at *now* would delete, empty and expire, without doing it.

    Uses the very predicates the run itself uses, so the figure cannot drift
    from what the job actually does.

    Args:
        session: Active database session.
        now: Current time, injected.
        retention_hours: How long a confession lives after its last change.
        reply_retention_days: How long a reply lives after it was written.

    Returns:
        The three counts.
    """
    cutoff = now - timedelta(hours=retention_hours)
    reply_cutoff = now - timedelta(days=reply_retention_days)
    return DueCounts(
        to_delete=await _count(session, _withdrawn_or_unreplied_due(cutoff)),
        # A row whose reply is already past retention is expired first, so it
        # is not also counted as one that would be emptied.
        to_empty=await _count(
            session,
            and_(_emptying_due(cutoff), ~_expiry_due(reply_cutoff, cutoff)),
        ),
        to_expire=await _count(session, _expiry_due(reply_cutoff, cutoff)),
    )


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
    stmt = delete(Confession).where(_withdrawn_or_unreplied_due(cutoff))
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
        .where(_emptying_due(cutoff))
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
    stmt = delete(Confession).where(_expiry_due(reply_cutoff, stale_cutoff))
    expired = await _execute_bulk(session, stmt)
    if expired:
        logger.info(
            "retention: deleted %d repl(ies) older than %dd",
            expired,
            reply_retention_days,
        )
    return expired


async def purge_stale_devices(
    session: AsyncSession, now: datetime, window_seconds: int
) -> int:
    """Delete device records whose rate-limit window has passed.

    ``anonymous_users`` exists for one reason: to refuse a second confession
    from the same device inside ``CONFESSION_RATE_LIMIT_SECONDS``. Once that
    window has passed a record does nothing except tie a device code to when it
    last spoke, and to how often, for ever. A deleted record behaves exactly
    like a device that has never submitted, which is what a device past its
    window already is.

    Args:
        session: Active database session (caller commits).
        now: Current time, injected.
        window_seconds: The rate-limit window (``CONFESSION_RATE_LIMIT_SECONDS``).

    Returns:
        How many records were deleted.
    """
    cutoff = now - timedelta(seconds=window_seconds)
    return await _execute_bulk(
        session, delete(AnonymousUser).where(AnonymousUser.last_confession_at < cutoff)
    )


def _content_leaving(
    now: datetime, retention_hours: int, reply_retention_days: int
) -> ColumnElement[bool]:
    """Rows whose content this run will delete or empty, matched once each.

    Only rows whose content is still present match, so a shell emptied in one run
    and expired in a later one matches once, when its content left.
    """
    cutoff = now - timedelta(hours=retention_hours)
    reply_cutoff = now - timedelta(days=reply_retention_days)
    return and_(
        Confession.purged_at.is_(None),
        or_(
            _withdrawn_or_unreplied_due(cutoff),
            _emptying_due(cutoff),
            _expiry_due(reply_cutoff, cutoff),
        ),
    )


async def _count_removals(
    session: AsyncSession, leaving: ColumnElement[bool]
) -> tuple[int, int]:
    """Count the flagged and unacknowledged-crisis rows among those *leaving*."""
    flagged = await _count(
        session, and_(leaving, Confession.status == ConfessionStatus.flagged)
    )
    unseen_crisis = await _count(
        session,
        and_(
            leaving,
            Confession.severity == ModerationSeverity.crisis.value,
            Confession.acknowledged_at.is_(None),
        ),
    )
    return flagged, unseen_crisis


async def run_retention(
    session: AsyncSession,
    now: datetime,
    retention_hours: int,
    reply_retention_days: int,
    device_window_seconds: int | None = None,
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
        device_window_seconds: The confession rate-limit window. Device records
            older than it are deleted; ``None`` leaves them alone, because a
            caller that does not know the window must not guess one.

    Returns:
        What the run did, by kind.
    """
    leaving = _content_leaving(now, retention_hours, reply_retention_days)
    flagged, unseen_crisis = await _count_removals(session, leaving)
    # Before anything is deleted or emptied: Insights keeps only these counts.
    await fold_leaving(session, leaving)
    expired = await expire_old_replies(
        session, now, reply_retention_days, retention_hours
    )
    deleted = await purge_stale_confessions(session, now, retention_hours)
    emptied = await empty_replied_confessions(session, now, retention_hours)
    devices = (
        await purge_stale_devices(session, now, device_window_seconds)
        if device_window_seconds is not None
        else 0
    )
    return RetentionResult(
        deleted=deleted,
        emptied_to_shell=emptied,
        expired_replies=expired,
        expired_devices=devices,
        flagged_removed=flagged,
        unacknowledged_crisis_removed=unseen_crisis,
    )


async def _main() -> None:
    """CLI entrypoint — run retention using real settings against the real database."""
    from app.database import async_session_factory

    logging.basicConfig(level=settings.LOG_LEVEL)

    from app.services.retention_status import record_run

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await run_retention(
            session,
            now,
            settings.RETENTION_HOURS,
            settings.REPLY_RETENTION_DAYS,
            device_window_seconds=settings.CONFESSION_RATE_LIMIT_SECONDS,
        )
        await record_run(
            session,
            now,
            result,
            settings.RETENTION_HOURS,
            settings.REPLY_RETENTION_DAYS,
        )
        await session.commit()

    logger.info(
        "retention: run complete, %d deleted, %d emptied to shells, "
        "%d replies expired, %d device records removed",
        result.deleted,
        result.emptied_to_shell,
        result.expired_replies,
        result.expired_devices,
    )


if __name__ == "__main__":
    asyncio.run(_main())
