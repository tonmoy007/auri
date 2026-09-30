"""Moderation queue — review, approve, or reject AI-flagged confessions.

Two callers, one queue. The Telegram bot calls these endpoints on a
moderator's behalf with ``X-Moderation-Api-Key`` (Phase 4.6, unchanged), and
the dashboard calls them with a staff session. The difference matters: a
session has a named actor, so the decision records **who** made it and
writes an audit event. The bot path stays anonymous, exactly as before.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.v1.confessions import ConfessionResponse
from app.config import settings
from app.database import session_dependency
from app.models.audit_event import AuditAction, ContentTier
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.user import User, UserRole
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/moderation", tags=["moderation"])

ClockDependency = Callable[[], datetime]

# Roles allowed to work the queue from the dashboard. Moderators exist for
# exactly this; HR shares it because flagged items are their escalations.
QUEUE_ROLES = frozenset({UserRole.moderator, UserRole.hr, UserRole.admin})


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see confessions.py)."""
    return lambda: datetime.now(timezone.utc)


def _matches_moderation_key(candidate: str | None) -> bool:
    """Return ``True`` if *candidate* is the configured moderation secret.

    Fails **closed**: an unset ``MODERATION_API_KEY`` (e.g. a missed deploy
    config step) matches nothing rather than leaving the queue open.
    """
    return bool(
        candidate
        and settings.MODERATION_API_KEY
        and candidate == settings.MODERATION_API_KEY
    )


async def moderation_actor(
    authorization: str | None = Header(None),
    x_moderation_api_key: str | None = Header(None, alias="X-Moderation-Api-Key"),
    session: AsyncSession = session_dependency,
) -> User | None:
    """Authenticate the caller as the bot **or** a queue-capable staff member.

    Returns:
        The signed-in staff account, or ``None`` for the legacy bot path
        (which has no named actor and therefore records none).
    """
    if _matches_moderation_key(x_moderation_api_key):
        return None

    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing moderation credentials",
        )

    user = await get_current_user(authorization=authorization, session=session)
    if user.role not in QUEUE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role does not have access to the moderation queue",
        )
    return user


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


async def _claim_flagged(
    session: AsyncSession, confession_id: uuid.UUID, **values: Any
) -> None:
    """Atomically apply *values* to a confession that is *still flagged*.

    The earlier fetch is an unlocked read, so two moderators can both see the
    item as flagged. The status condition in this UPDATE means only one of them
    changes it: the other matches no row, and is refused rather than silently
    overwriting the first decision. That holds for approve and reject, which
    leave the flagged state. Acknowledging does not, so a repeat acknowledge of a
    still-flagged item is accepted and restamps who saw it and when (unchanged).

    Raises:
        HTTPException: 404 if the confession is no longer flagged.
    """
    result = await session.execute(
        update(Confession)
        .where(
            Confession.id == confession_id,
            Confession.status == ConfessionStatus.flagged,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult, result).rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No flagged confession found with that ID",
        )


async def _record_decision(
    session: AsyncSession,
    request: Request,
    actor: User | None,
    confession: Confession,
    action: AuditAction,
    now: datetime,
) -> None:
    """Stamp the reviewer on *confession* and audit the decision.

    The bot path passes ``actor=None``: the shared key names no person, so no
    reviewer is stamped, but the decision is still recorded, attributed to
    ``telegram-bot``. Inventing a person would be a lie in the trail; recording
    nothing would leave Telegram moderation invisible.
    """
    if actor is None:
        await session.flush()
        await session.refresh(confession)
        await audit_service.record(
            session,
            actor=None,
            actor_label=audit_service.BOT_ACTOR_LABEL,
            action=action,
            target_confession_id=confession.id,
            content_tier=ContentTier.raw,
            source_ip=audit_service.client_ip(request),
        )
        return

    confession.reviewed_by = actor.id
    confession.reviewed_at = now
    # Flush and reload BEFORE recording: record() commits, so it must be the last
    # database call, or a failure after it leaves the decision committed behind
    # an error response.
    await session.flush()
    await session.refresh(confession)
    await audit_service.record(
        session,
        actor=actor,
        action=action,
        target_confession_id=confession.id,
        content_tier=ContentTier.raw,
        source_ip=audit_service.client_ip(request),
    )


@router.get(
    "/queue",
    response_model=list[ConfessionResponse],
    summary="List confessions currently flagged for moderator review",
)
async def list_moderation_queue(
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User | None = Depends(moderation_actor),
) -> list[Confession]:
    """Return every confession awaiting moderator review, oldest first.

    The queue serves full transcripts — reviewing content is the job — so a
    staff listing is audited at the ``raw`` tier.
    """
    # Crisis items sort ahead of everything else regardless of age: the
    # queue is worked top-down, and a self-harm disclosure must not wait
    # behind a week-old policy flag.
    stmt = (
        select(Confession)
        .where(Confession.status == ConfessionStatus.flagged)
        .order_by(
            (Confession.severity != ModerationSeverity.crisis.value),
            Confession.created_at,
        )
    )
    result = await session.execute(stmt)
    queue = list(result.scalars().all())

    if actor is not None:
        await audit_service.record(
            session,
            actor=actor,
            action=AuditAction.confession_list,
            content_tier=ContentTier.raw,
            source_ip=audit_service.client_ip(request),
        )
    return queue


@router.post(
    "/{confession_id}/approve",
    response_model=ConfessionResponse,
    summary="Approve a flagged confession, returning it to the normal pending flow",
)
async def approve_confession(
    confession_id: uuid.UUID,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User | None = Depends(moderation_actor),
    clock: ClockDependency = Depends(get_clock),
) -> Confession:
    """Move *confession_id* from ``flagged`` back to ``pending``."""
    confession = await _fetch_flagged_or_404(session, confession_id)
    await _claim_flagged(session, confession.id, status=ConfessionStatus.pending)
    await _record_decision(
        session, request, actor, confession, AuditAction.moderation_approve, clock()
    )
    return confession


@router.post(
    "/{confession_id}/reject",
    response_model=ConfessionResponse,
    summary="Reject a flagged confession, soft-deleting it",
)
async def reject_confession(
    confession_id: uuid.UUID,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User | None = Depends(moderation_actor),
    clock: ClockDependency = Depends(get_clock),
) -> Confession:
    """Move *confession_id* from ``flagged`` to ``deleted``."""
    confession = await _fetch_flagged_or_404(session, confession_id)
    await _claim_flagged(session, confession.id, status=ConfessionStatus.deleted)
    await _record_decision(
        session, request, actor, confession, AuditAction.moderation_reject, clock()
    )
    return confession


@router.post(
    "/{confession_id}/acknowledge",
    response_model=ConfessionResponse,
    summary="Record that a named person has seen a crisis item",
)
async def acknowledge_crisis(
    confession_id: uuid.UUID,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User | None = Depends(moderation_actor),
    clock: ClockDependency = Depends(get_clock),
) -> Confession:
    """Acknowledge a crisis item, stopping its SLA clock.

    Acknowledgement is separate from approve/reject on purpose: it answers
    "has a human actually seen this yet?", which is the only question that
    matters while someone may be in danger. It requires a named actor, so
    the bot's anonymous shared-key path cannot silence the banner.
    """
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acknowledging a crisis item requires a signed-in account",
        )

    confession = await _fetch_flagged_or_404(session, confession_id)
    if confession.severity != ModerationSeverity.crisis.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only crisis items are acknowledged",
        )

    await _claim_flagged(
        session,
        confession.id,
        acknowledged_by=actor.id,
        acknowledged_at=clock(),
    )
    await session.refresh(confession)
    # Last database call: it commits, so nothing after it may fail.
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.crisis_acknowledge,
        target_confession_id=confession.id,
        content_tier=ContentTier.raw,
        source_ip=audit_service.client_ip(request),
    )
    return confession
