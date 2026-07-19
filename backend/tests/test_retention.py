"""Unit tests for app.services.retention.purge_stale_confessions.

No mocks: this is pure domain logic over a real (in-memory) database
session, per AGENTS.md §16.4. Each test gets a fresh in-memory SQLite
database (AGENTS.md §16.3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.confession import Confession, ConfessionStatus
from app.services.retention import purge_stale_confessions

NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
RETENTION_HOURS = 24


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as s:
        yield s

    await engine.dispose()


def _make_confession(
    status: ConfessionStatus, updated_at: datetime, device_hash: str = "a" * 32
) -> Confession:
    return Confession(
        device_token_hash=device_hash,
        voice_mask="warm",
        transcript="a transcript",
        pii_stripped=True,
        status=status,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_purges_forwarded_confession_past_retention_window(
    session: AsyncSession,
) -> None:
    # Arrange
    stale = _make_confession(ConfessionStatus.forwarded, NOW - timedelta(hours=25))
    session.add(stale)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 1
    remaining = (await session.execute(select(Confession))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_purges_deleted_confession_past_retention_window(
    session: AsyncSession,
) -> None:
    # Arrange
    stale = _make_confession(ConfessionStatus.deleted, NOW - timedelta(hours=25))
    session.add(stale)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 1


@pytest.mark.asyncio
async def test_keeps_forwarded_confession_within_retention_window(
    session: AsyncSession,
) -> None:
    # Arrange — regression: must not purge before the window elapses
    fresh = _make_confession(ConfessionStatus.forwarded, NOW - timedelta(hours=1))
    session.add(fresh)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 0
    remaining = (await session.execute(select(Confession))).scalars().all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_never_purges_pending_confession_regardless_of_age(
    session: AsyncSession,
) -> None:
    # Arrange — regression: pending confessions are still awaiting action,
    # must never be purged no matter how old
    ancient_pending = _make_confession(
        ConfessionStatus.pending, NOW - timedelta(hours=1000)
    )
    session.add(ancient_pending)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 0


@pytest.mark.asyncio
async def test_never_purges_flagged_confession_regardless_of_age(
    session: AsyncSession,
) -> None:
    # Arrange — regression: flagged confessions await moderator review,
    # must never be purged out from under the moderation queue
    ancient_flagged = _make_confession(
        ConfessionStatus.flagged, NOW - timedelta(hours=1000)
    )
    session.add(ancient_flagged)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 0


@pytest.mark.asyncio
async def test_purges_only_stale_rows_among_a_mixed_set(
    session: AsyncSession,
) -> None:
    # Arrange
    session.add_all(
        [
            _make_confession(
                ConfessionStatus.forwarded, NOW - timedelta(hours=48), "a" * 32
            ),
            _make_confession(
                ConfessionStatus.deleted, NOW - timedelta(hours=48), "b" * 32
            ),
            _make_confession(
                ConfessionStatus.forwarded, NOW - timedelta(hours=2), "c" * 32
            ),
            _make_confession(
                ConfessionStatus.pending, NOW - timedelta(hours=48), "d" * 32
            ),
            _make_confession(
                ConfessionStatus.flagged, NOW - timedelta(hours=48), "e" * 32
            ),
        ]
    )
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 2
    remaining_statuses = sorted(
        c.status.value
        for c in (await session.execute(select(Confession))).scalars().all()
    )
    assert remaining_statuses == ["flagged", "forwarded", "pending"]
