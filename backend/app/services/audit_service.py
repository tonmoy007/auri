"""Writing and querying the append-only staff audit trail.

There is deliberately no update or delete function here. An audit trail that
its own subjects can edit proves nothing, so the only write path is
:func:`record`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditAction, AuditEvent, ContentTier
from app.models.user import User

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200


def client_ip(request: Request) -> str | None:
    """Return the client address for *request*, or ``None`` if unavailable."""
    return request.client.host if request.client else None


async def record(
    session: AsyncSession,
    actor: User,
    action: AuditAction,
    target_confession_id: uuid.UUID | None = None,
    content_tier: ContentTier | None = None,
    justification: str | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Append one audit row (the caller commits).

    Args:
        session: Active database session.
        actor: Staff account performing the action.
        action: What was done.
        target_confession_id: Confession acted on, when there is one.
        content_tier: How much content the actor was served.
        justification: Reason given for a raw-transcript read.
        source_ip: Client address the action came from.

    Returns:
        The persisted :class:`AuditEvent`.
    """
    event = AuditEvent(
        actor_user_id=actor.id,
        action=action.value,
        target_confession_id=target_confession_id,
        content_tier=content_tier.value if content_tier else None,
        justification=justification,
        source_ip=source_ip,
    )
    session.add(event)
    await session.flush()
    logger.info(
        "audit: %s by %s (tier=%s)",
        action.value,
        actor.id,
        content_tier.value if content_tier else "-",
    )
    return event


def _filtered(
    actor_user_id: uuid.UUID | None,
    action: AuditAction | None,
    target_confession_id: uuid.UUID | None,
    since: datetime | None,
    until: datetime | None,
) -> Select:
    """Build the filtered ``audit_events`` query shared by list and count."""
    stmt = select(AuditEvent)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action.value)
    if target_confession_id is not None:
        stmt = stmt.where(AuditEvent.target_confession_id == target_confession_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditEvent.created_at <= until)
    return stmt


async def list_events(
    session: AsyncSession,
    actor_user_id: uuid.UUID | None = None,
    action: AuditAction | None = None,
    target_confession_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    """Return a page of audit events, newest first, plus the total match count."""
    page_size = min(max(limit, 1), MAX_PAGE_SIZE)
    stmt = _filtered(actor_user_id, action, target_confession_id, since, until)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    result = await session.execute(
        stmt.order_by(AuditEvent.created_at.desc())
        .limit(page_size)
        .offset(max(offset, 0))
    )
    return list(result.scalars().all()), total or 0
