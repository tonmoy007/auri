"""Tests for working the moderation queue from a staff session.

The bot's shared-key path is covered by test_moderation_api.py and must keep
behaving identically; these cases cover the second caller added in 11.8.
"""

from __future__ import annotations

import uuid

import pytest
from app.api.v1 import moderation
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.confession_seeding import add_confession
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
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — 11.8 adds a caller, it does not change the existing one
    set_setting("MODERATION_API_KEY", "bot-secret")
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


# ── Two decisions on one item (11.23) ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stale_second_decision_is_refused_and_changes_nothing(
    api_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    monkeypatch,
) -> None:
    # Arrange — two moderators opened the same item. The first approves it; the
    # second's request then arrives holding a stale "still flagged" view.
    _, first = await make_staff(UserRole.moderator, email="first@example.test")
    _, second = await make_staff(UserRole.moderator, email="second@example.test")
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    confession_id = confession.id
    await api_client.post(f"/api/v1/moderation/{confession_id}/approve", headers=first)
    stale_flagged = Confession(
        id=confession_id,
        device_token_hash="x" * 32,
        voice_mask="warm",
        transcript="t",
        pii_stripped=True,
        status=ConfessionStatus.flagged,
    )

    async def stale_fetch(session: AsyncSession, cid: uuid.UUID) -> Confession:
        return stale_flagged

    monkeypatch.setattr(moderation, "_fetch_flagged_or_404", stale_fetch)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{confession_id}/reject", headers=second
    )

    # Assert — the first decision stands, and the refused one left no audit row
    assert response.status_code == 404
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        status_now = (
            await session.execute(
                select(Confession.status).where(Confession.id == confession_id)
            )
        ).scalar_one()
        decisions = (
            (
                await session.execute(
                    select(AuditEvent.action).where(
                        AuditEvent.action.like("moderation.%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert status_now is ConfessionStatus.pending
    assert decisions == ["moderation.approve"]


@pytest.mark.asyncio
async def test_a_stale_acknowledge_of_a_decided_crisis_item_is_refused(
    api_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    monkeypatch,
) -> None:
    # Arrange — a crisis item is rejected, then a second moderator's acknowledge
    # arrives still holding the "flagged" view it read before the rejection.
    _, first = await make_staff(UserRole.moderator, email="first@example.test")
    _, second = await make_staff(UserRole.moderator, email="second@example.test")
    confession = await add_confession(
        db_session,
        status=ConfessionStatus.flagged,
        severity=ModerationSeverity.crisis.value,
    )
    confession_id = confession.id
    await api_client.post(f"/api/v1/moderation/{confession_id}/reject", headers=first)
    stale_flagged = Confession(
        id=confession_id,
        device_token_hash="x" * 32,
        voice_mask="warm",
        transcript="t",
        pii_stripped=True,
        status=ConfessionStatus.flagged,
        severity=ModerationSeverity.crisis.value,
    )

    async def stale_fetch(session: AsyncSession, cid: uuid.UUID) -> Confession:
        return stale_flagged

    monkeypatch.setattr(moderation, "_fetch_flagged_or_404", stale_fetch)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{confession_id}/acknowledge", headers=second
    )

    # Assert — nothing was stamped, and the refused request left no audit row
    assert response.status_code == 404
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        acknowledged_at = (
            await session.execute(
                select(Confession.acknowledged_at).where(Confession.id == confession_id)
            )
        ).scalar_one()
        acknowledgements = (
            (
                await session.execute(
                    select(AuditEvent.action).where(
                        AuditEvent.action == "crisis.acknowledge"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert acknowledged_at is None
    assert acknowledgements == []
