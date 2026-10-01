"""Recipient-department directory: public read, admin-managed CRUD.

The mobile Forward screen reads the plain name list without credentials
(it is not sensitive, and the app has no session). Everything that reveals
or changes delivery routing requires a staff session.
"""

from __future__ import annotations

import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_role, require_hr_role
from app.database import session_dependency
from app.exceptions import (
    DepartmentInUseError,
    DepartmentNotFoundError,
    DuplicateDepartmentError,
)
from app.models.audit_event import AuditAction
from app.models.user import User
from app.services import audit_service, department_service

router = APIRouter(prefix="/departments", tags=["departments"])


_WHITESPACE = re.compile(r"\s+")


def _plain(text: str) -> str:
    """Return *text* with control and format characters made into spaces.

    A department name is typed by an admin and sits at the front of an audit
    note, ahead of the routing ids. A right-to-left override or a line break in
    it could make those ids render reversed, or on another line.
    """
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in text
    )
    return _WHITESPACE.sub(" ", cleaned).strip()


def _describe_update(
    name: str,
    previous: tuple[str | None, bool] | None,
    current: tuple[str | None, bool],
) -> str:
    """Say which settings an update changed, for the audit trail."""
    old_chat, old_active = previous if previous is not None else (None, current[1])
    new_chat, new_active = current
    changes: list[str] = []
    if old_chat != new_chat:
        changes.append(f"chat {old_chat or 'none'} -> {new_chat or 'none'}")
    if old_active != new_active:
        changes.append(f"active {old_active} -> {new_active}")
    return f"updated department {_plain(name)}: " + ("; ".join(changes) or "no change")


class DepartmentsResponse(BaseModel):
    """Response body listing the configured recipient departments."""

    departments: list[str]


class DepartmentResponse(BaseModel):
    """A directory entry, including where its confessions are delivered."""

    name: str
    telegram_chat_id: str | None
    is_active: bool
    undelivered_count: int

    model_config = {"from_attributes": True}


class DepartmentCreateRequest(BaseModel):
    """Body for adding a department."""

    name: str = Field(..., min_length=1, max_length=128)
    telegram_chat_id: str | None = Field(None, max_length=64)


class DepartmentUpdateRequest(BaseModel):
    """Body for editing a department's routing or availability."""

    telegram_chat_id: str | None = Field(None, max_length=64)
    is_active: bool = True


@router.get(
    "",
    response_model=DepartmentsResponse,
    summary="List active recipient department names",
)
async def list_departments(
    session: AsyncSession = session_dependency,
) -> DepartmentsResponse:
    """Return the department names a confession can be forwarded to.

    Reads the ``departments`` table, which the backend seeds once from the
    ``DEPARTMENTS`` env value the first time it is empty.
    """
    departments = await department_service.list_departments(session)
    return DepartmentsResponse(departments=[d.name for d in departments])


@router.get(
    "/directory",
    response_model=list[DepartmentResponse],
    summary="List the full directory, including routing (HR read)",
)
async def read_directory(
    session: AsyncSession = session_dependency,
    _actor: User = Depends(require_hr_role),
) -> list[DepartmentResponse]:
    """Return every department with its chat id and outstanding queue depth."""
    departments = await department_service.list_departments(
        session, include_inactive=True
    )
    return [
        DepartmentResponse(
            name=department.name,
            telegram_chat_id=department.telegram_chat_id,
            is_active=department.is_active,
            undelivered_count=await department_service.undelivered_count(
                session, department.name
            ),
        )
        for department in departments
    ]


@router.post(
    "/directory",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a department (admin)",
)
async def create_department(
    body: DepartmentCreateRequest,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_admin_role),
) -> DepartmentResponse:
    """Add a recipient department to the directory."""
    try:
        department = await department_service.create_department(
            session, body.name.strip(), body.telegram_chat_id
        )
    except DuplicateDepartmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    response = DepartmentResponse(
        name=department.name,
        telegram_chat_id=department.telegram_chat_id,
        is_active=department.is_active,
        undelivered_count=0,
    )
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.department_write,
        source_ip=audit_service.client_ip(request),
        detail=(
            f"created department {_plain(department.name)}: "
            f"chat {department.telegram_chat_id or 'none'}"
        ),
    )
    return response


@router.put(
    "/directory/{name}",
    response_model=DepartmentResponse,
    summary="Update a department's routing or availability (admin)",
)
async def update_department(
    name: str,
    body: DepartmentUpdateRequest,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_admin_role),
) -> DepartmentResponse:
    """Set a department's Telegram chat and whether it accepts new forwards."""
    # Plain values, not the ORM row: the update below changes that object in place.
    existing = await department_service.get_by_name(session, name)
    previous = (
        (existing.telegram_chat_id, existing.is_active)
        if existing is not None
        else None
    )
    try:
        department = await department_service.update_department(
            session, name, body.telegram_chat_id, body.is_active
        )
    except DepartmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    response = DepartmentResponse(
        name=department.name,
        telegram_chat_id=department.telegram_chat_id,
        is_active=department.is_active,
        undelivered_count=await department_service.undelivered_count(session, name),
    )
    # Last database call: it commits, so nothing after it may fail.
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.department_write,
        source_ip=audit_service.client_ip(request),
        detail=_describe_update(
            name, previous, (department.telegram_chat_id, department.is_active)
        ),
    )
    return response


@router.delete(
    "/directory/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a department, unless it still owes deliveries (admin)",
)
async def delete_department(
    name: str,
    request: Request,
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_admin_role),
) -> None:
    """Remove a department that has nothing queued for it."""
    # Plain values, read before the row is gone, so the note can say where it routed.
    existing = await department_service.get_by_name(session, name)
    held = (
        (existing.telegram_chat_id, existing.is_active)
        if existing is not None
        else (None, True)
    )
    try:
        await department_service.delete_department(session, name)
    except DepartmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except DepartmentInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.department_write,
        source_ip=audit_service.client_ip(request),
        detail=(
            f"deleted department {_plain(name)}: "
            f"chat {held[0] or 'none'}, active {held[1]}"
        ),
    )
