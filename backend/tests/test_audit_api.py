"""Tests for the audit trail service and its admin-only read API."""

from __future__ import annotations

import uuid

import pytest
from app.models.audit_event import AuditAction, AuditEvent, ContentTier
from app.models.user import UserRole
from app.services import audit_service
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

AUDIT_PATH = "/api/v1/audit"


# ── Recording ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_persists_every_field_of_a_raw_read(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    confession_id = uuid.uuid4()

    # Act
    await audit_service.record(
        db_session,
        actor=user,
        action=AuditAction.confession_read,
        target_confession_id=confession_id,
        content_tier=ContentTier.raw,
        justification="escalated by moderator",
        source_ip="10.0.0.7",
    )
    await db_session.commit()

    # Assert
    stored = (await db_session.execute(select(AuditEvent))).scalar_one()
    assert stored.actor_user_id == user.id
    assert stored.action == "confession.read"
    assert stored.target_confession_id == confession_id
    assert stored.content_tier == "raw"
    assert stored.justification == "escalated by moderator"
    assert stored.source_ip == "10.0.0.7"


@pytest.mark.asyncio
async def test_record_leaves_content_fields_empty_for_a_non_content_action(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.admin)

    # Act
    await audit_service.record(
        db_session, actor=user, action=AuditAction.department_write
    )
    await db_session.commit()

    # Assert
    stored = (await db_session.execute(select(AuditEvent))).scalar_one()
    assert stored.content_tier is None
    assert stored.target_confession_id is None


@pytest.mark.asyncio
async def test_list_events_filters_by_action(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    await audit_service.record(db_session, user, AuditAction.confession_read)
    await audit_service.record(db_session, user, AuditAction.moderation_approve)
    await db_session.commit()

    # Act
    events, total = await audit_service.list_events(
        db_session, action=AuditAction.moderation_approve
    )

    # Assert
    assert total == 1
    assert events[0].action == "moderation.approve"


@pytest.mark.asyncio
async def test_list_events_filters_by_target_confession(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    wanted = uuid.uuid4()
    await audit_service.record(
        db_session, user, AuditAction.confession_read, target_confession_id=wanted
    )
    await audit_service.record(
        db_session,
        user,
        AuditAction.confession_read,
        target_confession_id=uuid.uuid4(),
    )
    await db_session.commit()

    # Act
    events, total = await audit_service.list_events(
        db_session, target_confession_id=wanted
    )

    # Assert
    assert total == 1
    assert events[0].target_confession_id == wanted


@pytest.mark.asyncio
async def test_list_events_paginates_and_reports_the_full_total(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    for _ in range(5):
        await audit_service.record(db_session, user, AuditAction.confession_read)
    await db_session.commit()

    # Act
    events, total = await audit_service.list_events(db_session, limit=2, offset=0)

    # Assert
    assert len(events) == 2
    assert total == 5


@pytest.mark.asyncio
async def test_list_events_caps_an_oversized_page_request(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    await audit_service.record(db_session, user, AuditAction.confession_read)
    await db_session.commit()

    # Act
    events, _ = await audit_service.list_events(db_session, limit=10_000)

    # Assert — the request is clamped, not honoured
    assert len(events) == 1


# ── Read API ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_api_returns_events_to_an_admin(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    admin, headers = await make_staff(UserRole.admin, "admin@example.com")
    await audit_service.record(
        db_session, admin, AuditAction.confession_read, content_tier=ContentTier.summary
    )
    await db_session.commit()

    # Act
    response = await api_client.get(AUDIT_PATH, headers=headers)

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["content_tier"] == "summary"


@pytest.mark.asyncio
async def test_audit_api_refuses_an_hr_session(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — the trail records which HR person read which confession,
    # so HR must not be able to read it
    _, headers = await make_staff(UserRole.hr, "hr@example.com")

    # Act
    response = await api_client.get(AUDIT_PATH, headers=headers)

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_audit_api_refuses_an_unauthenticated_request(
    api_client: AsyncClient,
) -> None:
    # Arrange / Act
    response = await api_client.get(AUDIT_PATH)

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_audit_api_exposes_no_mutation_routes(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — append-only means the API offers no way to rewrite history
    _, headers = await make_staff(UserRole.admin, "admin@example.com")

    # Act
    deleted = await api_client.delete(f"{AUDIT_PATH}/{uuid.uuid4()}", headers=headers)
    updated = await api_client.put(AUDIT_PATH, headers=headers, json={})
    created = await api_client.post(AUDIT_PATH, headers=headers, json={})

    # Assert
    assert [deleted.status_code, updated.status_code, created.status_code] == [
        404,
        405,
        405,
    ]
