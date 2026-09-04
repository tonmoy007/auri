"""Recipient-department directory: public read, admin-managed CRUD.

The mobile Forward screen reads the plain name list without credentials
(it is not sensitive, and the app has no session). Everything that reveals
or changes delivery routing requires a staff session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_role, require_hr_role
from app.database import get_async_session
from app.exceptions import (
    DepartmentInUseError,
    DepartmentNotFoundError,
    DuplicateDepartmentError,
)
from app.models.audit_event import AuditAction
from app.models.user import User
from app.services import audit_service, department_service

router = APIRouter(prefix="/departments", tags=["departments"])


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
    session: AsyncSession = Depends(get_async_session),
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
    session: AsyncSession = Depends(get_async_session),
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
    session: AsyncSession = Depends(get_async_session),
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

    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.department_write,
        source_ip=audit_service.client_ip(request),
    )
    return DepartmentResponse(
        name=department.name,
        telegram_chat_id=department.telegram_chat_id,
        is_active=department.is_active,
        undelivered_count=0,
    )


@router.put(
    "/directory/{name}",
    response_model=DepartmentResponse,
    summary="Update a department's routing or availability (admin)",
)
async def update_department(
    name: str,
    body: DepartmentUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    actor: User = Depends(require_admin_role),
) -> DepartmentResponse:
    """Set a department's Telegram chat and whether it accepts new forwards."""
    try:
        department = await department_service.update_department(
            session, name, body.telegram_chat_id, body.is_active
        )
    except DepartmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.department_write,
        source_ip=audit_service.client_ip(request),
    )
    return DepartmentResponse(
        name=department.name,
        telegram_chat_id=department.telegram_chat_id,
        is_active=department.is_active,
        undelivered_count=await department_service.undelivered_count(session, name),
    )


@router.delete(
    "/directory/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a department, unless it still owes deliveries (admin)",
)
async def delete_department(
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    actor: User = Depends(require_admin_role),
) -> None:
    """Remove a department that has nothing queued for it."""
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
    )
