"""The small-cohort rule and the fixed-week Insights endpoint (plans 11.6, 14.1).

The suppression rule is the privacy guarantee, so it is tested at its exact
boundary: N-1 is withheld, N is shown (AGENTS.md 16.2). The week-level rules
(complementary suppression, freezing) are in test_insight_suppression.py and
test_insight_weeks.py; this file covers the threshold and the HTTP contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone

import pytest
from app.api.v1.hr import get_clock
from app.main import app
from app.models.insight_count import InsightDailyCount
from app.models.user import UserRole
from app.services import insights_service, settings_service
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

MIN_COHORT = 5
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def cohort_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the suppression threshold and clear any live-config override."""
    monkeypatch.setattr(
        insights_service.settings, "ANALYTICS_MIN_COHORT", MIN_COHORT, raising=False
    )
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)


@pytest.fixture
def frozen_clock() -> Iterator[None]:
    """Freeze the HR clock at NOW for the endpoint tests."""
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    yield
    app.dependency_overrides.pop(get_clock, None)


# ── The rule ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("count", "expected"),
    [(MIN_COHORT - 1, None), (MIN_COHORT, MIN_COHORT), (0, 0)],
    ids=["one-below", "exactly-at", "zero"],
)
def test_the_threshold_boundary(count: int, expected: int | None) -> None:
    # Arrange
    threshold = MIN_COHORT

    # Act
    bucket = insights_service.suppress_small_cohort("work", count, threshold)

    # Assert
    assert bucket.count == expected
    assert bucket.suppressed is (expected is None)


def test_raising_the_threshold_via_live_config_applies_at_once() -> None:
    # Arrange
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "50"

    # Act
    threshold = insights_service.min_cohort()

    # Assert
    assert threshold == 50


def test_a_threshold_below_two_is_refused() -> None:
    # Arrange — a bucket of one *is* a person, so 1 is not an allowed floor
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "1"

    # Act
    threshold = insights_service.min_cohort()

    # Assert
    assert threshold == 2


def test_a_malformed_threshold_falls_back_to_the_configured_default() -> None:
    # Arrange
    settings_service._cache["ANALYTICS_MIN_COHORT"] = "not-a-number"

    # Act
    threshold = insights_service.min_cohort()

    # Assert
    assert threshold == MIN_COHORT


# ── The endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insights_returns_a_months_fixed_weeks_suppressed(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    frozen_clock: None,
) -> None:
    # Arrange — September's first week: 9 forwarded, of which 2 were about grief
    _, headers = await make_staff(UserRole.hr)
    day = date(2026, 9, 2)
    db_session.add_all(
        [
            InsightDailyCount(day=day, dimension="total", label="all", count=9),
            InsightDailyCount(day=day, dimension="status", label="forwarded", count=9),
            InsightDailyCount(day=day, dimension="category", label="grief", count=2),
            InsightDailyCount(day=day, dimension="category", label="work", count=7),
        ]
    )
    await db_session.commit()

    # Act
    response = await api_client.get(
        "/api/v1/hr/insights", params={"month": "2026-09"}, headers=headers
    )

    # Assert — grief is withheld, and work goes with it (else 9 - 7 = 2)
    body = response.json()
    first = body["weeks"][0]
    assert response.status_code == 200
    assert (body["month"], body["min_cohort"]) == ("2026-09", MIN_COHORT)
    assert [w["label"] for w in body["weeks"]] == ["W1", "W2", "W3", "W4"]
    assert first["total"] == {"label": "total", "count": 9, "suppressed": False}
    assert first["by_category"] == [
        {"label": "grief", "count": None, "suppressed": True},
        {"label": "work", "count": None, "suppressed": True},
    ]


@pytest.mark.asyncio
async def test_insights_defaults_to_the_current_month(
    api_client: AsyncClient, make_staff: StaffFactory, frozen_clock: None
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    body = (await api_client.get("/api/v1/hr/insights", headers=headers)).json()

    # Assert — 3 October: no week of October has ended yet
    assert body["month"] == "2026-10"
    assert [w["frozen"] for w in body["weeks"]] == [False, False, False, False]
    assert body["weeks"][0]["total"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("month", ["2026-13", "2026-9", "26-09", "2026-09-01"])
async def test_insights_refuses_anything_but_a_calendar_month(
    api_client: AsyncClient, make_staff: StaffFactory, month: str
) -> None:
    # Arrange — free ranges are gone: only fixed periods can be asked for
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(
        "/api/v1/hr/insights", params={"month": month}, headers=headers
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
