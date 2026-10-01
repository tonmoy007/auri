"""Tests for recording a device's submission while the retention job may purge it.

``create_confession`` reads the device's ``anonymous_users`` row first, then spends
several seconds in model calls, and only then writes the row back. The retention
job deletes exactly the rows a returning confessor has (their last confession is
older than the rate-limit window), so the row can disappear in between. The write
must still succeed: losing a confession because a clean-up job ran would be worse
than the record it was updating.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.api.v1 import confessions
from app.models.user import AnonymousUser
from app.services.retention import purge_stale_devices
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

NOW = datetime(2026, 7, 20, 12, 0, 0)  # noqa: DTZ001 - naive, like the SQLite tests
WINDOW_SECONDS = 300
DEVICE = "d" * 32


async def _rows(engine: AsyncEngine) -> list[tuple[str, int, datetime]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(select(AnonymousUser))).scalars().all()
        return [
            (r.device_token_hash, r.confession_count, r.last_confession_at)
            for r in rows
        ]


async def _record(session: AsyncSession) -> None:
    await confessions._record_submission(session, DEVICE, NOW)
    await session.commit()


@pytest.mark.asyncio
async def test_a_submission_is_recorded_when_the_row_was_purged_after_it_was_read(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Arrange — the route has read the row; then the retention job removes it
    old = NOW - timedelta(seconds=WINDOW_SECONDS + 60)
    db_session.add(
        AnonymousUser(
            device_token_hash=DEVICE, last_confession_at=old, confession_count=4
        )
    )
    await db_session.commit()
    # the route's own read, still held in the session while the job runs
    (await db_session.execute(select(AnonymousUser))).scalar_one()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as job_session:
        assert await purge_stale_devices(job_session, NOW, WINDOW_SECONDS) == 1
        await job_session.commit()

    # Act
    await _record(db_session)

    # Assert — the confession is not lost, and the device starts a fresh record
    assert await _rows(db_engine) == [(DEVICE, 1, NOW)]


@pytest.mark.asyncio
async def test_a_submission_updates_the_existing_row_in_place(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Arrange
    old = NOW - timedelta(seconds=30)
    db_session.add(
        AnonymousUser(
            device_token_hash=DEVICE, last_confession_at=old, confession_count=4
        )
    )
    await db_session.commit()
    (await db_session.execute(select(AnonymousUser))).scalar_one()

    # Act
    await _record(db_session)

    # Assert
    assert await _rows(db_engine) == [(DEVICE, 5, NOW)]


@pytest.mark.asyncio
async def test_a_first_submission_creates_the_row(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Act
    await _record(db_session)

    # Assert
    assert await _rows(db_engine) == [(DEVICE, 1, NOW)]
