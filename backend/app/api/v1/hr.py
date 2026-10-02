"""HR-facing confession access, tiered and audited.

Reads return the de-identified summary by default. The original transcript
is a separate request that must state a reason, only works for escalated
items, and always writes an audit event — see
``app.services.confession_access`` for where that is enforced.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_hr_role
from app.database import session_dependency
from app.exceptions import (
    ConfessionNotFoundError,
    HrReplyInvalidError,
    JustificationRequiredError,
    RawAccessNotPermittedError,
    ReplyNotPermittedError,
)
from app.models.audit_event import AuditAction, ContentTier
from app.models.confession import ConfessionStatus
from app.models.user import User
from app.services import (
    audit_service,
    confession_access,
    hr_reply_service,
    insight_weeks,
)

router = APIRouter(prefix="/hr", tags=["hr"])

ClockDependency = Callable[[], datetime]


class ConfessionSummaryResponse(BaseModel):
    """The default HR view of a confession — summary only, no transcript.

    Carries the staff-written reply and its timestamps, never its author:
    who wrote it lives only in the audit trail.
    """

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

    model_config = {"from_attributes": True}


class ConfessionRawResponse(ConfessionSummaryResponse):
    """The summary plus the original transcript, for a justified read."""

    transcript: str


class ConfessionPage(BaseModel):
    """A page of confession summaries plus the total number of matches."""

    items: list[ConfessionSummaryResponse]
    total: int
    limit: int
    offset: int


class RawAccessRequest(BaseModel):
    """Why the original transcript is needed."""

    justification: str = Field(..., min_length=1, max_length=2000)


class HrReplyRequest(BaseModel):
    """The organisation's reply to a confessor.

    The service is authoritative: it strips the text and applies the limit
    to what is stored. The bounds here only reject an obviously unusable
    body before it reaches the service.
    """

    reply: str = Field(
        ..., min_length=1, max_length=hr_reply_service.MAX_HR_REPLY_LENGTH
    )


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see delivery.py)."""
    return lambda: datetime.now(timezone.utc)


@router.get(
    "/confessions",
    response_model=ConfessionPage,
    summary="List confession summaries (HR)",
)
async def list_confessions(
    request: Request,
    status_filter: ConfessionStatus | None = Query(None, alias="status"),
    category: str | None = Query(None, max_length=128),
    department: str | None = Query(None, max_length=128),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    replied: bool | None = Query(None),
    limit: int = Query(25, ge=1, le=confession_access.MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
) -> ConfessionPage:
    """Return de-identified confession summaries, newest first."""
    items, total = await confession_access.list_summaries(
        session,
        status=status_filter,
        category=category,
        department=department,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        replied=replied,
    )
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.confession_list,
        content_tier=ContentTier.summary,
        source_ip=audit_service.client_ip(request),
    )
    return ConfessionPage(
        items=[ConfessionSummaryResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/confessions/{confession_id}",
    response_model=ConfessionSummaryResponse,
    summary="Read one confession's summary (HR)",
)
async def read_confession_summary(
    confession_id: uuid.UUID,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
) -> ConfessionSummaryResponse:
    """Return the de-identified summary for one confession."""
    try:
        summary = await confession_access.read_summary(session, confession_id)
    except ConfessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.confession_read,
        target_confession_id=confession_id,
        content_tier=ContentTier.summary,
        source_ip=audit_service.client_ip(request),
    )
    return ConfessionSummaryResponse.model_validate(summary)


async def _save_reply_or_http_error(
    session: AsyncSession, confession_id: uuid.UUID, text: str, now: datetime
) -> hr_reply_service.ReplyWriteResult:
    """Write the reply, translating domain refusals into HTTP errors.

    Each message is a fixed string from the service and never contains the
    submitted text.
    """
    try:
        return await hr_reply_service.write_reply(session, confession_id, text, now)
    except HrReplyInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ConfessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ReplyNotPermittedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.put(
    "/confessions/{confession_id}/reply",
    response_model=ConfessionSummaryResponse,
    summary="Write or edit the organisation's reply to a confession (HR)",
)
async def write_confession_reply(
    confession_id: uuid.UUID,
    body: HrReplyRequest,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
    clock: ClockDependency = Depends(get_clock),
) -> ConfessionSummaryResponse:
    """Save the reply the confessor will see in their own history.

    The author goes to the audit trail, never onto the confession. A save
    that changes the text is audited as ``hr_reply.write``. Re-saving
    identical text writes nothing, but it still served summary content, so
    it is audited as a summary-tier read. Failed requests write no audit
    row, and the reply text is never logged or audited.
    """
    result = await _save_reply_or_http_error(
        session, confession_id, body.reply, clock()
    )
    await audit_service.record(
        session,
        actor=actor,
        action=(
            AuditAction.hr_reply_write
            if result.changed
            else AuditAction.confession_read
        ),
        target_confession_id=confession_id,
        content_tier=ContentTier.summary,
        justification=None,
        source_ip=audit_service.client_ip(request),
    )
    return ConfessionSummaryResponse.model_validate(result.view)


@router.post(
    "/confessions/{confession_id}/raw",
    response_model=ConfessionRawResponse,
    summary="Read one confession's original transcript, with a stated reason (HR)",
)
async def read_confession_raw(
    confession_id: uuid.UUID,
    body: RawAccessRequest,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
) -> ConfessionRawResponse:
    """Return the original transcript for an escalated confession.

    A POST rather than a GET because it is a recorded act, not a passive
    read: it carries a justification body and always writes an audit event.
    """
    try:
        raw = await confession_access.read_raw(
            session, confession_id, body.justification
        )
    except JustificationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ConfessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except RawAccessNotPermittedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.confession_read,
        target_confession_id=confession_id,
        content_tier=ContentTier.raw,
        justification=body.justification.strip(),
        source_ip=audit_service.client_ip(request),
    )
    return ConfessionRawResponse.model_validate(raw)


class BucketResponse(BaseModel):
    """One aggregate number, or an explicit refusal to report it.

    ``suppressed`` is not the same as zero: it means "there were people
    here, but too few to show without identifying them".
    """

    label: str
    count: int | None
    suppressed: bool

    model_config = {"from_attributes": True}


class WeekInsightsResponse(BaseModel):
    """One fixed week. Until it is frozen, ``total`` is null and every list empty."""

    label: str
    start: date
    end: date
    frozen: bool
    total: BucketResponse | None
    by_day: list[BucketResponse]
    by_category: list[BucketResponse]
    by_sentiment: list[BucketResponse]
    by_department: list[BucketResponse]
    by_status: list[BucketResponse]
    delivery: list[BucketResponse]
    delivery_time: list[BucketResponse]

    model_config = {"from_attributes": True}


class InsightsResponse(BaseModel):
    """A month as its fixed weeks, already suppressed server-side."""

    month: str
    min_cohort: int
    weeks: list[WeekInsightsResponse]

    model_config = {"from_attributes": True}


@router.get(
    "/insights",
    response_model=InsightsResponse,
    summary="Aggregate reporting by fixed week, with small-cohort suppression (HR)",
)
async def read_insights(
    request: Request,
    month: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Calendar month as YYYY-MM; the current UTC month when omitted",
    ),
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
    clock: ClockDependency = Depends(get_clock),
) -> InsightsResponse:
    """Return one month as its four fixed weeks (plan 14.1).

    Only fixed periods can be asked for, so two answers never overlap partly and
    cannot be subtracted to isolate a few days. A week has figures only once it is
    frozen; every figure below ``ANALYTICS_MIN_COHORT`` comes back suppressed.
    """
    now = clock()
    year, number = (
        (int(p) for p in month.split("-")) if month else (now.year, now.month)
    )
    insights = await insight_weeks.build_month(session, year, number, now)
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.insights_read,
        source_ip=audit_service.client_ip(request),
    )
    return InsightsResponse.model_validate(insights)
