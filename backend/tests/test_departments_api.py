"""Integration tests for the departments directory API (app.api.v1.departments)."""

from __future__ import annotations

import importlib
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_list_departments_returns_default_directory(client: AsyncClient) -> None:
    # Act
    response = await client.get("/api/v1/departments")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"departments": ["HR", "Engineering", "Management"]}


@pytest.mark.asyncio
async def test_list_departments_reflects_configured_setting(
    client: AsyncClient, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setenv("DEPARTMENTS", "Legal, Finance ,, Security")
    import app.config as config_module
    import app.api.v1.departments as departments_module

    importlib.reload(config_module)
    monkeypatch.setattr(departments_module, "settings", config_module.settings)

    # Act
    response = await client.get("/api/v1/departments")

    # Assert
    assert response.json() == {"departments": ["Legal", "Finance", "Security"]}

    # Cleanup
    monkeypatch.delenv("DEPARTMENTS", raising=False)
    importlib.reload(config_module)
    importlib.reload(departments_module)
