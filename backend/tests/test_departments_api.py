"""Integration tests for the public departments endpoint.

Rewritten for 11.10: the directory moved from the ``DEPARTMENTS`` env string
into the ``departments`` table, so the old cases (which reloaded
``app.config`` and asserted against the env value) were asserting against a
source of truth that no longer exists. The env value now only *seeds* an
empty table, which is covered in test_departments_directory.py.
"""

from __future__ import annotations

import pytest
from app.models.department import Department
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_departments_returns_the_directory_contents(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    db_session.add_all(
        [
            Department(name="HR", telegram_chat_id="1", is_active=True),
            Department(name="Engineering", telegram_chat_id="2", is_active=True),
        ]
    )
    await db_session.commit()

    # Act
    response = await api_client.get("/api/v1/departments")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"departments": ["Engineering", "HR"]}


@pytest.mark.asyncio
async def test_list_departments_is_empty_before_anything_is_configured(
    api_client: AsyncClient,
) -> None:
    # Arrange — an unseeded directory reports nothing rather than inventing
    # defaults the operator never chose
    # Act
    response = await api_client.get("/api/v1/departments")

    # Assert
    assert response.json() == {"departments": []}


@pytest.mark.asyncio
async def test_list_departments_needs_no_credentials(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange — the mobile Forward screen has no session to present
    db_session.add(Department(name="HR", telegram_chat_id="1", is_active=True))
    await db_session.commit()

    # Act
    response = await api_client.get("/api/v1/departments")

    # Assert
    assert response.status_code == 200
