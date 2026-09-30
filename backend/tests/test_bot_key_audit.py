"""Tests that moderation done through the Telegram bot's shared key is audited.

The key names nobody, so the row says ``telegram-bot`` rather than a person; that
is the most the system can truthfully record. Reads through the bot (its queue
polls) stay unrecorded: they happen every few seconds and would bury the trail.
"""

from __future__ import annotations

import pytest
from app.models.audit_event import AuditAction, AuditEvent
from app.models.confession import ConfessionStatus
from app.models.user import UserRole
from app.services import audit_service
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import add_confession
from tests.conftest import SettingPatcher, StaffFactory

BOT = {"X-Moderation-Api-Key": "bot-secret"}


async def _events(session: AsyncSession) -> list[AuditEvent]:
    session.expire_all()
    return list((await session.execute(select(AuditEvent))).scalars().all())


@pytest.mark.asyncio
async def test_a_bot_rejection_is_recorded_as_the_bot(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("MODERATION_API_KEY", "bot-secret")
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{confession.id}/reject", headers=BOT
    )

    # Assert
    assert response.status_code == 200
    (event,) = await _events(db_session)
    assert (event.action, event.actor_user_id, event.actor_label) == (
        "moderation.reject",
        None,
        "telegram-bot",
    )
    assert event.source_ip is not None


@pytest.mark.asyncio
async def test_a_refused_bot_decision_leaves_no_row(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("MODERATION_API_KEY", "bot-secret")

    # Act
    missing = await api_client.post(
        "/api/v1/moderation/00000000-0000-0000-0000-000000000000/approve", headers=BOT
    )
    wrong_key = await api_client.post(
        "/api/v1/moderation/00000000-0000-0000-0000-000000000000/approve",
        headers={"X-Moderation-Api-Key": "not-the-key"},
    )

    # Assert
    assert (missing.status_code, wrong_key.status_code) == (404, 403)
    assert await _events(db_session) == []


@pytest.mark.asyncio
async def test_polling_the_queue_with_the_bot_key_is_not_recorded(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — the bot polls every few seconds; a row per poll would bury the trail
    set_setting("MODERATION_API_KEY", "bot-secret")
    await add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    response = await api_client.get("/api/v1/moderation/queue", headers=BOT)

    # Assert
    assert response.status_code == 200
    assert await _events(db_session) == []


@pytest.mark.asyncio
async def test_the_audit_api_shows_who_a_row_belongs_to(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("MODERATION_API_KEY", "bot-secret")
    _, admin = await make_staff(UserRole.admin)
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    await api_client.post(f"/api/v1/moderation/{confession.id}/approve", headers=BOT)

    # Act
    items = (await api_client.get("/api/v1/audit", headers=admin)).json()["items"]

    # Assert
    row = next(i for i in items if i["action"] == "moderation.approve")
    assert row["actor_user_id"] is None
    assert row["actor_label"] == "telegram-bot"


@pytest.mark.asyncio
async def test_a_row_must_name_an_actor_or_a_label(db_session: AsyncSession) -> None:
    # Act / Assert — an anonymous audit row would prove nothing
    with pytest.raises(ValueError, match="actor"):
        await audit_service.record(
            db_session, actor=None, action=AuditAction.moderation_approve
        )
