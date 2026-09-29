"""Tests for the HR reply write service (app.services.hr_reply_service).

Real SQLite, no mocked domain logic (AGENTS.md §16.4). The one collaborator
stub simulates a race the single-connection test database cannot produce.
Every write passes an explicit ``now`` (AGENTS.md §16.5).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.exceptions import (
    ConfessionNotFoundError,
    HrReplyInvalidError,
    ReplyNotPermittedError,
)
from app.models.confession import Confession, ConfessionStatus
from app.services import confession_access, hr_reply_service
from app.services.retention import purge_stale_confessions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import add_confession

T1 = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=1)
MAX_LENGTH = 2000


def _naive(moment: datetime | None) -> datetime | None:
    """Drop tzinfo: SQLite hands back naive UTC where Postgres returns aware."""
    return moment.replace(tzinfo=None) if moment is not None else None


async def _reply_columns(
    session: AsyncSession, confession_id: uuid.UUID
) -> tuple[str | None, datetime | None, datetime | None]:
    """Re-read the three reply columns straight from the database."""
    row = (
        await session.execute(
            select(
                Confession.hr_reply,
                Confession.hr_replied_at,
                Confession.hr_reply_edited_at,
            ).where(Confession.id == confession_id)
        )
    ).one()
    return row.hr_reply, _naive(row.hr_replied_at), _naive(row.hr_reply_edited_at)


# ── Writing a reply ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_write_stores_stripped_text_and_stamps_replied_at(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session)

    # Act
    result = await hr_reply_service.write_reply(
        db_session, confession.id, "  we heard you  ", T1
    )

    # Assert
    stored = await _reply_columns(db_session, confession.id)
    assert result.changed is True
    assert result.view.hr_reply == "we heard you"
    assert stored == ("we heard you", _naive(T1), None)


@pytest.mark.asyncio
async def test_resaving_identical_text_is_a_no_op(db_session: AsyncSession) -> None:
    # Arrange
    confession = await add_confession(db_session)
    await hr_reply_service.write_reply(db_session, confession.id, "we heard you", T1)

    # Act
    result = await hr_reply_service.write_reply(
        db_session, confession.id, " we heard you ", T2
    )

    # Assert
    stored = await _reply_columns(db_session, confession.id)
    assert result.changed is False
    assert stored == ("we heard you", _naive(T1), None)


@pytest.mark.asyncio
async def test_saving_different_text_marks_the_reply_edited(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session)
    await hr_reply_service.write_reply(db_session, confession.id, "first words", T1)

    # Act
    result = await hr_reply_service.write_reply(
        db_session, confession.id, "second words", T2
    )

    # Assert
    stored = await _reply_columns(db_session, confession.id)
    assert result.changed is True
    assert result.view.hr_reply == "second words"
    assert stored == ("second words", _naive(T1), _naive(T2))


@pytest.mark.asyncio
async def test_writing_a_reply_preserves_updated_at(db_session: AsyncSession) -> None:
    # Arrange — the retention clock and the bot's dedupe key both read it
    original_updated_at = datetime(2026, 9, 28, 8, 0, 0, tzinfo=timezone.utc)
    confession = await add_confession(db_session, updated_at=original_updated_at)

    # Act
    await hr_reply_service.write_reply(db_session, confession.id, "we heard you", T1)

    # Assert
    updated_at = await db_session.scalar(
        select(Confession.updated_at).where(Confession.id == confession.id)
    )
    assert _naive(updated_at) == _naive(original_updated_at)


@pytest.mark.asyncio
async def test_replied_forwarded_row_is_still_purged_by_retention(
    db_session: AsyncSession,
) -> None:
    # Arrange — a reply must not keep a forwarded transcript alive
    now = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    confession = await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        updated_at=now - timedelta(hours=25),
    )
    await hr_reply_service.write_reply(db_session, confession.id, "we heard you", now)

    # Act
    purged = await purge_stale_confessions(db_session, now, 24)

    # Assert
    assert purged == 1


@pytest.mark.asyncio
async def test_forwarded_confession_accepts_a_reply(db_session: AsyncSession) -> None:
    # Arrange
    confession = await add_confession(db_session, status=ConfessionStatus.forwarded)

    # Act
    result = await hr_reply_service.write_reply(
        db_session, confession.id, "we heard you", T1
    )

    # Assert
    assert result.view.status is ConfessionStatus.forwarded
    assert result.view.hr_reply == "we heard you"


# ── Refusals ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flagged_confession_refuses_a_reply_and_stays_unchanged(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    with pytest.raises(ReplyNotPermittedError) as raised:
        await hr_reply_service.write_reply(
            db_session, confession.id, "we heard you", T1
        )

    # Assert
    assert "'flagged'" in str(raised.value)
    assert await _reply_columns(db_session, confession.id) == (None, None, None)


@pytest.mark.asyncio
async def test_deleted_confession_is_reported_as_not_found(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session, status=ConfessionStatus.deleted)

    # Act / Assert
    with pytest.raises(ConfessionNotFoundError):
        await hr_reply_service.write_reply(
            db_session, confession.id, "we heard you", T1
        )


@pytest.mark.asyncio
async def test_unknown_confession_is_reported_as_not_found(
    db_session: AsyncSession,
) -> None:
    # Arrange
    unknown_id = uuid.uuid4()

    # Act / Assert
    with pytest.raises(ConfessionNotFoundError):
        await hr_reply_service.write_reply(db_session, unknown_id, "we heard you", T1)


@pytest.mark.asyncio
async def test_confession_deleted_after_the_read_is_not_written(
    db_session: AsyncSession, monkeypatch
) -> None:
    # Arrange — the confessor deletes between the eligibility read and the
    # write; a stale read still says "pending", so only the UPDATE's own
    # status guard can stop the reply from landing on a withdrawn row
    confession = await add_confession(db_session, status=ConfessionStatus.deleted)
    stale_view = confession_access.ConfessionSummaryView(
        id=confession.id,
        status=ConfessionStatus.pending,
        category=None,
        sentiment=None,
        ai_summary=None,
        recipient_dept=None,
        created_at=T1,
        delivered_at=None,
        severity="none",
        hr_reply=None,
        hr_replied_at=None,
        hr_reply_edited_at=None,
    )

    async def read_stale_summary(
        session: AsyncSession, confession_id: uuid.UUID
    ) -> confession_access.ConfessionSummaryView:
        return stale_view

    monkeypatch.setattr(confession_access, "read_summary", read_stale_summary)

    # Act
    with pytest.raises(ConfessionNotFoundError):
        await hr_reply_service.write_reply(
            db_session, confession.id, "we heard you", T1
        )

    # Assert
    assert await _reply_columns(db_session, confession.id) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   \n\t  ", "a\x00b"])
async def test_blank_or_nul_text_is_rejected_and_nothing_is_written(
    db_session: AsyncSession, text: str
) -> None:
    # Arrange
    confession = await add_confession(db_session)

    # Act
    with pytest.raises(HrReplyInvalidError):
        await hr_reply_service.write_reply(db_session, confession.id, text, T1)

    # Assert
    assert await _reply_columns(db_session, confession.id) == (None, None, None)


# ── Length limit ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_of_exactly_the_limit_is_accepted_after_stripping(
    db_session: AsyncSession,
) -> None:
    # Arrange — the padding must not count against the limit
    confession = await add_confession(db_session)
    padded = "  " + "a" * MAX_LENGTH + "\n "

    # Act
    result = await hr_reply_service.write_reply(db_session, confession.id, padded, T1)

    # Assert
    assert result.view.hr_reply == "a" * MAX_LENGTH


@pytest.mark.asyncio
async def test_reply_one_over_the_limit_is_rejected_without_echoing_it(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session)
    marker = "leaky-marker-over-limit"
    too_long = marker + "a" * (MAX_LENGTH + 1 - len(marker))

    # Act
    with pytest.raises(HrReplyInvalidError) as raised:
        await hr_reply_service.write_reply(db_session, confession.id, too_long, T1)

    # Assert
    assert str(raised.value) == f"reply must be at most {MAX_LENGTH} characters"
    assert marker not in str(raised.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("   ", "reply must not be blank"),
        ("leaky-marker\x00tail", "reply must not contain NUL characters"),
    ],
)
def test_rejection_messages_are_fixed_and_never_contain_the_text(
    text: str, message: str
) -> None:
    # Arrange / Act
    with pytest.raises(HrReplyInvalidError) as raised:
        hr_reply_service.normalize_reply(text)

    # Assert
    assert str(raised.value) == message


# ── What HR is shown ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returned_view_carries_neither_transcript_nor_device_hash(
    db_session: AsyncSession,
) -> None:
    # Arrange
    confession = await add_confession(db_session)

    # Act
    result = await hr_reply_service.write_reply(
        db_session, confession.id, "we heard you", T1
    )

    # Assert
    assert not hasattr(result.view, "transcript")
    assert not hasattr(result.view, "device_token_hash")


@pytest.mark.asyncio
async def test_summary_view_carries_the_severity(db_session: AsyncSession) -> None:
    # Arrange
    confession = await add_confession(db_session, severity="crisis")

    # Act
    view = await confession_access.read_summary(db_session, confession.id)

    # Assert
    assert view.severity == "crisis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replied", "expected_labels"),
    [
        (True, {"replied_pending", "replied_forwarded"}),
        (False, {"open_pending", "open_flagged"}),
        (
            None,
            {"replied_pending", "replied_forwarded", "open_pending", "open_flagged"},
        ),
    ],
)
async def test_list_summaries_filters_by_reply_state(
    db_session: AsyncSession, replied: bool | None, expected_labels: set[str]
) -> None:
    # Arrange — a deleted row never shows up, replied or not
    seeded = {
        "replied_pending": await add_confession(
            db_session, hr_reply="one", hr_replied_at=T1
        ),
        "replied_forwarded": await add_confession(
            db_session,
            status=ConfessionStatus.forwarded,
            hr_reply="two",
            hr_replied_at=T1,
        ),
        "open_pending": await add_confession(db_session),
        "open_flagged": await add_confession(
            db_session, status=ConfessionStatus.flagged
        ),
    }
    await add_confession(
        db_session,
        status=ConfessionStatus.deleted,
        hr_reply="withdrawn",
        hr_replied_at=T1,
    )
    expected_ids = {seeded[label].id for label in expected_labels}

    # Act
    views, total = await confession_access.list_summaries(db_session, replied=replied)

    # Assert
    assert {view.id for view in views} == expected_ids
    assert total == len(expected_ids)
