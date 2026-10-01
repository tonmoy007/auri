"""Tests for the HR-facing delivery view and the resend action.

The question this surface exists to answer is "did anything I said actually
reach anyone", so the cases below are mostly about *why* something has not
arrived — previously a bot log line nobody who could fix it ever saw.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.department import Department
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

OVERVIEW_PATH = "/api/v1/delivery/overview"
DELIVERED_AT = datetime(2026, 9, 3, 10, 0, 0, tzinfo=timezone.utc)


async def _add_forwarded(
    session: AsyncSession,
    department: str | None = "Engineering",
    delivered_at: datetime | None = None,
    severity: ModerationSeverity = ModerationSeverity.none,
    acknowledged_at: datetime | None = None,
) -> Confession:
    confession = Confession(
        device_token_hash="a" * 32,
        voice_mask="warm",
        transcript="private words",
        ai_summary="a summary",
        pii_stripped=True,
        status=ConfessionStatus.forwarded,
        recipient_dept=department,
        delivered_at=delivered_at,
        severity=severity.value,
        acknowledged_at=acknowledged_at,
    )
    session.add(confession)
    await session.commit()
    return confession


async def _add_department(
    session: AsyncSession, name: str = "Engineering", chat_id: str | None = "111"
) -> None:
    session.add(Department(name=name, telegram_chat_id=chat_id, is_active=True))
    await session.commit()


# ── What is stuck, and why ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delivered_item_reports_no_blocked_reason(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    await _add_forwarded(db_session, delivered_at=DELIVERED_AT)

    # Act
    body = (await api_client.get(OVERVIEW_PATH, headers=headers)).json()

    # Assert
    assert body[0]["blocked_reason"] is None
    assert body[0]["delivered_at"] is not None


@pytest.mark.asyncio
async def test_an_unmapped_department_is_named_as_the_reason(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the failure that used to be invisible
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session, chat_id=None)
    await _add_forwarded(db_session)

    # Act
    body = (await api_client.get(OVERVIEW_PATH, headers=headers)).json()

    # Assert
    assert "No Telegram chat is configured for Engineering" in body[0]["blocked_reason"]


@pytest.mark.asyncio
async def test_an_unacknowledged_crisis_item_says_it_is_held(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    await _add_forwarded(db_session, severity=ModerationSeverity.crisis)

    # Act
    body = (await api_client.get(OVERVIEW_PATH, headers=headers)).json()

    # Assert
    assert "acknowledges it" in body[0]["blocked_reason"]


@pytest.mark.asyncio
async def test_a_routable_pending_item_says_it_is_simply_waiting(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    await _add_forwarded(db_session)

    # Act
    body = (await api_client.get(OVERVIEW_PATH, headers=headers)).json()

    # Assert
    assert body[0]["blocked_reason"] == "Waiting for the bot's next delivery poll"


@pytest.mark.asyncio
async def test_the_overview_exposes_no_confession_content(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — answering "did it arrive" does not require reading what was said
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    await _add_forwarded(db_session)

    # Act
    response = await api_client.get(OVERVIEW_PATH, headers=headers)

    # Assert
    assert "private words" not in response.text
    assert "a summary" not in response.text


@pytest.mark.asyncio
async def test_the_overview_requires_a_session(api_client: AsyncClient) -> None:
    # Arrange / Act
    response = await api_client.get(OVERVIEW_PATH)

    # Assert
    assert response.status_code == 401


# ── Resend ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resending_puts_a_delivered_item_back_in_the_queue(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — for when the backend recorded a delivery nobody received
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    confession = await _add_forwarded(db_session, delivered_at=DELIVERED_AT)

    # Act
    response = await api_client.post(
        f"/api/v1/delivery/{confession.id}/resend", headers=headers
    )

    # Assert
    await db_session.refresh(confession)
    assert response.status_code == 200
    assert confession.delivered_at is None


@pytest.mark.asyncio
async def test_resending_is_audited(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    confession = await _add_forwarded(db_session, delivered_at=DELIVERED_AT)

    # Act
    await api_client.post(f"/api/v1/delivery/{confession.id}/resend", headers=headers)

    # Assert
    event = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "delivery.retry")
            )
        )
        .scalars()
        .one()
    )
    assert event.actor_user_id == actor.id
    assert event.target_confession_id == confession.id


@pytest.mark.asyncio
async def test_resending_an_undelivered_item_is_refused(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the bot already retries these on every poll; a second
    # "retry" button would imply an action that does nothing
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    confession = await _add_forwarded(db_session)

    # Act
    response = await api_client.post(
        f"/api/v1/delivery/{confession.id}/resend", headers=headers
    )

    # Assert
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_resent_item_reappears_in_the_bot_delivery_queue(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting,
) -> None:
    # Arrange
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    _, headers = await make_staff(UserRole.hr)
    await _add_department(db_session)
    confession = await _add_forwarded(db_session, delivered_at=DELIVERED_AT)

    # Act
    await api_client.post(f"/api/v1/delivery/{confession.id}/resend", headers=headers)
    queue = (
        await api_client.get(
            "/api/v1/delivery/queue", headers={"X-Delivery-Api-Key": "delivery-secret"}
        )
    ).json()

    # Assert
    assert [item["id"] for item in queue] == [str(confession.id)]
