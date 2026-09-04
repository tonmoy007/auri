"""Tests for working the moderation queue from a staff session.

The bot's shared-key path is covered by test_moderation_api.py and must keep
behaving identically; these cases cover the second caller added in 11.8.
"""

from __future__ import annotations

import pytest
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

QUEUE_PATH = "/api/v1/moderation/queue"


async def _add_flagged(session: AsyncSession) -> Confession:
    confession = Confession(
        device_token_hash="a" * 32,
        voice_mask="warm",
        transcript="something that tripped the safety check",
        ai_summary="a summary",
        category="work",
        sentiment="negative",
        pii_stripped=True,
        status=ConfessionStatus.flagged,
    )
    session.add(confession)
    await session.commit()
    return confession


@pytest.mark.asyncio
async def test_moderator_session_can_list_the_queue(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")
    await _add_flagged(db_session)

    # Act
    response = await api_client.get(QUEUE_PATH, headers=headers)

    # Assert
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_listing_the_queue_from_a_session_is_audited_as_a_raw_read(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the queue serves full transcripts, so listing it is a raw read
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")
    await _add_flagged(db_session)

    # Act
    await api_client.get(QUEUE_PATH, headers=headers)

    # Assert
    event = (await db_session.execute(select(AuditEvent))).scalars().one()
    assert event.content_tier == "raw"
    assert event.action == "confession.list"


@pytest.mark.asyncio
async def test_approving_from_a_session_records_the_reviewer(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.moderator, "mod@example.com")
    confession = await _add_flagged(db_session)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{confession.id}/approve", headers=headers
    )

    # Assert
    await db_session.refresh(confession)
    assert response.status_code == 200
    assert confession.status == ConfessionStatus.pending
    assert confession.reviewed_by == actor.id
    assert confession.reviewed_at is not None


@pytest.mark.asyncio
async def test_rejecting_from_a_session_records_the_reviewer_and_audits_it(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.moderator, "mod@example.com")
    confession = await _add_flagged(db_session)

    # Act
    await api_client.post(f"/api/v1/moderation/{confession.id}/reject", headers=headers)

    # Assert
    await db_session.refresh(confession)
    event = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "moderation.reject")
            )
        )
        .scalars()
        .one()
    )
    assert confession.status == ConfessionStatus.deleted
    assert confession.reviewed_by == actor.id
    assert event.target_confession_id == confession.id


@pytest.mark.asyncio
async def test_bot_path_still_works_and_stays_anonymous(
    api_client: AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    # Arrange — 11.8 adds a caller, it does not change the existing one
    from app.config import settings

    monkeypatch.setattr(settings, "MODERATION_API_KEY", "bot-secret")
    confession = await _add_flagged(db_session)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{confession.id}/approve",
        headers={"X-Moderation-Api-Key": "bot-secret"},
    )

    # Assert — no reviewer stamped, no audit row: the Telegram path has no
    # named actor, and inventing one would be a lie in the audit trail
    await db_session.refresh(confession)
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert response.status_code == 200
    assert confession.status == ConfessionStatus.pending
    assert confession.reviewed_by is None
    assert events == []


@pytest.mark.asyncio
async def test_a_deactivated_reviewer_loses_queue_access(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — revoking an account has to cut off the queue too, not just
    # the panels that use the role dependency
    user, headers = await make_staff(UserRole.moderator, "mod@example.com")
    user.is_active = False
    await db_session.commit()

    # Act
    response = await api_client.get(QUEUE_PATH, headers=headers)

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_an_hr_session_can_also_work_the_queue(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — flagged items are HR's escalations, so HR shares the queue
    _, headers = await make_staff(UserRole.hr, "hr@example.com")
    await _add_flagged(db_session)

    # Act
    response = await api_client.get(QUEUE_PATH, headers=headers)

    # Assert
    assert response.status_code == 200
    assert len(response.json()) == 1
