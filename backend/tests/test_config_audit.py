"""Tests that a change to a dashboard setting leaves an audit row.

Flipping the Guide's kill switch or repointing Ollama changes where questions go and
whether they are asked at all, so it must be accountable. The row names the key and
what was done, never the value: a value can be an address or a secret.
"""

from __future__ import annotations

import pytest
from app.models.audit_event import AuditEvent
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SettingPatcher, StaffFactory

CONFIG = "/api/v1/admin/config"
SECRET_VALUE = "https://value-that-must-not-be-audited.example"


async def _rows(db: AsyncSession) -> list[AuditEvent]:
    return list((await db.execute(select(AuditEvent))).scalars().all())


@pytest.mark.asyncio
async def test_setting_a_value_is_audited_by_key_without_the_value(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    admin, headers = await make_staff(UserRole.admin)

    # Act
    response = await api_client.put(
        CONFIG,
        json={"key": "OLLAMA_BASE_URL", "value": SECRET_VALUE},
        headers=headers,
    )

    # Assert
    assert response.status_code == 200
    rows = await _rows(db_session)
    assert [(r.action, r.actor_user_id, r.detail) for r in rows] == [
        ("config.write", admin.id, "set OLLAMA_BASE_URL")
    ]
    assert SECRET_VALUE not in str(rows[0].detail)


@pytest.mark.asyncio
async def test_clearing_a_value_is_audited(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await api_client.delete(f"{CONFIG}/PRIEST_MODE_ENABLED", headers=headers)

    # Assert
    assert response.status_code == 200
    assert [(r.action, r.detail) for r in await _rows(db_session)] == [
        ("config.write", "cleared PRIEST_MODE_ENABLED")
    ]


@pytest.mark.asyncio
async def test_the_shared_admin_key_is_audited_under_its_label(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ADMIN_API_KEY", "shared-admin-key-for-this-test")

    # Act
    response = await api_client.put(
        CONFIG,
        json={"key": "PRIEST_MODE_ENABLED", "value": "true"},
        headers={"X-Admin-Api-Key": "shared-admin-key-for-this-test"},
    )

    # Assert
    assert response.status_code == 200
    rows = await _rows(db_session)
    assert [(r.actor_user_id, r.actor_label) for r in rows] == [(None, "admin-api-key")]


@pytest.mark.asyncio
async def test_a_refused_write_is_not_audited(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await api_client.put(
        CONFIG,
        json={"key": "PRIEST_LLM_BASE_URL", "value": SECRET_VALUE},
        headers=headers,
    )

    # Assert
    assert response.status_code == 422
    assert await _rows(db_session) == []
