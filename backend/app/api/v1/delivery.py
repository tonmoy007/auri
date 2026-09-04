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

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.confessions import ConfessionResponse
from app.config import settings
from app.database import get_async_session
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.services import department_service

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
    session: AsyncSession = Depends(get_async_session),
) -> list[DeliveryQueueItem]:
    """Return every forwarded confession not yet marked delivered, oldest first."""
    # A crisis item never rides the automatic delivery path until a named
    # person has acknowledged it. Handing "I want to hurt myself" to a
    # department chat unattended is the failure this guard exists to stop.
    stmt = (
        select(Confession)
        .where(
            Confession.status == ConfessionStatus.forwarded,
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
    session: AsyncSession = Depends(get_async_session),
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
