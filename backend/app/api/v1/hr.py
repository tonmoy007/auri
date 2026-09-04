"""HR-facing confession access, tiered and audited.

Reads return the de-identified summary by default. The original transcript
is a separate request that must state a reason, only works for escalated
items, and always writes an audit event — see
``app.services.confession_access`` for where that is enforced.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_hr_role
from app.database import get_async_session
from app.exceptions import (
    ConfessionNotFoundError,
    JustificationRequiredError,
    RawAccessNotPermittedError,
)
from app.models.audit_event import AuditAction, ContentTier
from app.models.confession import ConfessionStatus
from app.models.user import User
from app.services import audit_service, confession_access, insights_service

router = APIRouter(prefix="/hr", tags=["hr"])


class ConfessionSummaryResponse(BaseModel):
    """The default HR view of a confession — summary only, no transcript."""

    id: uuid.UUID
    status: ConfessionStatus
    category: str | None
    sentiment: str | None
    ai_summary: str | None
    recipient_dept: str | None
    created_at: datetime
    delivered_at: datetime | None

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
    limit: int = Query(25, ge=1, le=confession_access.MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
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
    session: AsyncSession = Depends(get_async_session),
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


@router.post(
    "/confessions/{confession_id}/raw",
    response_model=ConfessionRawResponse,
    summary="Read one confession's original transcript, with a stated reason (HR)",
)
async def read_confession_raw(
    confession_id: uuid.UUID,
    body: RawAccessRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
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


class SentimentPointResponse(BaseModel):
    """Sentiment split for one ISO week."""

    label: str
    buckets: list[BucketResponse]

    model_config = {"from_attributes": True}


class InsightsResponse(BaseModel):
    """Aggregate reporting payload, already suppressed server-side."""

    range_start: datetime
    range_end: datetime
    min_cohort: int
    total: BucketResponse
    volume_by_day: list[BucketResponse]
    volume_by_week: list[BucketResponse]
    by_category: list[BucketResponse]
    by_sentiment: list[BucketResponse]
    by_department: list[BucketResponse]
    forwarded: BucketResponse
    blind: BucketResponse
    flagged: BucketResponse
    flagged_rate: float | None
    delivered: BucketResponse
    median_hours_to_delivery: float | None
    sentiment_trend: list[SentimentPointResponse]

    model_config = {"from_attributes": True}


@router.get(
    "/insights",
    response_model=InsightsResponse,
    summary="Aggregate confession reporting with small-cohort suppression (HR)",
)
async def read_insights(
    request: Request,
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    session: AsyncSession = Depends(get_async_session),
    actor: User = Depends(require_hr_role),
) -> InsightsResponse:
    """Return aggregates over the requested window (default: last 30 days).

    Every bucket smaller than ``ANALYTICS_MIN_COHORT`` comes back
    suppressed. The client is never sent a number it is expected to hide.
    """
    end = until or datetime.now(timezone.utc)
    start = since or end - timedelta(days=insights_service.DEFAULT_RANGE_DAYS)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'since' must not be after 'until'",
        )

    insights = await insights_service.build_insights(session, start, end)
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.insights_read,
        source_ip=audit_service.client_ip(request),
    )
    return InsightsResponse.model_validate(insights)
