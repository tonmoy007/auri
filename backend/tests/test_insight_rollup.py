"""Count-only rollups written as confessions leave at retention (plan 14.12).

Insights reports frozen weekly counts, so the numbers have to outlive the rows:
the retention job folds each leaving confession's metadata into per-day counts
just before it deletes or empties the row. No text, no ids, no device hash.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from app.models.base import Base
from app.models.confession import Confession, ConfessionStatus
from app.models.insight_count import InsightDailyCount
from app.services.retention import run_retention
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=30)
HOURS, REPLY_DAYS = 24, 30
DAY = STALE.date()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _confession(
    status: ConfessionStatus,
    *,
    updated_at: datetime = STALE,
    category: str | None = "workload",
    sentiment: str | None = "negative",
    dept: str | None = "HR",
    delivered_after: timedelta | None = None,
    reply: str | None = None,
) -> Confession:
    return Confession(
        device_token_hash="d" * 32,
        voice_mask="warm",
        transcript="words",
        pii_stripped=True,
        status=status,
        category=category,
        sentiment=sentiment,
        recipient_dept=dept,
        created_at=updated_at,
        updated_at=updated_at,
        delivered_at=updated_at + delivered_after if delivered_after else None,
        hr_reply=reply,
        hr_replied_at=updated_at if reply else None,
    )


async def _counts(session: AsyncSession) -> dict[tuple[date, str, str], int]:
    await session.commit()
    rows = (await session.execute(select(InsightDailyCount))).scalars().all()
    return {(r.day, r.dimension, r.label): r.count for r in rows}


@pytest.mark.asyncio
async def test_a_leaving_confession_is_folded_into_counts_per_dimension(
    session: AsyncSession,
) -> None:
    # Arrange
    session.add(
        _confession(ConfessionStatus.forwarded, delivered_after=timedelta(hours=2))
    )
    await session.commit()

    # Act
    await run_retention(session, NOW, HOURS, REPLY_DAYS)
    counts = await _counts(session)

    # Assert — one row per dimension, the day the confession was made
    assert counts == {
        (DAY, "total", "all"): 1,
        (DAY, "category", "workload"): 1,
        (DAY, "sentiment", "negative"): 1,
        (DAY, "department", "HR"): 1,
        (DAY, "status", "forwarded"): 1,
        (DAY, "delivery", "delivered"): 1,
        (DAY, "delivery_hours", "1-6h"): 1,
    }


@pytest.mark.asyncio
async def test_missing_labels_are_counted_as_unlabelled(session: AsyncSession) -> None:
    # Arrange
    session.add(
        _confession(ConfessionStatus.pending, category=None, sentiment=None, dept=None)
    )
    await session.commit()

    # Act
    await run_retention(session, NOW, HOURS, REPLY_DAYS)
    counts = await _counts(session)

    # Assert
    assert counts[(DAY, "category", "unlabelled")] == 1
    assert counts[(DAY, "sentiment", "unlabelled")] == 1
    assert counts[(DAY, "department", "unlabelled")] == 1
    assert counts[(DAY, "delivery", "not_delivered")] == 1


@pytest.mark.asyncio
async def test_withdrawn_and_still_live_confessions_are_not_folded(
    session: AsyncSession,
) -> None:
    # Arrange — Insights never counted withdrawn ones; a fresh one is not leaving
    session.add_all(
        [
            _confession(ConfessionStatus.deleted),
            _confession(ConfessionStatus.forwarded, updated_at=NOW),
        ]
    )
    await session.commit()

    # Act
    await run_retention(session, NOW, HOURS, REPLY_DAYS)
    counts = await _counts(session)

    # Assert
    assert counts == {}


@pytest.mark.asyncio
async def test_a_second_run_adds_to_the_same_day_without_double_counting(
    session: AsyncSession,
) -> None:
    # Arrange — one leaves now, a second (same day) only becomes due later
    session.add(_confession(ConfessionStatus.flagged))
    session.add(
        _confession(ConfessionStatus.flagged, updated_at=STALE + timedelta(hours=8))
    )
    await session.commit()
    await run_retention(session, NOW, HOURS, REPLY_DAYS)

    # Act
    await run_retention(session, NOW + timedelta(hours=10), HOURS, REPLY_DAYS)
    await run_retention(session, NOW + timedelta(hours=11), HOURS, REPLY_DAYS)
    counts = await _counts(session)

    # Assert
    assert counts[(DAY, "total", "all")] == 2
    assert counts[(DAY, "status", "flagged")] == 2


@pytest.mark.asyncio
async def test_a_replied_confession_is_folded_once_across_emptying_and_expiry(
    session: AsyncSession,
) -> None:
    # Arrange — emptied to a shell now (content leaves), the shell expires later
    session.add(_confession(ConfessionStatus.forwarded, reply="we heard you"))
    await session.commit()
    await run_retention(session, NOW, HOURS, REPLY_DAYS)

    # Act
    await run_retention(
        session, NOW + timedelta(days=REPLY_DAYS + 2), HOURS, REPLY_DAYS
    )
    counts = await _counts(session)

    # Assert
    assert counts[(DAY, "total", "all")] == 1


@pytest.mark.parametrize(
    ("after", "bucket"),
    [
        (timedelta(minutes=30), "<1h"),
        (timedelta(hours=1), "1-6h"),
        (timedelta(hours=6), "6-24h"),
        (timedelta(hours=24), "24-72h"),
        (timedelta(hours=72), ">72h"),
    ],
)
def test_delivery_time_buckets(after: timedelta, bucket: str) -> None:
    # Arrange
    from app.services.insight_rollup import delivery_bucket

    # Act
    label = delivery_bucket(after)

    # Assert
    assert label == bucket
