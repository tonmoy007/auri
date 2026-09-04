"""Read API for the staff audit trail (admin-only, append-only).

There is no create, update, or delete endpoint here by design — rows are
written by the code paths that read confession content, and nothing can
rewrite them afterwards.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_role
from app.database import get_async_session
from app.models.audit_event import AuditAction
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    """One audit row as returned to the dashboard."""

    id: uuid.UUID
    actor_user_id: uuid.UUID
    action: str
    target_confession_id: uuid.UUID | None
    content_tier: str | None
    justification: str | None
    source_ip: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditPage(BaseModel):
    """A page of audit events plus the total number of matches."""

    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


@router.get(
    "",
    response_model=AuditPage,
    summary="List staff audit events (admin only)",
)
async def list_audit_events(
    actor_user_id: uuid.UUID | None = Query(None),
    action: AuditAction | None = Query(None),
    target_confession_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=audit_service.MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    _admin: User = Depends(require_admin_role),
) -> AuditPage:
    """Return audit events newest-first, filtered and paginated.

    Restricted to administrators: the trail records which HR staff read
    which confession, so it is itself sensitive.
    """
    events, total = await audit_service.list_events(
        session,
        actor_user_id=actor_user_id,
        action=action,
        target_confession_id=target_confession_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AuditPage(
        items=[AuditEventResponse.model_validate(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/actions",
    response_model=list[str],
    summary="List the audit action values the dashboard can filter by",
)
async def list_audit_actions(
    _admin: User = Depends(require_admin_role),
) -> list[str]:
    """Return every known :class:`AuditAction` value."""
    return [action.value for action in AuditAction]
