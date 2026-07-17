"""Read-only recipient-department directory for the Forward flow."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import parse_comma_separated_list, settings

router = APIRouter(prefix="/departments", tags=["departments"])


class DepartmentsResponse(BaseModel):
    """Response body listing the configured recipient departments."""

    departments: list[str]


@router.get(
    "",
    response_model=DepartmentsResponse,
    summary="List configured recipient departments",
)
async def list_departments() -> DepartmentsResponse:
    """Return the admin-managed department list a confession can be forwarded to.

    Source of truth is the ``DEPARTMENTS`` setting (comma-separated) — there
    is no LDAP/HRIS integration, per the plan's resolved decisions.
    """
    return DepartmentsResponse(
        departments=parse_comma_separated_list(settings.DEPARTMENTS)
    )
