"""Writing and querying the append-only staff audit trail.

There is deliberately no update or delete function here. An audit trail that
its own subjects can edit proves nothing, so the only write path is
:func:`record`.

:func:`record` also **commits**. The request session commits when its dependency
exits, which is declared function-scoped (see ``app.database.session_dependency``)
so that it runs right after the handler and before the response goes out. Committing
here as well makes the audit row (and the change it accounts for, which every caller
writes first) durable at the point the handler chooses, and keeps it durable even if
a later step in the handler fails; if this commit fails the request fails and nothing
is released.
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
MAX_DETAIL_CHARS = 500
# Who the trail names for decisions made with the bot's shared moderation key:
# the key identifies nobody, so this says so instead of inventing a person.
BOT_ACTOR_LABEL = "telegram-bot"


def client_ip(request: Request) -> str | None:
    """Return the client address for *request*, or ``None`` if unavailable."""
    return request.client.host if request.client else None


async def record(
    session: AsyncSession,
    actor: User | None,
    action: AuditAction,
    target_confession_id: uuid.UUID | None = None,
    content_tier: ContentTier | None = None,
    justification: str | None = None,
    source_ip: str | None = None,
    detail: str | None = None,
    actor_label: str | None = None,
) -> AuditEvent:
    """Append one audit row and commit it (with any change already pending).

    Args:
        session: Active database session.
        actor: Staff account performing the action.
        action: What was done.
        target_confession_id: Confession acted on, when there is one.
        content_tier: How much content the actor was served.
        justification: Reason given for a raw-transcript read.
        source_ip: Client address the action came from.
        detail: What changed, for an action that alters settings. Never put
            confession content here; it is capped at ``MAX_DETAIL_CHARS``.
        actor_label: Who acted when *actor* is ``None`` (for example
            ``BOT_ACTOR_LABEL``). One of the two is required.

    Raises:
        ValueError: If neither an actor nor a label is given.

    Returns:
        The persisted :class:`AuditEvent`.

    Note:
        Commits mid-request, so it must be the caller's **last database call**
        (build the response first), and the caller's ORM objects must survive a
        commit: the production session factory sets ``expire_on_commit=False``,
        which a test pins.
    """
    if actor is None and not actor_label:
        raise ValueError("an audit row needs an actor account or an actor label")
    event = AuditEvent(
        actor_user_id=actor.id if actor else None,
        actor_label=actor_label if actor is None else None,
        action=action.value,
        target_confession_id=target_confession_id,
        content_tier=content_tier.value if content_tier else None,
        justification=justification,
        source_ip=source_ip,
        detail=detail[:MAX_DETAIL_CHARS] if detail else None,
    )
    # Read what the log line needs before committing: nothing after the commit
    # should be able to fail and turn a durable row into an error response.
    actor_id = actor.id if actor else actor_label
    tier = content_tier.value if content_tier else "-"
    session.add(event)
    await session.commit()
    logger.info("audit: %s by %s (tier=%s)", action.value, actor_id, tier)
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
