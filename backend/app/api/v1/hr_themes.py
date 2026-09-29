"""HR recurring-themes report and leadership digest.

Themes are computed from de-identified summaries only, and a theme smaller
than ``ANALYTICS_MIN_COHORT`` is withheld with its label (see
``app.services.theme_report``). The Markdown and CSV digest travel in the same
response as the themes, so the file a leader downloads is exactly what the
page showed — regenerating would ask the model again and could group
differently.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_hr_role
from app.api.v1.hr import BucketResponse
from app.database import get_async_session
from app.models.audit_event import AuditAction, ContentTier
from app.models.user import User
from app.services import audit_service, theme_digest, theme_service
from app.services.theme_report import ThemeReport

router = APIRouter(prefix="/hr", tags=["hr"])

ClockDependency = Callable[[], datetime]

MAX_REPORT_DAYS = 30
DEFAULT_REPORT_DAYS = 7


class ThemeResponse(BaseModel):
    """One recurring theme, already suppressed."""

    rank: int
    label: str
    confessions: int
    previous_period: BucketResponse
    negative_share: float | None
    previous_negative_share: float | None
    sentiment_change: float | None
    sentiment_status: str

    model_config = {"from_attributes": True}


class ThemesResponse(BaseModel):
    """The themes report plus its downloadable digest."""

    range_start: datetime
    range_end: datetime
    days: int
    min_cohort: int
    method: str
    notice: str | None
    analysed: BucketResponse
    truncated: bool
    themes: list[ThemeResponse]
    hidden_themes: int
    digest_markdown: str
    digest_csv: str


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see hr.py)."""
    return lambda: datetime.now(timezone.utc)


def _to_response(report: ThemeReport) -> ThemesResponse:
    """Serialise *report* together with both digest renderings."""
    return ThemesResponse(
        range_start=report.range_start,
        range_end=report.range_end,
        days=report.days,
        min_cohort=report.min_cohort,
        method=report.method,
        notice=report.notice,
        analysed=BucketResponse.model_validate(report.analysed),
        truncated=report.truncated,
        themes=[ThemeResponse.model_validate(theme) for theme in report.themes],
        hidden_themes=report.hidden_themes,
        digest_markdown=theme_digest.render_markdown(report),
        digest_csv=theme_digest.render_csv(report),
    )


@router.get(
    "/themes",
    response_model=ThemesResponse,
    summary="Recurring themes and a leadership digest, with small-cohort suppression (HR)",
)
async def read_themes(
    request: Request,
    days: int = Query(DEFAULT_REPORT_DAYS, ge=1, le=MAX_REPORT_DAYS),
    session: AsyncSession = Depends(get_async_session),
    actor: User = Depends(require_hr_role),
    clock: ClockDependency = Depends(get_clock),
) -> ThemesResponse:
    """Group the last *days* of summaries into ranked recurring themes.

    Runs the local model, so it can take a while; the dashboard triggers it
    on demand rather than on every page load.

    Args:
        request: The request (for the audited source address).
        days: Length of the period; the same length before it is compared.
        session: Database session.
        actor: The signed-in hr or admin user.
        clock: Current-time function, injectable in tests.

    Returns:
        The suppressed themes plus the Markdown and CSV digest.
    """
    report = await theme_service.generate_report(session, days, clock())
    await audit_service.record(
        session,
        actor=actor,
        action=AuditAction.themes_read,
        content_tier=ContentTier.summary,
        source_ip=audit_service.client_ip(request),
    )
    return _to_response(report)
