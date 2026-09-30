"""Delivery queue — hands forwarded confessions to the Telegram bot for delivery.

Every endpoint here is service-to-service (called by the bot, not the mobile
app), so all of them require the ``X-Delivery-Api-Key`` header to match
``settings.DELIVERY_API_KEY``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_hr_role
from app.api.v1.confessions import ConfessionResponse
from app.config import settings
from app.database import session_dependency
from app.models.audit_event import AuditAction
from app.models.confession import (
    Confession,
    ConfessionStatus,
    ModerationSeverity,
    content_present,
)
from app.models.user import User
from app.services import audit_service, department_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/delivery", tags=["delivery"])

ClockDependency = Callable[[], datetime]


class DeliveryQueueItem(ConfessionResponse):
    """A queued confession plus the chat the backend says it belongs in.

    The routing target now lives in the ``departments`` table, so the bot no
    longer keeps its own copy of the mapping. Sending it here means the two
    can no longer disagree — the failure mode that made an unmapped
    department invisible to everyone who could fix it.
    """

    recipient_chat_id: str | None


class DeliveryOverviewItem(BaseModel):
    """One forwarded confession and what is (or is not) happening to it.

    ``blocked_reason`` answers the question the system could not answer
    before: *why* has this not arrived. An unmapped department used to be a
    single bot log line, invisible to the person who could fix it.
    """

    id: uuid.UUID
    recipient_dept: str | None
    recipient_chat_id: str | None
    severity: str
    created_at: datetime
    delivered_at: datetime | None
    blocked_reason: str | None


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see confessions.py)."""
    return lambda: datetime.now(timezone.utc)


def require_delivery_service(
    x_delivery_api_key: str = Header(..., alias="X-Delivery-Api-Key"),
) -> None:
    """Reject the request unless it carries the configured delivery secret.

    Fails **closed**: an unset ``DELIVERY_API_KEY`` denies every request
    rather than leaving the queue open (mirrors ``require_moderator``).
    """
    if not settings.DELIVERY_API_KEY or x_delivery_api_key != settings.DELIVERY_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing delivery credentials",
        )


async def _fetch_undelivered_or_404(
    session: AsyncSession, confession_id: uuid.UUID
) -> Confession:
    """Fetch a ``forwarded``, not-yet-delivered confession by ID, or raise ``404``."""
    stmt = select(Confession).where(
        Confession.id == confession_id,
        Confession.status == ConfessionStatus.forwarded,
        content_present(),
        Confession.delivered_at.is_(None),
        or_(
            Confession.severity != ModerationSeverity.crisis.value,
            Confession.acknowledged_at.isnot(None),
        ),
    )
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No undelivered forwarded confession found with that ID",
        )
    return confession


@router.get(
    "/queue",
    response_model=list[DeliveryQueueItem],
    dependencies=[Depends(require_delivery_service)],
    summary="List forwarded confessions awaiting Telegram delivery",
)
async def list_delivery_queue(
    session: AsyncSession = session_dependency,
) -> list[DeliveryQueueItem]:
    """Return every forwarded confession not yet marked delivered, oldest first."""
    # A crisis item never rides the automatic delivery path until a named
    # person has acknowledged it. Handing "I want to hurt myself" to a
    # department chat unattended is the failure this guard exists to stop.
    stmt = (
        select(Confession)
        .where(
            Confession.status == ConfessionStatus.forwarded,
            content_present(),
            Confession.delivered_at.is_(None),
            or_(
                Confession.severity != ModerationSeverity.crisis.value,
                Confession.acknowledged_at.isnot(None),
            ),
        )
        .order_by(Confession.created_at)
    )
    result = await session.execute(stmt)
    return [await _with_chat_id(session, item) for item in result.scalars().all()]


@router.post(
    "/{confession_id}/delivered",
    response_model=ConfessionResponse,
    dependencies=[Depends(require_delivery_service)],
    summary="Mark a forwarded confession as delivered",
)
async def mark_delivered(
    confession_id: uuid.UUID,
    session: AsyncSession = session_dependency,
    clock: ClockDependency = Depends(get_clock),
) -> Confession:
    """Set ``delivered_at`` on *confession_id*, removing it from the queue.

    404s if the confession is unknown, not ``forwarded``, or already
    delivered — the same not-found-or-already-handled shape as the
    moderation approve/reject endpoints, so a re-delivered callback (e.g.
    two overlapping bot polls) fails loudly instead of double-processing.
    """
    confession = await _fetch_undelivered_or_404(session, confession_id)
    confession.delivered_at = clock()

    await session.flush()
    await session.refresh(confession)
    return confession


async def _with_chat_id(
    session: AsyncSession, confession: Confession
) -> DeliveryQueueItem:
    """Attach the department's configured chat id to a queued confession."""
    chat_id = (
        await department_service.resolve_chat_id(session, confession.recipient_dept)
        if confession.recipient_dept
        else None
    )
    base = ConfessionResponse.model_validate(confession, from_attributes=True)
    return DeliveryQueueItem(**base.model_dump(), recipient_chat_id=chat_id)


def _blocked_reason(confession: Confession, chat_id: str | None) -> str | None:
    """Explain why *confession* has not been delivered, or ``None`` if it has.

    Ordered by what the reader can act on: a missing chat id is fixed in the
    Directory, an unacknowledged crisis item is fixed in the Queue, and
    anything else is simply waiting for the bot's next poll.
    """
    if confession.delivered_at is not None:
        return None
    if not confession.recipient_dept:
        return "No department was chosen for this confession"
    if chat_id is None:
        return (
            f"No Telegram chat is configured for {confession.recipient_dept} — "
            "set one in the Directory tab"
        )
    if (
        confession.severity == ModerationSeverity.crisis.value
        and confession.acknowledged_at is None
    ):
        return "Crisis item held until someone acknowledges it in the Queue tab"
    return "Waiting for the bot's next delivery poll"


@router.get(
    "/overview",
    response_model=list[DeliveryOverviewItem],
    summary="What was forwarded where, and what is still stuck (HR)",
)
async def read_delivery_overview(
    session: AsyncSession = session_dependency,
    _actor: User = Depends(require_hr_role),
) -> list[DeliveryOverviewItem]:
    """Return every forwarded confession with its delivery state.

    Metadata only — no transcript and no summary. Answering "did anything I
    said reach anyone" does not require reading what was said.
    """
    stmt = (
        select(Confession)
        .where(Confession.status == ConfessionStatus.forwarded, content_present())
        .order_by(Confession.created_at.desc())
    )
    result = await session.execute(stmt)

    overview = []
    for confession in result.scalars().all():
        chat_id = (
            await department_service.resolve_chat_id(session, confession.recipient_dept)
            if confession.recipient_dept
            else None
        )
        overview.append(
            DeliveryOverviewItem(
                id=confession.id,
                recipient_dept=confession.recipient_dept,
                recipient_chat_id=chat_id,
                severity=confession.severity,
                created_at=confession.created_at,
                delivered_at=confession.delivered_at,
                blocked_reason=_blocked_reason(confession, chat_id),
            )
        )
    return overview


@router.post(
    "/{confession_id}/resend",
    response_model=DeliveryOverviewItem,
    summary="Put a delivered confession back in the queue (HR)",
)
async def resend_confession(
    confession_id: uuid.UUID,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
) -> DeliveryOverviewItem:
    """Clear ``delivered_at`` so the bot sends the confession again.

    For the case the old system could not recover from: the backend recorded
    a delivery that nobody actually received. Undelivered items need no
    action — the bot already retries those on every poll.
    """
    stmt = select(Confession).where(
        Confession.id == confession_id,
        Confession.status == ConfessionStatus.forwarded,
        content_present(),
    )
    confession = (await session.execute(stmt)).scalar_one_or_none()
    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No forwarded confession found with that ID",
        )
    if confession.delivered_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This confession has not been delivered yet; it is already queued",
        )

    confession.delivered_at = None
    await session.flush()
    chat_id = (
        await department_service.resolve_chat_id(session, confession.recipient_dept)
        if confession.recipient_dept
        else None
    )
    item = DeliveryOverviewItem(
        id=confession.id,
        recipient_dept=confession.recipient_dept,
        recipient_chat_id=chat_id,
        severity=confession.severity,
        created_at=confession.created_at,
        delivered_at=None,
        blocked_reason=_blocked_reason(confession, chat_id),
    )
    # Last database call: it commits, so nothing after it may fail and leave the
    # change committed behind an error response.
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.delivery_retry,
        target_confession_id=confession.id,
        source_ip=audit_service.client_ip(request),
    )
    return item
