"""Tests for aggregate reporting and its k-anonymity suppression.

The suppression rule is the privacy guarantee, so it is tested at its exact
boundary: N-1 is withheld, N is shown (AGENTS.md §16.2 — specific outcomes,
not "is not None").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from app.services import insights_service, settings_service
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

RANGE_END = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
RANGE_START = RANGE_END - timedelta(days=30)
MIN_COHORT = 5


@pytest.fixture(autouse=True)
def cohort_threshold(monkeypatch):
    """Pin the suppression threshold and clear any live-config override."""
    monkeypatch.setattr(
        insights_service.settings, "ANALYTICS_MIN_COHORT", MIN_COHORT, raising=False
    )
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)


async def _add(
    session: AsyncSession,
    count: int,
    category: str = "work",
    sentiment: str = "negative",
    department: str | None = None,
    status: ConfessionStatus = ConfessionStatus.pending,
    created_at: datetime = RANGE_END - timedelta(days=1),
    delivered_at: datetime | None = None,
) -> None:
    for index in range(count):
        session.add(
            Confession(
                device_token_hash=f"{index:032d}",
                voice_mask="warm",
                transcript="words",
                ai_summary="summary",
                category=category,
                sentiment=sentiment,
                pii_stripped=True,
                status=status,
                recipient_dept=department,
                created_at=created_at,
                delivered_at=delivered_at,
            )
        )
    await session.commit()


async def _build(session: AsyncSession):
    return await insights_service.build_insights(session, RANGE_START, RANGE_END)


# ── Suppression boundary ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bucket_one_below_the_threshold_is_suppressed(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT - 1, category="grief")

    # Act
    insights = await _build(db_session)

    # Assert
    grief = next(b for b in insights.by_category if b.label == "grief")
    assert grief.suppressed is True
    assert grief.count is None


@pytest.mark.asyncio
async def test_bucket_exactly_at_the_threshold_is_reported(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT, category="grief")

    # Act
    insights = await _build(db_session)

    # Assert
    grief = next(b for b in insights.by_category if b.label == "grief")
    assert grief.suppressed is False
    assert grief.count == MIN_COHORT


@pytest.mark.asyncio
async def test_empty_bucket_reports_zero_rather_than_being_suppressed(
    db_session: AsyncSession,
) -> None:
    # Arrange — a day with nobody in it identifies nobody
    await _add(db_session, MIN_COHORT, created_at=RANGE_END - timedelta(days=1))

    # Act
    insights = await _build(db_session)

    # Assert
    empty_days = [b for b in insights.volume_by_day if b.count == 0]
    assert empty_days[0].suppressed is False


@pytest.mark.asyncio
async def test_a_small_department_is_suppressed_while_a_large_one_is_shown(
    db_session: AsyncSession,
) -> None:
    # Arrange — the exact de-anonymisation vector the rule exists for
    await _add(db_session, 2, department="Facilities")
    await _add(db_session, MIN_COHORT + 3, department="Engineering")

    # Act
    insights = await _build(db_session)

    # Assert
    facilities = next(b for b in insights.by_department if b.label == "Facilities")
    engineering = next(b for b in insights.by_department if b.label == "Engineering")
    assert facilities.count is None
    assert engineering.count == MIN_COHORT + 3


@pytest.mark.asyncio
async def test_raising_the_threshold_via_live_config_suppresses_more(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT + 1, category="work")
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "50"

    # Act
    insights = await _build(db_session)

    # Assert
    work = next(b for b in insights.by_category if b.label == "work")
    assert work.suppressed is True
    assert insights.min_cohort == 50


@pytest.mark.asyncio
async def test_a_threshold_below_two_is_refused(db_session: AsyncSession) -> None:
    # Arrange — a bucket of one *is* a person, so 1 is not an allowed floor
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "1"

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.min_cohort == 2


@pytest.mark.asyncio
async def test_a_malformed_threshold_falls_back_to_the_configured_default(
    db_session: AsyncSession,
) -> None:
    # Arrange
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "not-a-number"

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.min_cohort == MIN_COHORT


# ── Aggregates ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forwarded_and_blind_split_covers_every_confession(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT, status=ConfessionStatus.forwarded)
    await _add(db_session, MIN_COHORT, status=ConfessionStatus.pending)

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.forwarded.count == MIN_COHORT
    assert insights.blind.count == MIN_COHORT
    assert insights.total.count == MIN_COHORT * 2


@pytest.mark.asyncio
async def test_flagged_rate_is_reported_once_both_cohorts_are_large_enough(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT, status=ConfessionStatus.flagged)
    await _add(db_session, MIN_COHORT * 3, status=ConfessionStatus.pending)

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.flagged_rate == 0.25


@pytest.mark.asyncio
async def test_flagged_rate_is_withheld_when_the_flagged_cohort_is_small(
    db_session: AsyncSession,
) -> None:
    # Arrange — publishing 2/40 tells HR there are exactly two flagged people
    await _add(db_session, 2, status=ConfessionStatus.flagged)
    await _add(db_session, MIN_COHORT * 4, status=ConfessionStatus.pending)

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.flagged_rate is None


@pytest.mark.asyncio
async def test_median_time_to_delivery_is_computed_from_delivered_items(
    db_session: AsyncSession,
) -> None:
    # Arrange
    created = RANGE_END - timedelta(days=2)
    await _add(
        db_session,
        MIN_COHORT,
        status=ConfessionStatus.forwarded,
        created_at=created,
        delivered_at=created + timedelta(hours=4),
    )

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.median_hours_to_delivery == 4.0


@pytest.mark.asyncio
async def test_median_time_to_delivery_is_withheld_for_too_few_deliveries(
    db_session: AsyncSession,
) -> None:
    # Arrange
    created = RANGE_END - timedelta(days=2)
    await _add(
        db_session,
        2,
        status=ConfessionStatus.forwarded,
        created_at=created,
        delivered_at=created + timedelta(hours=4),
    )

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.median_hours_to_delivery is None


@pytest.mark.asyncio
async def test_deleted_confessions_are_excluded_from_every_aggregate(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT, status=ConfessionStatus.deleted)

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.total.count == 0


@pytest.mark.asyncio
async def test_confessions_outside_the_window_are_excluded(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(db_session, MIN_COHORT, created_at=RANGE_START - timedelta(days=5))

    # Act
    insights = await _build(db_session)

    # Assert
    assert insights.total.count == 0


@pytest.mark.asyncio
async def test_sentiment_trend_buckets_each_week_separately(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await _add(
        db_session,
        MIN_COHORT,
        sentiment="positive",
        created_at=RANGE_END - timedelta(days=1),
    )

    # Act
    insights = await _build(db_session)

    # Assert
    week = insights.sentiment_trend[0]
    positive = next(b for b in week.buckets if b.label == "positive")
    assert positive.count == MIN_COHORT


# ── API surface ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_endpoint_returns_suppressed_buckets_to_hr(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add(db_session, 2, category="grief", created_at=datetime.now(timezone.utc))

    # Act
    response = await api_client.get("/api/v1/hr/insights", headers=headers)

    # Assert
    body = response.json()
    grief = next(b for b in body["by_category"] if b["label"] == "grief")
    assert response.status_code == 200
    assert grief == {"label": "grief", "count": None, "suppressed": True}


@pytest.mark.asyncio
async def test_insights_endpoint_rejects_an_inverted_date_range(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(
        "/api/v1/hr/insights?since=2026-09-04T00:00:00Z&until=2026-09-01T00:00:00Z",
        headers=headers,
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_insights_endpoint_refuses_a_moderator(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")

    # Act
    response = await api_client.get("/api/v1/hr/insights", headers=headers)

    # Assert
    assert response.status_code == 403
