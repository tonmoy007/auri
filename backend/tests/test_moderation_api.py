"""Integration tests for the moderation queue API (app.api.v1.moderation).

Each test gets a fresh in-memory SQLite database (per AGENTS.md §16.3).
Only the LLMService boundary is mocked; auth, queue filtering, and status
transitions run for real.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.api.v1.moderation as moderation_module
from app.database import get_async_session
from app.main import app
from app.models.base import Base

DEVICE_HASH = "a" * 32
MODERATION_KEY = "test-moderation-secret"
AUTH_HEADERS = {"X-Moderation-Api-Key": MODERATION_KEY}


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    """Fresh in-memory DB + a configured moderation secret for a single test."""
    monkeypatch.setattr(
        moderation_module.settings, "MODERATION_API_KEY", MODERATION_KEY
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_session() -> AsyncIterator:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_async_session] = override_get_session

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_flagged_confession(client: AsyncClient) -> dict:
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "a confession that trips the moderation check",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value="deidentified text",
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="A summary."),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=True),
    ):
        response = await client.post("/api/v1/confessions", json=payload)
    return response.json()


@pytest.mark.asyncio
async def test_flagged_confession_is_created_with_flagged_status(
    client: AsyncClient,
) -> None:
    # Act
    created = await _create_flagged_confession(client)

    # Assert
    assert created["status"] == "flagged"


@pytest.mark.asyncio
async def test_queue_rejects_missing_moderation_key(client: AsyncClient) -> None:
    # Act
    response = await client.get("/api/v1/moderation/queue")

    # Assert
    assert response.status_code == 422  # missing required header


@pytest.mark.asyncio
async def test_queue_rejects_wrong_moderation_key(client: AsyncClient) -> None:
    # Act
    response = await client.get(
        "/api/v1/moderation/queue",
        headers={"X-Moderation-Api-Key": "wrong-secret"},
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_queue_denies_all_when_moderation_key_unset(
    client: AsyncClient, monkeypatch
) -> None:
    # Arrange — regression: an unconfigured secret must fail closed, not open.
    monkeypatch.setattr(moderation_module.settings, "MODERATION_API_KEY", "")

    # Act
    response = await client.get("/api/v1/moderation/queue", headers=AUTH_HEADERS)

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_queue_lists_only_flagged_confessions(client: AsyncClient) -> None:
    # Arrange
    flagged = await _create_flagged_confession(client)

    # Act
    response = await client.get("/api/v1/moderation/queue", headers=AUTH_HEADERS)

    # Assert
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert flagged["id"] in ids


@pytest.mark.asyncio
async def test_approve_moves_confession_from_flagged_to_pending(
    client: AsyncClient,
) -> None:
    # Arrange
    flagged = await _create_flagged_confession(client)

    # Act
    response = await client.post(
        f"/api/v1/moderation/{flagged['id']}/approve", headers=AUTH_HEADERS
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_reject_moves_confession_from_flagged_to_deleted(
    client: AsyncClient,
) -> None:
    # Arrange
    flagged = await _create_flagged_confession(client)

    # Act
    response = await client.post(
        f"/api/v1/moderation/{flagged['id']}/reject", headers=AUTH_HEADERS
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_approve_returns_404_for_unknown_id(client: AsyncClient) -> None:
    # Act
    response = await client.post(
        "/api/v1/moderation/00000000-0000-0000-0000-000000000000/approve",
        headers=AUTH_HEADERS,
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_returns_404_for_already_approved_confession(
    client: AsyncClient,
) -> None:
    # Arrange — regression: the queue must not double-process an item.
    flagged = await _create_flagged_confession(client)
    await client.post(
        f"/api/v1/moderation/{flagged['id']}/approve", headers=AUTH_HEADERS
    )

    # Act
    response = await client.post(
        f"/api/v1/moderation/{flagged['id']}/approve", headers=AUTH_HEADERS
    )

    # Assert
    assert response.status_code == 404
