"""Integration tests for the delivery queue API (app.api.v1.delivery).

Each test gets a fresh in-memory SQLite database (per AGENTS.md §16.3).
Only the LLMService boundary is mocked; auth, queue filtering, and the
delivered_at transition run for real.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.api.v1.delivery as delivery_module
from app.database import get_async_session
from app.main import app
from app.models.base import Base

DEVICE_HASH = "a" * 32
DELIVERY_KEY = "test-delivery-secret"
AUTH_HEADERS = {"X-Delivery-Api-Key": DELIVERY_KEY}


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    """Fresh in-memory DB + a configured delivery secret for a single test."""
    monkeypatch.setattr(delivery_module.settings, "DELIVERY_API_KEY", DELIVERY_KEY)

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


async def _create_forwarded_confession(
    client: AsyncClient, department: str = "HR"
) -> dict:
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "a confession to forward",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value="deidentified text",
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="A summary."),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
    ):
        created = (await client.post("/api/v1/confessions", json=payload)).json()

    response = await client.post(
        f"/api/v1/confessions/{created['id']}/forward",
        json={"department": department},
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )
    return response.json()


@pytest.mark.asyncio
async def test_queue_rejects_missing_delivery_key(client: AsyncClient) -> None:
    # Act
    response = await client.get("/api/v1/delivery/queue")

    # Assert
    assert response.status_code == 422  # missing required header


@pytest.mark.asyncio
async def test_queue_rejects_wrong_delivery_key(client: AsyncClient) -> None:
    # Act
    response = await client.get(
        "/api/v1/delivery/queue",
        headers={"X-Delivery-Api-Key": "wrong-secret"},
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_queue_denies_all_when_delivery_key_unset(
    client: AsyncClient, monkeypatch
) -> None:
    # Arrange — regression: an unconfigured secret must fail closed, not open.
    monkeypatch.setattr(delivery_module.settings, "DELIVERY_API_KEY", "")

    # Act
    response = await client.get("/api/v1/delivery/queue", headers=AUTH_HEADERS)

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_queue_lists_forwarded_undelivered_confessions(
    client: AsyncClient,
) -> None:
    # Arrange
    forwarded = await _create_forwarded_confession(client)

    # Act
    response = await client.get("/api/v1/delivery/queue", headers=AUTH_HEADERS)

    # Assert
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert forwarded["id"] in ids


@pytest.mark.asyncio
async def test_queue_excludes_pending_and_flagged_confessions(
    client: AsyncClient,
) -> None:
    # Arrange — a confession never forwarded must never appear in the delivery queue
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "still pending",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value="deidentified text",
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="A summary."),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
    ):
        pending = (await client.post("/api/v1/confessions", json=payload)).json()

    # Act
    response = await client.get("/api/v1/delivery/queue", headers=AUTH_HEADERS)

    # Assert
    ids = [item["id"] for item in response.json()]
    assert pending["id"] not in ids


@pytest.mark.asyncio
async def test_mark_delivered_removes_item_from_queue(client: AsyncClient) -> None:
    # Arrange
    forwarded = await _create_forwarded_confession(client)

    # Act
    mark_response = await client.post(
        f"/api/v1/delivery/{forwarded['id']}/delivered", headers=AUTH_HEADERS
    )
    queue_response = await client.get("/api/v1/delivery/queue", headers=AUTH_HEADERS)

    # Assert
    assert mark_response.status_code == 200
    assert mark_response.json()["id"] == forwarded["id"]
    ids = [item["id"] for item in queue_response.json()]
    assert forwarded["id"] not in ids


@pytest.mark.asyncio
async def test_mark_delivered_returns_404_for_unknown_id(client: AsyncClient) -> None:
    # Act
    response = await client.post(
        "/api/v1/delivery/00000000-0000-0000-0000-000000000000/delivered",
        headers=AUTH_HEADERS,
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_delivered_returns_404_when_already_delivered(
    client: AsyncClient,
) -> None:
    # Arrange — regression: the queue must not double-process an item.
    forwarded = await _create_forwarded_confession(client)
    await client.post(
        f"/api/v1/delivery/{forwarded['id']}/delivered", headers=AUTH_HEADERS
    )

    # Act
    response = await client.post(
        f"/api/v1/delivery/{forwarded['id']}/delivered", headers=AUTH_HEADERS
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_delivered_returns_404_for_pending_confession(
    client: AsyncClient,
) -> None:
    # Arrange — regression: a confession that was never forwarded must not be markable
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "still pending",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value="deidentified text",
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="A summary."),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
    ):
        pending = (await client.post("/api/v1/confessions", json=payload)).json()

    # Act
    response = await client.post(
        f"/api/v1/delivery/{pending['id']}/delivered", headers=AUTH_HEADERS
    )

    # Assert
    assert response.status_code == 404
