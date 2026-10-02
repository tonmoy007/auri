"""Unit tests for app.services.retention.purge_stale_confessions.

No mocks: this is pure domain logic over a real (in-memory) database
session, per AGENTS.md §16.4. Each test gets a fresh in-memory SQLite
database (AGENTS.md §16.3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from app.config import Settings
from app.models.base import Base
from app.models.confession import Confession, ConfessionStatus
from app.models.user import AnonymousUser
from app.services.retention import (
    RetentionResult,
    purge_stale_confessions,
    purge_stale_devices,
    run_retention,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
async def test_purges_pending_confession_past_retention_window(
    session: AsyncSession,
) -> None:
    # Arrange — owner decision 2026-10-01 (plan 14.10): pending confessions follow
    # RETENTION_HOURS like everything else; they used to be kept for ever
    stale_pending = _make_confession(
        ConfessionStatus.pending, NOW - timedelta(hours=RETENTION_HOURS + 1)
    )
    session.add(stale_pending)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 1


@pytest.mark.asyncio
async def test_purges_flagged_confession_past_retention_window(
    session: AsyncSession,
) -> None:
    # Arrange — plan 14.10: a flagged item nobody acted on is not kept for ever;
    # its removal is counted on the run record instead (see the counter tests)
    stale_flagged = _make_confession(
        ConfessionStatus.flagged, NOW - timedelta(hours=RETENTION_HOURS + 1)
    )
    session.add(stale_flagged)
    await session.commit()

    # Act
    deleted_count = await purge_stale_confessions(session, NOW, RETENTION_HOURS)
    await session.commit()

    # Assert
    assert deleted_count == 1


@pytest.mark.asyncio
async def test_a_pending_confession_inside_the_window_is_kept(
    session: AsyncSession,
) -> None:
    # Arrange
    fresh_pending = _make_confession(
        ConfessionStatus.pending, NOW - timedelta(hours=RETENTION_HOURS - 1)
    )
    session.add(fresh_pending)
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

    # Assert — every stale row goes, whatever its status (plan 14.10); only the
    # forwarded one inside the window is left
    assert deleted_count == 4
    remaining_statuses = sorted(
        c.status.value
        for c in (await session.execute(select(Confession))).scalars().all()
    )
    assert remaining_statuses == ["forwarded"]


# ── Replies outlive the confession (11.16) ───────────────────────────────

REPLY_RETENTION_DAYS = 30
REPLIED_AT = NOW - timedelta(days=1)


def _replied(
    updated_at: datetime,
    status: ConfessionStatus = ConfessionStatus.forwarded,
    replied_at: datetime = REPLIED_AT,
    edited_at: datetime | None = None,
) -> Confession:
    confession = _make_confession(status, updated_at, device_hash="d" * 32)
    confession.ai_summary = "summary"
    confession.category = "work"
    confession.sentiment = "negative"
    confession.recipient_dept = "HR"
    confession.counselor_response = "you are heard"
    confession.counselor_reply = {"acknowledgement": "you are heard"}
    confession.severity = "crisis"
    confession.reviewed_by = uuid.uuid4()
    confession.reviewed_at = updated_at
    confession.acknowledged_by = uuid.uuid4()
    confession.acknowledged_at = updated_at
    confession.delivered_at = updated_at
    confession.hr_reply = "we heard you"
    confession.hr_replied_at = replied_at
    confession.hr_reply_edited_at = edited_at
    return confession


async def _only_row(session: AsyncSession) -> Confession:
    await session.commit()
    return (await session.execute(select(Confession))).scalars().one()


@pytest.mark.asyncio
async def test_a_stale_replied_confession_is_emptied_not_deleted(
    session: AsyncSession,
) -> None:
    # Arrange
    session.add(_replied(NOW - timedelta(hours=25)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert result == RetentionResult(deleted=0, emptied_to_shell=1, expired_replies=0)
    assert row.purged_at is not None


@pytest.mark.asyncio
async def test_emptying_clears_everything_that_describes_the_confession(
    session: AsyncSession,
) -> None:
    # Arrange
    session.add(_replied(NOW - timedelta(hours=25)))
    await session.commit()

    # Act
    await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert row.transcript == ""
    assert (row.ai_summary, row.category, row.sentiment) == (None, None, None)
    assert (row.recipient_dept, row.counselor_response) == (None, None)
    assert row.counselor_reply is None
    assert (row.severity, row.reviewed_by, row.acknowledged_by) == ("none", None, None)
    assert (row.reviewed_at, row.acknowledged_at) == (None, None)


@pytest.mark.asyncio
async def test_emptying_keeps_what_the_confessor_needs_to_read_the_reply(
    session: AsyncSession,
) -> None:
    # Arrange
    edited = NOW - timedelta(hours=3)
    session.add(_replied(NOW - timedelta(hours=25), edited_at=edited))
    await session.commit()

    # Act
    await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert row.device_token_hash == "d" * 32
    assert row.hr_reply == "we heard you"
    assert row.status is ConfessionStatus.forwarded
    assert row.hr_reply_edited_at is not None


@pytest.mark.asyncio
async def test_a_replied_confession_inside_the_window_is_left_whole(
    session: AsyncSession,
) -> None:
    # Arrange
    session.add(_replied(NOW - timedelta(hours=23)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert result == RetentionResult(0, 0, 0)
    assert (row.purged_at, row.transcript) == (None, "a transcript")


@pytest.mark.asyncio
async def test_a_withdrawn_replied_confession_is_deleted_with_its_reply(
    session: AsyncSession,
) -> None:
    # Arrange — the confessor withdrew it, so there is nobody to show the reply to
    session.add(_replied(NOW - timedelta(hours=25), status=ConfessionStatus.deleted))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    remaining = (await session.execute(select(Confession))).scalars().all()

    # Assert
    assert result.deleted == 1
    assert remaining == []


@pytest.mark.asyncio
async def test_a_stale_replied_pending_confession_is_emptied_to_a_shell(
    session: AsyncSession,
) -> None:
    # Arrange — plan 14.10: like a forwarded one, its reply is kept for the device
    session.add(
        _replied(
            NOW - timedelta(hours=RETENTION_HOURS + 1), status=ConfessionStatus.pending
        )
    )
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert result.emptied_to_shell == 1
    assert (row.transcript, row.hr_reply) == ("", "we heard you")
    assert row.purged_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_days", "survives"),
    [(29, True), (30, True), (31, False)],
    ids=["29d", "exactly-30d", "31d"],
)
async def test_a_shell_is_deleted_only_once_the_reply_is_older_than_the_retention(
    age_days: int, survives: bool, session: AsyncSession
) -> None:
    # Arrange — a genuine shell: emptied at the moment its 24h came due
    replied_at = NOW - timedelta(days=age_days)
    session.add(_replied(replied_at - timedelta(hours=25), replied_at=replied_at))
    await session.commit()
    await run_retention(session, replied_at + timedelta(hours=1), 24, 30)
    assert (await _only_row(session)).purged_at is not None

    # Act
    await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    remaining = (await session.execute(select(Confession))).scalars().all()

    # Assert
    assert (len(remaining) == 1) is survives


@pytest.mark.asyncio
async def test_editing_a_reply_does_not_extend_how_long_it_is_kept(
    session: AsyncSession,
) -> None:
    # Arrange — first written 31 days ago, edited yesterday
    session.add(
        _replied(
            NOW - timedelta(days=31),
            replied_at=NOW - timedelta(days=31),
            edited_at=NOW - timedelta(days=1),
        )
    )
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert result.expired_replies == 1


@pytest.mark.asyncio
async def test_a_row_due_for_both_is_expired_not_emptied_first(
    session: AsyncSession,
) -> None:
    # Arrange — stale AND its reply is past retention
    session.add(_replied(NOW - timedelta(days=40), replied_at=NOW - timedelta(days=35)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert result == RetentionResult(deleted=0, emptied_to_shell=0, expired_replies=1)


@pytest.mark.asyncio
async def test_a_second_run_empties_nothing_more(session: AsyncSession) -> None:
    # Arrange
    session.add(_replied(NOW - timedelta(hours=25)))
    await session.commit()
    await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Act
    second = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert second == RetentionResult(0, 0, 0)


@pytest.mark.asyncio
async def test_a_row_already_emptied_is_not_emptied_again(
    session: AsyncSession,
) -> None:
    # Arrange — a shell whose updated_at is stale, as it would be after the
    # ORM stamped it; only purged_at says it was already done
    first_emptied = NOW - timedelta(hours=2)
    shell = _replied(NOW - timedelta(hours=40))
    shell.purged_at = first_emptied
    session.add(shell)
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert result.emptied_to_shell == 0
    assert row.purged_at is not None
    assert row.purged_at.replace(tzinfo=None) == first_emptied.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_a_live_confession_is_not_expired_just_because_its_reply_is_old(
    session: AsyncSession,
) -> None:
    # Arrange — HR replied to a pending item 31 days ago; the confessor forwarded
    # it an hour ago. It is live and undelivered, so it must survive.
    session.add(_replied(NOW - timedelta(hours=1), replied_at=NOW - timedelta(days=31)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    row = await _only_row(session)

    # Assert
    assert result == RetentionResult(0, 0, 0)
    assert row.transcript == "a transcript"


@pytest.mark.asyncio
async def test_that_live_confession_becomes_a_shell_and_is_then_expired(
    session: AsyncSession,
) -> None:
    # Arrange — same row, once ordinary retention comes due for it
    session.add(_replied(NOW - timedelta(hours=1), replied_at=NOW - timedelta(days=31)))
    await session.commit()
    later = NOW + timedelta(hours=25)

    # Act
    first = await run_retention(session, later, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    second = await run_retention(
        session, later + timedelta(minutes=1), RETENTION_HOURS, REPLY_RETENTION_DAYS
    )

    # Assert — the first run finds it stale AND its reply old: expired outright
    assert first == RetentionResult(deleted=0, emptied_to_shell=0, expired_replies=1)
    assert second == RetentionResult(0, 0, 0)


@pytest.mark.asyncio
async def test_a_confession_exactly_at_the_retention_age_is_not_yet_emptied(
    session: AsyncSession,
) -> None:
    # Arrange — strictly older than 24h is due; exactly 24h is not
    session.add(_replied(NOW - timedelta(hours=RETENTION_HOURS)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert result.emptied_to_shell == 0


def test_reply_retention_defaults_to_thirty_days() -> None:
    # Act
    configured = Settings(_env_file=None)

    # Assert
    assert configured.REPLY_RETENTION_DAYS == 30


@pytest.mark.parametrize("days", [0, -5])
def test_a_reply_retention_of_zero_or_less_is_refused(days: int) -> None:
    # Act / Assert — zero would delete every reply the moment it is emptied
    with pytest.raises(ValidationError):
        Settings(_env_file=None, REPLY_RETENTION_DAYS=days)


def test_a_reply_retention_shorter_than_the_confession_retention_is_refused() -> None:
    # Act / Assert — the reply would be deleted before the row is ever emptied
    with pytest.raises(ValidationError):
        Settings(_env_file=None, RETENTION_HOURS=100, REPLY_RETENTION_DAYS=2)


def test_a_reply_retention_exactly_covering_the_confession_retention_is_accepted() -> (
    None
):
    # Act
    configured = Settings(_env_file=None, RETENTION_HOURS=48, REPLY_RETENTION_DAYS=2)

    # Assert
    assert configured.REPLY_RETENTION_DAYS == 2


def test_zero_reply_retention_is_refused_even_when_confession_retention_is_zero() -> (
    None
):
    # Arrange — 0 days covers 0 hours, so only the field's own lower bound stops it

    # Act / Assert
    with pytest.raises(ValidationError):
        Settings(_env_file=None, RETENTION_HOURS=0, REPLY_RETENTION_DAYS=0)


RATE_LIMIT_SECONDS = 300


def _device(device_hash: str, last_confession_at: datetime) -> AnonymousUser:
    return AnonymousUser(
        device_token_hash=device_hash,
        last_confession_at=last_confession_at,
        confession_count=3,
    )


async def _device_hashes(session: AsyncSession) -> list[str]:
    rows = await session.execute(select(AnonymousUser.device_token_hash))
    return sorted(rows.scalars().all())


@pytest.mark.asyncio
async def test_device_records_past_the_rate_limit_window_are_deleted(
    session: AsyncSession,
) -> None:
    # Arrange — the record exists only to rate-limit, so once the window has
    # passed it does nothing except link a device to when it last spoke
    window = timedelta(seconds=RATE_LIMIT_SECONDS)
    session.add_all(
        [
            _device("expired", NOW - window - timedelta(seconds=1)),
            _device("edge", NOW - window),
            _device("active", NOW - window + timedelta(seconds=1)),
        ]
    )
    await session.commit()

    # Act
    removed = await purge_stale_devices(session, NOW, RATE_LIMIT_SECONDS)
    await session.commit()

    # Assert — only the one strictly past the window goes
    assert removed == 1
    assert await _device_hashes(session) == ["active", "edge"]


@pytest.mark.asyncio
async def test_a_device_still_inside_its_window_is_still_rate_limited_after_a_run(
    session: AsyncSession,
) -> None:
    # Arrange — purging must never forget a device that is still being limited
    session.add(_device("limited", NOW - timedelta(seconds=10)))
    await session.commit()

    # Act
    await run_retention(
        session, NOW, RETENTION_HOURS, 30, device_window_seconds=RATE_LIMIT_SECONDS
    )
    await session.commit()

    # Assert
    assert await _device_hashes(session) == ["limited"]


@pytest.mark.asyncio
async def test_a_run_counts_the_device_records_it_removed(
    session: AsyncSession,
) -> None:
    # Arrange
    old = NOW - timedelta(days=2)
    session.add_all([_device("one", old), _device("two", old)])
    await session.commit()

    # Act
    result = await run_retention(
        session, NOW, RETENTION_HOURS, 30, device_window_seconds=RATE_LIMIT_SECONDS
    )
    await session.commit()

    # Assert
    assert result.expired_devices == 2
    assert await _device_hashes(session) == []


@pytest.mark.asyncio
async def test_device_records_are_left_alone_unless_a_window_is_given(
    session: AsyncSession,
) -> None:
    # Arrange — a caller that does not know the rate limit must not guess one
    session.add(_device("old", NOW - timedelta(days=2)))
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, 30)
    await session.commit()

    # Assert
    assert result.expired_devices == 0
    assert await _device_hashes(session) == ["old"]


# -- removal counters on the run record (plan 14.10: "the purge is audited") ---


@pytest.mark.asyncio
async def test_a_run_counts_the_flagged_items_it_removed(session: AsyncSession) -> None:
    # Arrange — two stale flagged items, one stale forwarded, one fresh flagged
    stale = NOW - timedelta(hours=RETENTION_HOURS + 1)
    session.add_all(
        [
            _make_confession(ConfessionStatus.flagged, stale, device_hash="f1" * 16),
            _make_confession(ConfessionStatus.flagged, stale, device_hash="f2" * 16),
            _make_confession(ConfessionStatus.forwarded, stale, device_hash="w1" * 16),
            _make_confession(ConfessionStatus.flagged, NOW, device_hash="f3" * 16),
        ]
    )
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert result.flagged_removed == 2
    assert result.deleted == 3


@pytest.mark.asyncio
async def test_a_run_counts_crisis_items_removed_before_anyone_acknowledged_them(
    session: AsyncSession,
) -> None:
    # Arrange — a crisis nobody acknowledged, one that was acknowledged, both stale
    stale = NOW - timedelta(hours=RETENTION_HOURS + 1)
    unseen = _make_confession(ConfessionStatus.flagged, stale, device_hash="c1" * 16)
    unseen.severity = "crisis"
    seen = _make_confession(ConfessionStatus.flagged, stale, device_hash="c2" * 16)
    seen.severity = "crisis"
    seen.acknowledged_at = stale
    seen.acknowledged_by = uuid.uuid4()
    session.add_all([unseen, seen])
    await session.commit()

    # Act
    result = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)

    # Assert
    assert result.unacknowledged_crisis_removed == 1
    assert result.flagged_removed == 2


@pytest.mark.asyncio
async def test_emptying_then_expiring_a_flagged_shell_counts_it_once(
    session: AsyncSession,
) -> None:
    # Arrange — a stale replied flagged item: emptied now, expired a month later
    session.add(
        _replied(
            NOW - timedelta(hours=RETENTION_HOURS + 1), status=ConfessionStatus.flagged
        )
    )
    await session.commit()
    month_later = NOW + timedelta(days=REPLY_RETENTION_DAYS + 2)

    # Act
    first = await run_retention(session, NOW, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    second = await run_retention(
        session, month_later, RETENTION_HOURS, REPLY_RETENTION_DAYS
    )

    # Assert — the content left once, so it is counted once
    assert (first.emptied_to_shell, first.flagged_removed) == (1, 1)
    assert (second.expired_replies, second.flagged_removed) == (1, 0)


@pytest.mark.asyncio
async def test_the_run_record_keeps_the_removal_counters(session: AsyncSession) -> None:
    # Arrange
    from app.models.retention_run import RetentionRun
    from app.services.retention_status import record_run

    result = RetentionResult(
        3, 1, 0, flagged_removed=2, unacknowledged_crisis_removed=1
    )

    # Act
    await record_run(session, NOW, result, RETENTION_HOURS, REPLY_RETENTION_DAYS)
    await session.commit()
    stored = (await session.execute(select(RetentionRun))).scalars().one()

    # Assert
    assert (stored.flagged_removed, stored.unacknowledged_crisis_removed) == (2, 1)
