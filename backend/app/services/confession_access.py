"""What HR is allowed to see of a confession, enforced in the query layer.

The default view is the de-identified summary, which also carries the
staff-written reply fields (``hr_reply`` and its two timestamps) and the
severity. The raw transcript is a separate, narrower tier: it requires a
stated justification, only exists for items already escalated to human
review, and always leaves an audit trail.

Enforcement lives here rather than in the API or the UI on purpose. A
summary read never selects the transcript column at all, so raw text cannot
leak through a forgotten response-model field or a hidden column in the
dashboard — it is never fetched from the database in the first place.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    ConfessionNotFoundError,
    JustificationRequiredError,
    RawAccessNotPermittedError,
)
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity

logger = logging.getLogger(__name__)

MIN_JUSTIFICATION_LENGTH = 12
MAX_PAGE_SIZE = 100

# Raw text is only reachable for items a human already had to look at.
# An ordinary pending or delivered confession has no read-the-original path.
RAW_ELIGIBLE_STATUSES: tuple[ConfessionStatus, ...] = (ConfessionStatus.flagged,)
# Crisis items stay readable after approval: a welfare follow-up should not
# be blocked because the item was released back into the normal flow.
RAW_ELIGIBLE_SEVERITIES: tuple[str, ...] = (ModerationSeverity.crisis.value,)

_SUMMARY_COLUMNS = (
    Confession.id,
    Confession.status,
    Confession.category,
    Confession.sentiment,
    Confession.ai_summary,
    Confession.recipient_dept,
    Confession.created_at,
    Confession.delivered_at,
    Confession.severity,
    Confession.hr_reply,
    Confession.hr_replied_at,
    Confession.hr_reply_edited_at,
)


@dataclass(frozen=True)
class ConfessionSummaryView:
    """Everything HR sees by default — no transcript, ever."""

    id: uuid.UUID
    status: ConfessionStatus
    category: str | None
    sentiment: str | None
    ai_summary: str | None
    recipient_dept: str | None
    created_at: datetime
    delivered_at: datetime | None
    severity: str
    hr_reply: str | None
    hr_replied_at: datetime | None
    hr_reply_edited_at: datetime | None


@dataclass(frozen=True)
class ConfessionRawView(ConfessionSummaryView):
    """The summary plus the original transcript, for justified reads only."""

    transcript: str


def _summary_from_row(row) -> ConfessionSummaryView:
    """Build a summary view from a column-scoped result row."""
    return ConfessionSummaryView(
        id=row.id,
        status=row.status,
        category=row.category,
        sentiment=row.sentiment,
        ai_summary=row.ai_summary,
        recipient_dept=row.recipient_dept,
        created_at=row.created_at,
        delivered_at=row.delivered_at,
        severity=row.severity,
        hr_reply=row.hr_reply,
        hr_replied_at=row.hr_replied_at,
        hr_reply_edited_at=row.hr_reply_edited_at,
    )


def _filtered(
    status: ConfessionStatus | None,
    category: str | None,
    department: str | None,
    since: datetime | None,
    until: datetime | None,
    replied: bool | None = None,
) -> Select:
    """Build the filtered summary query shared by the list and its count."""
    stmt = select(*_SUMMARY_COLUMNS).where(
        Confession.status != ConfessionStatus.deleted
    )
    if status is not None:
        stmt = stmt.where(Confession.status == status)
    if category is not None:
        stmt = stmt.where(Confession.category == category)
    if department is not None:
        stmt = stmt.where(Confession.recipient_dept == department)
    if since is not None:
        stmt = stmt.where(Confession.created_at >= since)
    if until is not None:
        stmt = stmt.where(Confession.created_at <= until)
    if replied is not None:
        stmt = stmt.where(
            Confession.hr_replied_at.is_not(None)
            if replied
            else Confession.hr_replied_at.is_(None)
        )
    return stmt


async def list_summaries(
    session: AsyncSession,
    status: ConfessionStatus | None = None,
    category: str | None = None,
    department: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 25,
    offset: int = 0,
    replied: bool | None = None,
) -> tuple[list[ConfessionSummaryView], int]:
    """Return a page of summary views, newest first, plus the total matches.

    Soft-deleted confessions are excluded: a confessor who deleted theirs
    has withdrawn it, and HR reporting must honour that. *replied* keeps only
    confessions that do (``True``) or do not (``False``) have an HR reply.
    """
    page_size = min(max(limit, 1), MAX_PAGE_SIZE)
    stmt = _filtered(status, category, department, since, until, replied)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    result = await session.execute(
        stmt.order_by(Confession.created_at.desc())
        .limit(page_size)
        .offset(max(offset, 0))
    )
    return [_summary_from_row(row) for row in result.all()], total or 0


async def read_summary(
    session: AsyncSession, confession_id: uuid.UUID
) -> ConfessionSummaryView:
    """Return one confession's summary view.

    Raises:
        ConfessionNotFoundError: If no visible confession has that ID.
    """
    stmt = select(*_SUMMARY_COLUMNS).where(
        Confession.id == confession_id,
        Confession.status != ConfessionStatus.deleted,
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise ConfessionNotFoundError(f"no visible confession with id {confession_id}")
    return _summary_from_row(row)


async def read_raw(
    session: AsyncSession, confession_id: uuid.UUID, justification: str
) -> ConfessionRawView:
    """Return one confession's transcript, if the read is permitted.

    Args:
        session: Active database session.
        confession_id: Confession to read.
        justification: Why the original text is needed. Recorded verbatim in
            the audit trail by the caller.

    Raises:
        JustificationRequiredError: If *justification* is too short to mean
            anything — "ok" is not a reason.
        ConfessionNotFoundError: If no visible confession has that ID.
        RawAccessNotPermittedError: If the confession is not in a status
            that permits reading the original text.
    """
    reason = justification.strip()
    if len(reason) < MIN_JUSTIFICATION_LENGTH:
        raise JustificationRequiredError(
            f"a justification of at least {MIN_JUSTIFICATION_LENGTH} characters "
            "is required to read a raw transcript"
        )

    stmt = select(*_SUMMARY_COLUMNS, Confession.transcript).where(
        Confession.id == confession_id,
        Confession.status != ConfessionStatus.deleted,
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise ConfessionNotFoundError(f"no visible confession with id {confession_id}")

    if (
        row.status not in RAW_ELIGIBLE_STATUSES
        and row.severity not in RAW_ELIGIBLE_SEVERITIES
    ):
        raise RawAccessNotPermittedError(
            f"confessions in status '{row.status.value}' do not expose their "
            "original transcript; only escalated items do"
        )

    summary = _summary_from_row(row)
    return ConfessionRawView(**summary.__dict__, transcript=row.transcript)
