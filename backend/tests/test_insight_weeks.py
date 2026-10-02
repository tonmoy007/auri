"""Fixed-period Insights read from the count-only rollups (plan 14.1).

A month is reported as its fixed, month-aligned weeks (days 1-7, 8-14, 15-21 and
22 to the end), so weeks nest exactly in months and two reports can never be
subtracted to isolate a few days. A week is shown only once it is frozen: it has
ended and every confession made in it has been folded, so its figures never change.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from app.models.confession import Confession, ConfessionStatus
from app.models.insight_count import InsightDailyCount
from app.services import insights_service, settings_service
from app.services.insight_weeks import build_month, month_weeks
from sqlalchemy.ext.asyncio import AsyncSession

K = 5
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def cohort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(insights_service.settings, "ANALYTICS_MIN_COHORT", K)
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)


def _counts(day: date, **dimensions: dict[str, int]) -> list[InsightDailyCount]:
    rows = [
        InsightDailyCount(day=day, dimension=dim, label=label, count=n)
        for dim, labels in dimensions.items()
        for label, n in labels.items()
    ]
    total = sum(dimensions.get("status", {}).values())
    rows.append(InsightDailyCount(day=day, dimension="total", label="all", count=total))
    return rows


def _week_of(month, label: str):
    return next(w for w in month.weeks if w.label == label)


def test_a_month_has_four_month_aligned_weeks() -> None:
    # Arrange
    year, month = 2026, 9

    # Act
    weeks = month_weeks(year, month)

    # Assert — week four runs to the month's last day, so weeks nest in months
    assert weeks == [
        ("W1", date(2026, 9, 1), date(2026, 9, 7)),
        ("W2", date(2026, 9, 8), date(2026, 9, 14)),
        ("W3", date(2026, 9, 15), date(2026, 9, 21)),
        ("W4", date(2026, 9, 22), date(2026, 9, 30)),
    ]


@pytest.mark.asyncio
async def test_a_frozen_week_reports_its_counts(db_session: AsyncSession) -> None:
    # Arrange — 12 confessions folded on 2 September
    db_session.add_all(
        _counts(
            date(2026, 9, 2),
            status={"forwarded": 7, "pending": 5},
            category={"workload": 12},
            sentiment={"negative": 6, "neutral": 6},
            department={"HR": 7, "unlabelled": 5},
            delivery={"delivered": 7, "not_delivered": 5},
            delivery_hours={"1-6h": 7},
        )
    )
    await db_session.commit()

    # Act
    report = await build_month(db_session, 2026, 9, NOW)
    week = _week_of(report, "W1")

    # Assert
    assert (report.month, report.min_cohort, week.frozen) == ("2026-09", K, True)
    assert week.total.count == 12
    assert {b.label: b.count for b in week.by_status} == {
        "pending": 5,
        "forwarded": 7,
        "flagged": 0,
    }
    assert {b.label: b.count for b in week.by_category} == {"workload": 12}
    assert [b.label for b in week.by_day] == [f"2026-09-0{d}" for d in range(1, 8)]
    assert {b.label: b.count for b in week.delivery_time}["1-6h"] == 7


@pytest.mark.asyncio
async def test_a_week_with_a_live_confession_is_not_reported_yet(
    db_session: AsyncSession,
) -> None:
    # Arrange — a confession from 9 September is still stored (not yet folded)
    db_session.add_all(_counts(date(2026, 9, 8), status={"forwarded": 9}))
    db_session.add(
        Confession(
            device_token_hash="d" * 32,
            voice_mask="warm",
            transcript="still here",
            pii_stripped=True,
            status=ConfessionStatus.pending,
            created_at=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W2")

    # Assert — no figure at all until every confession of the week is folded
    assert week.frozen is False
    assert week.total is None
    assert week.by_category == []


@pytest.mark.asyncio
async def test_a_week_that_has_not_ended_is_not_reported(
    db_session: AsyncSession,
) -> None:
    # Arrange — 3 October: the first week of October is still running
    db_session.add_all(_counts(date(2026, 10, 1), status={"forwarded": 20}))
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 10, NOW), "W1")

    # Assert
    assert (week.frozen, week.total) == (False, None)


@pytest.mark.asyncio
async def test_a_withdrawn_live_confession_does_not_hold_a_week_back(
    db_session: AsyncSession,
) -> None:
    # Arrange — withdrawn ones are never folded, so they cannot block freezing
    db_session.add_all(_counts(date(2026, 9, 16), status={"forwarded": 6}))
    db_session.add(
        Confession(
            device_token_hash="w" * 32,
            voice_mask="warm",
            transcript="withdrawn",
            pii_stripped=True,
            status=ConfessionStatus.deleted,
            created_at=datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W3")

    # Assert
    assert (week.frozen, week.total.count) == (True, 6)


@pytest.mark.asyncio
async def test_a_lone_small_part_takes_another_with_it(
    db_session: AsyncSession,
) -> None:
    # Arrange — 3 flagged would follow from total 15 minus 7 and 5
    db_session.add_all(
        _counts(date(2026, 9, 23), status={"forwarded": 7, "pending": 5, "flagged": 3})
    )
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W4")

    # Assert
    assert {b.label: b.count for b in week.by_status} == {
        "pending": None,
        "forwarded": 7,
        "flagged": None,
    }


@pytest.mark.asyncio
async def test_a_small_week_hides_its_total_and_every_part(
    db_session: AsyncSession,
) -> None:
    # Arrange
    db_session.add_all(
        _counts(date(2026, 9, 3), status={"forwarded": 3}, category={"workload": 3})
    )
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W1")

    # Assert
    assert week.total.suppressed is True
    assert [b.count for b in week.by_category] == [None]
    assert {b.label: b.count for b in week.by_day}["2026-09-03"] is None


@pytest.mark.asyncio
async def test_delivery_bands_are_hidden_when_the_delivered_count_is(
    db_session: AsyncSession,
) -> None:
    # Arrange — delivered 2 of 9 is hidden, which hides not-delivered too;
    # the bands must not add back up to it
    db_session.add_all(
        _counts(
            date(2026, 9, 10),
            status={"forwarded": 9},
            delivery={"delivered": 2, "not_delivered": 7},
            delivery_hours={"<1h": 2},
        )
    )
    await db_session.commit()

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W2")

    # Assert
    assert {b.label: b.count for b in week.delivery} == {
        "delivered": None,
        "not_delivered": None,
    }
    assert {b.label: b.count for b in week.delivery_time}["<1h"] is None


@pytest.mark.asyncio
async def test_an_empty_frozen_week_reports_zeros(db_session: AsyncSession) -> None:
    # Arrange — nothing at all in the week of 15-21 September

    # Act
    week = _week_of(await build_month(db_session, 2026, 9, NOW), "W3")

    # Assert — "nobody" is reported as zero, not hidden
    assert (week.frozen, week.total.count) == (True, 0)
    assert all(b.count == 0 for b in week.by_day)
