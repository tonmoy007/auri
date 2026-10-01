"""The Privacy panel: what is kept, for how long, who can see it, what is not protected.

Readable by HR and administrators — it is the page HR shows an employee who
asks how anonymity works. Named staff accounts are the one part reserved for
administrators: HR sees how many accounts hold each role, not who they are.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_hr_role
from app.api.v1.hr import BucketResponse
from app.database import session_dependency
from app.models.user import User, UserRole
from app.services import privacy_overview

router = APIRouter(prefix="/privacy", tags=["privacy"])

ClockDependency = Callable[[], datetime]


class FactResponse(BaseModel):
    """One plain-language statement about how data is handled."""

    id: str
    statement: str

    model_config = {"from_attributes": True}


class RetentionRunResponse(BaseModel):
    """One retention run; each count is suppressed like every other figure."""

    ran_at: datetime
    retention_hours: int
    reply_retention_days: int
    deleted: BucketResponse
    emptied_to_shell: BucketResponse
    expired_replies: BucketResponse
    expired_devices: BucketResponse
    flagged_removed: BucketResponse
    unacknowledged_crisis_removed: BucketResponse

    model_config = {"from_attributes": True}


class RetentionResponse(BaseModel):
    """The retention promise and the evidence the job is keeping it."""

    retention_hours: int
    reply_retention_days: int
    expected_run_hours: int
    last_run: RetentionRunResponse | None
    overdue: bool
    due_to_delete: BucketResponse
    due_to_empty: BucketResponse
    due_to_expire: BucketResponse

    model_config = {"from_attributes": True}


class StaffMemberResponse(BaseModel):
    """A named staff account (administrators only); never a credential."""

    email: str
    role: UserRole
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class StaffResponse(BaseModel):
    """Staff by role for everyone, and by name for administrators."""

    role_counts: dict[str, int]
    members: list[StaffMemberResponse] | None

    model_config = {"from_attributes": True}


class PrivacyOverviewResponse(BaseModel):
    """Everything the Privacy panel renders."""

    min_cohort: int
    guarantees: list[FactResponse]
    limits: list[FactResponse]
    retention: RetentionResponse
    staff: StaffResponse

    model_config = {"from_attributes": True}


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see hr.py)."""
    return lambda: datetime.now(timezone.utc)


@router.get(
    "/overview",
    response_model=PrivacyOverviewResponse,
    summary="How confession data is handled, kept and protected (HR, admin)",
)
async def read_privacy_overview(
    session: AsyncSession = session_dependency,
    actor: User = Depends(require_hr_role),
    clock: ClockDependency = Depends(get_clock),
) -> PrivacyOverviewResponse:
    """Return the guarantees, the limits, the retention status and the staff roster.

    Args:
        session: Database session.
        actor: The signed-in hr or admin user; only an admin gets named staff.
        clock: Current-time function, injectable in tests.

    Returns:
        The overview. Any figure over a small group is already suppressed.
    """
    overview = await privacy_overview.build_overview(
        session, clock(), include_members=actor.role == UserRole.admin
    )
    return PrivacyOverviewResponse.model_validate(overview)
