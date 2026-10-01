"""Recording retention runs and reading back how the job is doing.

Append-only by design: like the audit trail, this exposes ``record_run`` and
read functions and nothing that updates or deletes a row, because its purpose
is to be believable evidence that the job ran. Each row also keeps the windows
the run enforced, so the log can show what was actually applied, not only what
is configured today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.retention_run import RetentionRun
from app.services.retention import RetentionResult


async def record_run(
    session: AsyncSession,
    now: datetime,
    result: RetentionResult,
    retention_hours: int,
    reply_retention_days: int,
) -> RetentionRun:
    """Append one run to the log (the caller commits).

    Args:
        session: Active database session.
        now: When the run happened, from the job's injected clock.
        result: What the run did.
        retention_hours: The confession window this run enforced.
        reply_retention_days: The reply window this run enforced.

    Returns:
        The persisted :class:`RetentionRun`.
    """
    run = RetentionRun(
        ran_at=now,
        retention_hours=retention_hours,
        reply_retention_days=reply_retention_days,
        deleted=result.deleted,
        emptied_to_shell=result.emptied_to_shell,
        expired_replies=result.expired_replies,
        expired_devices=result.expired_devices,
        flagged_removed=result.flagged_removed,
        unacknowledged_crisis_removed=result.unacknowledged_crisis_removed,
    )
    session.add(run)
    await session.flush()
    return run


async def latest_run(session: AsyncSession) -> RetentionRun | None:
    """Return the most recent run, or ``None`` if none was ever recorded."""
    stmt = select(RetentionRun).order_by(RetentionRun.ran_at.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive datetime (SQLite returns these) as UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def is_overdue(
    last: RetentionRun | None, now: datetime, expected_run_hours: int
) -> bool:
    """Whether the job has missed its expected schedule.

    Compared against how often the job is *expected* to run, not against the
    retention window: a job that runs once per window lets a confession live
    almost twice as long as published, and that must not read as healthy.

    Args:
        last: The most recent recorded run, if any.
        now: Current time, injected.
        expected_run_hours: How often the job should run.

    Returns:
        ``True`` if there is no run, or the last one is older than that.
    """
    if last is None:
        return True
    return _as_utc(now) - _as_utc(last.ran_at) > timedelta(hours=expected_run_hours)
