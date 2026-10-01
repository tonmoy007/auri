"""Tests for the database-backed department directory.

Covers the two things that used to be able to disagree — the backend's list
and the bot's routing map — plus the rule that a department owing
deliveries cannot be deleted out from under them.
"""

from __future__ import annotations

import pytest
from app.models.confession import Confession, ConfessionStatus
from app.models.department import Department
from app.models.user import UserRole
from app.services import department_service
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

DIRECTORY_PATH = "/api/v1/departments/directory"


async def _add_department(
    session: AsyncSession,
    name: str = "Engineering",
    chat_id: str | None = "111",
    is_active: bool = True,
) -> Department:
    department = Department(name=name, telegram_chat_id=chat_id, is_active=is_active)
    session.add(department)
    await session.commit()
    return department


async def _add_undelivered(session: AsyncSession, department: str) -> Confession:
    confession = Confession(
        device_token_hash="a" * 32,
        voice_mask="warm",
        transcript="words",
        pii_stripped=True,
        status=ConfessionStatus.forwarded,
        recipient_dept=department,
    )
    session.add(confession)
    await session.commit()
    return confession


# ── Seeding ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seeding_populates_an_empty_directory_from_env(
    db_session: AsyncSession, set_setting
) -> None:
    # Arrange
    set_setting("DEPARTMENTS", "HR,Engineering")

    # Act
    created = await department_service.seed_from_env_if_empty(db_session)

    # Assert
    names = sorted(
        d.name for d in (await db_session.execute(select(Department))).scalars()
    )
    assert created == 2
    assert names == ["Engineering", "HR"]


@pytest.mark.asyncio
async def test_seeding_never_overwrites_an_existing_directory(
    db_session: AsyncSession, set_setting
) -> None:
    # Arrange — a stale deploy variable must not resurrect removed entries
    await _add_department(db_session, "Facilities")
    set_setting("DEPARTMENTS", "HR,Engineering")

    # Act
    created = await department_service.seed_from_env_if_empty(db_session)

    # Assert
    names = [d.name for d in (await db_session.execute(select(Department))).scalars()]
    assert created == 0
    assert names == ["Facilities"]


# ── Read surface ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_list_returns_only_active_departments(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    await _add_department(db_session, "Engineering", is_active=True)
    await _add_department(db_session, "Closed Team", is_active=False)

    # Act
    body = (await api_client.get("/api/v1/departments")).json()

    # Assert
    assert body["departments"] == ["Engineering"]


@pytest.mark.asyncio
async def test_directory_shows_routing_and_queue_depth_to_hr(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session, "Engineering", chat_id="222")
    await _add_undelivered(db_session, "Engineering")

    # Act
    body = (await api_client.get(DIRECTORY_PATH, headers=headers)).json()

    # Assert
    assert body[0]["telegram_chat_id"] == "222"
    assert body[0]["undelivered_count"] == 1


@pytest.mark.asyncio
async def test_directory_requires_a_session(api_client: AsyncClient) -> None:
    # Arrange / Act
    response = await api_client.get(DIRECTORY_PATH)

    # Assert
    assert response.status_code == 401


# ── Write surface ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_add_a_department(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin, "admin@example.com")

    # Act
    response = await api_client.post(
        DIRECTORY_PATH,
        headers=headers,
        json={"name": "Facilities", "telegram_chat_id": "333"},
    )

    # Assert
    assert response.status_code == 201
    assert response.json()["telegram_chat_id"] == "333"


@pytest.mark.asyncio
async def test_hr_cannot_write_the_directory(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — HR reads routing, admins change it
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.post(
        DIRECTORY_PATH, headers=headers, json={"name": "Facilities"}
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_adding_a_duplicate_department_is_refused(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin, "admin@example.com")
    await _add_department(db_session, "Engineering")

    # Act
    response = await api_client.post(
        DIRECTORY_PATH, headers=headers, json={"name": "Engineering"}
    )

    # Assert
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_updating_sets_the_chat_id_and_active_flag(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin, "admin@example.com")
    department = await _add_department(db_session, "Engineering", chat_id=None)

    # Act
    response = await api_client.put(
        f"{DIRECTORY_PATH}/Engineering",
        headers=headers,
        json={"telegram_chat_id": "444", "is_active": False},
    )

    # Assert
    await db_session.refresh(department)
    assert response.status_code == 200
    assert department.telegram_chat_id == "444"
    assert department.is_active is False


# ── Deletion safety ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_department_with_undelivered_items_is_blocked(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — those confessions were forwarded on the promise that
    # somebody would receive them; dropping the row would strand them
    _, headers = await make_staff(UserRole.admin, "admin@example.com")
    await _add_department(db_session, "Engineering")
    await _add_undelivered(db_session, "Engineering")

    # Act
    response = await api_client.delete(f"{DIRECTORY_PATH}/Engineering", headers=headers)

    # Assert
    remaining = (await db_session.execute(select(Department))).scalars().all()
    assert response.status_code == 409
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_deleting_an_idle_department_succeeds(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin, "admin@example.com")
    await _add_department(db_session, "Engineering")

    # Act
    response = await api_client.delete(f"{DIRECTORY_PATH}/Engineering", headers=headers)

    # Assert
    remaining = (await db_session.execute(select(Department))).scalars().all()
    assert response.status_code == 204
    assert remaining == []


# ── Forward validation & delivery routing ────────────────────────────────


@pytest.mark.asyncio
async def test_forwarding_to_an_inactive_department_is_refused(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    await _add_department(db_session, "Closed Team", is_active=False)
    confession = Confession(
        device_token_hash="b" * 32,
        voice_mask="warm",
        transcript="words",
        pii_stripped=True,
        status=ConfessionStatus.pending,
    )
    db_session.add(confession)
    await db_session.commit()

    # Act
    response = await api_client.post(
        f"/api/v1/confessions/{confession.id}/forward",
        headers={"X-Device-Token-Hash": "b" * 32},
        json={"department": "Closed Team"},
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delivery_queue_carries_the_directory_chat_id(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — the bot no longer keeps its own copy of the routing map
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    await _add_department(db_session, "Engineering", chat_id="555")
    await _add_undelivered(db_session, "Engineering")

    # Act
    body = (
        await api_client.get(
            "/api/v1/delivery/queue",
            headers={"X-Delivery-Api-Key": "delivery-secret"},
        )
    ).json()

    # Assert
    assert body[0]["recipient_chat_id"] == "555"


@pytest.mark.asyncio
async def test_delivery_queue_reports_a_missing_chat_id_as_null(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — an unmapped department was previously only a bot log line,
    # invisible to anyone who could fix it
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    await _add_department(db_session, "Engineering", chat_id=None)
    await _add_undelivered(db_session, "Engineering")

    # Act
    body = (
        await api_client.get(
            "/api/v1/delivery/queue",
            headers={"X-Delivery-Api-Key": "delivery-secret"},
        )
    ).json()

    # Assert
    assert body[0]["recipient_chat_id"] is None
