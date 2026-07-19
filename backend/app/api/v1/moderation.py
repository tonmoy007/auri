"""Moderation queue — review, approve, or reject AI-flagged confessions.

Every endpoint here is service-to-service (called by the Telegram bot on a
moderator's behalf, not by the mobile app), so all of them require the
``X-Moderation-Api-Key`` header to match ``settings.MODERATION_API_KEY``.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.confessions import ConfessionResponse
from app.config import settings
from app.database import get_async_session
from app.models.confession import Confession, ConfessionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/moderation", tags=["moderation"])


def require_moderator(
    x_moderation_api_key: str = Header(..., alias="X-Moderation-Api-Key"),
) -> None:
    """Reject the request unless it carries the configured moderation secret.

    Fails **closed**: an unset ``MODERATION_API_KEY`` (e.g. a missed deploy
    config step) denies every request rather than leaving the queue open.
    """
    if (
        not settings.MODERATION_API_KEY
        or x_moderation_api_key != settings.MODERATION_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing moderation credentials",
        )


async def _fetch_flagged_or_404(
    session: AsyncSession, confession_id: uuid.UUID
) -> Confession:
    """Fetch a ``flagged`` confession by ID, or raise ``404``."""
    stmt = select(Confession).where(
        Confession.id == confession_id,
        Confession.status == ConfessionStatus.flagged,
    )
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No flagged confession found with that ID",
        )
    return confession


@router.get(
    "/queue",
    response_model=list[ConfessionResponse],
    dependencies=[Depends(require_moderator)],
    summary="List confessions currently flagged for moderator review",
)
async def list_moderation_queue(
    session: AsyncSession = Depends(get_async_session),
) -> list[Confession]:
    """Return every confession awaiting moderator review, oldest first."""
    stmt = (
        select(Confession)
        .where(Confession.status == ConfessionStatus.flagged)
        .order_by(Confession.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/{confession_id}/approve",
    response_model=ConfessionResponse,
    dependencies=[Depends(require_moderator)],
    summary="Approve a flagged confession, returning it to the normal pending flow",
)
async def approve_confession(
    confession_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Move *confession_id* from ``flagged`` back to ``pending``."""
    confession = await _fetch_flagged_or_404(session, confession_id)
    confession.status = ConfessionStatus.pending

    await session.flush()
    await session.refresh(confession)
    return confession


@router.post(
    "/{confession_id}/reject",
    response_model=ConfessionResponse,
    dependencies=[Depends(require_moderator)],
    summary="Reject a flagged confession, soft-deleting it",
)
async def reject_confession(
    confession_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Move *confession_id* from ``flagged`` to ``deleted``."""
    confession = await _fetch_flagged_or_404(session, confession_id)
    confession.status = ConfessionStatus.deleted

    await session.flush()
    await session.refresh(confession)
    return confession
