"""Tests that department changes leave an audit row saying *what* changed.

Re-pointing a department's Telegram chat redirects every future forwarded
summary (and the first 1,000 transcript characters) to a new place. A trail that
records only "a department was written" cannot answer who sent them where.
"""

from __future__ import annotations

import unicodedata

import pytest
from app.models.audit_event import AuditAction, AuditEvent
from app.models.user import UserRole
from app.services import audit_service, department_service
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

DIRECTORY = "/api/v1/departments/directory"


async def _details(session: AsyncSession) -> list[str | None]:
    session.expire_all()
    rows = (
        (await session.execute(select(AuditEvent).order_by(AuditEvent.created_at)))
        .scalars()
        .all()
    )
    return [row.detail for row in rows]


@pytest.mark.asyncio
async def test_creating_a_department_records_its_name_and_chat(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    await api_client.post(
        DIRECTORY,
        json={"name": "Facilities", "telegram_chat_id": "-100999"},
        headers=headers,
    )

    # Assert — a new department can route summaries anywhere, so the chat is named
    assert await _details(db_session) == ["created department Facilities: chat -100999"]


@pytest.mark.asyncio
async def test_creating_a_department_without_a_chat_says_so(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    await api_client.post(DIRECTORY, json={"name": "Facilities"}, headers=headers)

    # Assert
    assert await _details(db_session) == ["created department Facilities: chat none"]


@pytest.mark.asyncio
async def test_repointing_a_chat_records_the_old_and_new_ids(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await department_service.create_department(db_session, "Facilities", "-100111")
    await db_session.commit()

    # Act
    await api_client.put(
        f"{DIRECTORY}/Facilities",
        json={"telegram_chat_id": "-100222", "is_active": True},
        headers=headers,
    )

    # Assert
    detail = (await _details(db_session))[0]
    assert detail is not None
    assert "Facilities" in detail
    assert "-100111" in detail
    assert "-100222" in detail


@pytest.mark.asyncio
async def test_deactivating_a_department_is_recorded(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await department_service.create_department(db_session, "Facilities", "-100111")
    await db_session.commit()

    # Act
    await api_client.put(
        f"{DIRECTORY}/Facilities",
        json={"telegram_chat_id": "-100111", "is_active": False},
        headers=headers,
    )

    # Assert
    detail = (await _details(db_session))[0]
    assert detail is not None
    assert "active True -> False" in detail


@pytest.mark.asyncio
async def test_removing_a_chat_is_recorded_as_none(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await department_service.create_department(db_session, "Facilities", "-100111")
    await db_session.commit()

    # Act
    await api_client.put(
        f"{DIRECTORY}/Facilities",
        json={"telegram_chat_id": None, "is_active": True},
        headers=headers,
    )

    # Assert
    detail = (await _details(db_session))[0]
    assert detail is not None
    assert "-100111 -> none" in detail


@pytest.mark.asyncio
async def test_deleting_a_department_records_the_chat_it_held(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await department_service.create_department(db_session, "Facilities", "-100111")
    await db_session.commit()

    # Act
    await api_client.delete(f"{DIRECTORY}/Facilities", headers=headers)

    # Assert
    assert await _details(db_session) == [
        "deleted department Facilities: chat -100111, active True"
    ]


@pytest.mark.asyncio
async def test_a_refused_delete_or_update_leaves_no_audit_row(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    deleted = await api_client.delete(f"{DIRECTORY}/Nowhere", headers=headers)
    updated = await api_client.put(
        f"{DIRECTORY}/Nowhere",
        json={"telegram_chat_id": "-1", "is_active": True},
        headers=headers,
    )

    # Assert
    assert (deleted.status_code, updated.status_code) == (404, 404)
    assert await _details(db_session) == []


@pytest.mark.asyncio
async def test_a_department_name_cannot_smuggle_control_characters_into_the_note(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — a right-to-left override or a line break in the name could make
    # the ids that follow it render reversed, or on a separate line, in the table
    _, headers = await make_staff(UserRole.admin)

    # Act
    await api_client.post(
        DIRECTORY,
        json={"name": "Fac\u202eili\nties", "telegram_chat_id": "-100999"},
        headers=headers,
    )

    # Assert
    detail = (await _details(db_session))[0]
    assert detail is not None
    assert not any(unicodedata.category(char).startswith("C") for char in detail)
    assert detail.endswith(": chat -100999")


@pytest.mark.asyncio
async def test_the_audit_api_returns_the_detail(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await api_client.post(DIRECTORY, json={"name": "Facilities"}, headers=headers)

    # Act
    page = (await api_client.get("/api/v1/audit", headers=headers)).json()

    # Assert
    assert page["items"][0]["detail"] == "created department Facilities: chat none"


@pytest.mark.asyncio
async def test_a_content_read_carries_no_detail(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — detail is for changes to settings, never a place to put content
    _, headers = await make_staff(UserRole.hr)

    # Act
    await api_client.get("/api/v1/hr/insights", headers=headers)

    # Assert
    assert await _details(db_session) == [None]


@pytest.mark.asyncio
async def test_an_over_long_detail_is_capped(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, _ = await make_staff(UserRole.admin)

    # Act
    await audit_service.record(
        db_session,
        actor=actor,
        action=AuditAction.department_write,
        detail="x" * (audit_service.MAX_DETAIL_CHARS + 100),
    )

    # Assert
    detail = (await _details(db_session))[0]
    assert detail is not None
    assert len(detail) == audit_service.MAX_DETAIL_CHARS
