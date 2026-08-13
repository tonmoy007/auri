"""Integration tests for the confessions API (app.api.v1.confessions).

Each test gets a fresh in-memory SQLite database (via the ``client``
fixture, function-scoped per AGENTS.md §16.3) and a frozen, dependency-
injected clock (AGENTS.md §16.5). Only the LLMService boundary is mocked;
rate limiting, ownership checks, and status transitions run for real.

Note: the frozen clock values used here are timezone-naive. SQLite has no
native timestamptz support, so a value round-tripped through the DB comes
back naive; using naive values throughout keeps every read/write consistent
without touching the production clock (which correctly uses
``datetime.now(timezone.utc)`` against the real Postgres backend).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.api.v1.confessions import _FALLBACK_COUNSELOR_RESPONSE, get_clock
from app.database import get_async_session
from app.main import app
from app.models.base import Base
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FROZEN_NOW = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001 — deliberately naive, see module docstring
DEVICE_HASH = "a" * 32
OTHER_DEVICE_HASH = "b" * 32
DEIDENTIFIED_TEXT = "raw with [EMAIL]"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Fresh in-memory DB + dependency overrides for a single test."""
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
    app.dependency_overrides[get_clock] = lambda: lambda: FROZEN_NOW

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


def _set_clock(now: datetime) -> None:
    app.dependency_overrides[get_clock] = lambda: lambda: now


async def _create_confession(
    client: AsyncClient, device_hash: str = DEVICE_HASH
) -> dict:
    payload = {
        "device_token_hash": device_hash,
        "voice_mask": "warm",
        "transcript": "raw with john@example.com",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief summary.",
        ),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
    ):
        response = await client.post("/api/v1/confessions", json=payload)
    return response.json()


@pytest.mark.asyncio
async def test_create_confession_returns_201_with_deidentified_transcript(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "raw with john@example.com",
    }

    # Act
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief summary.",
        ),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    body = response.json()
    assert response.status_code == 201
    assert body["pii_stripped"] is True
    assert body["transcript"] == DEIDENTIFIED_TEXT
    assert body["status"] == "pending"
    assert body["category"] == "work"
    assert body["ai_summary"] == "A brief summary."


@pytest.mark.asyncio
async def test_create_confession_rejects_empty_transcript_with_422(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "",
    }

    # Act
    response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_confession_rejects_invalid_voice_mask_with_422(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "not-a-mask",
        "transcript": "a valid transcript",
    }

    # Act
    response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_confession_rate_limits_second_submission_within_window(
    client: AsyncClient,
) -> None:
    # Arrange
    await _create_confession(client)
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "another confession right away",
    }

    # Act
    with patch(
        "app.api.v1.confessions.LLMService.deidentify",
        return_value=DEIDENTIFIED_TEXT,
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_create_confession_allowed_after_rate_limit_window_elapses(
    client: AsyncClient,
) -> None:
    # Arrange
    await _create_confession(client)
    _set_clock(FROZEN_NOW + timedelta(seconds=301))
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "a confession after the window passed",
    }

    # Act
    with patch(
        "app.api.v1.confessions.LLMService.deidentify",
        return_value=DEIDENTIFIED_TEXT,
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_confession_rate_limit_survives_naive_stored_timestamp(
    client: AsyncClient,
) -> None:
    # Arrange — regression, found live 2026-08-13: SQLite (dev/QA) has no
    # native timestamptz support, so `last_confession_at` round-trips as
    # naive even though the column is `DateTime(timezone=True)` and `now`
    # is always tz-aware UTC (see `get_clock`). A device's *second* real
    # confession crashed 500 instead of being rate-limited (429) because
    # the existing tests' frozen clock is naive on both sides, which never
    # exercises the aware-vs-naive subtraction that production's real
    # clock actually produces against SQLite.
    await _create_confession(client)
    _set_clock(FROZEN_NOW.replace(tzinfo=timezone.utc) + timedelta(seconds=5))
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "another confession right away, with an aware clock this time",
    }

    # Act
    with patch(
        "app.api.v1.confessions.LLMService.deidentify",
        return_value=DEIDENTIFIED_TEXT,
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_get_confession_returns_404_for_unknown_id(
    client: AsyncClient,
) -> None:
    # Arrange
    unknown_id = uuid.uuid4()

    # Act
    response = await client.get(
        f"/api/v1/confessions/{unknown_id}",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_confession_returns_403_for_wrong_device_token(
    client: AsyncClient,
) -> None:
    # Arrange
    created = await _create_confession(client)

    # Act
    response = await client.get(
        f"/api/v1/confessions/{created['id']}",
        headers={"X-Device-Token-Hash": OTHER_DEVICE_HASH},
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_confession_soft_deletes(client: AsyncClient) -> None:
    # Arrange
    created = await _create_confession(client)

    # Act
    delete_response = await client.delete(
        f"/api/v1/confessions/{created['id']}",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )
    get_response = await client.get(
        f"/api/v1/confessions/{created['id']}",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert delete_response.status_code == 204
    assert get_response.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_forward_confession_returns_409_when_not_pending(
    client: AsyncClient,
) -> None:
    # Arrange
    created = await _create_confession(client)
    body = {"department": "HR"}
    await client.post(
        f"/api/v1/confessions/{created['id']}/forward",
        json=body,
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Act
    response = await client.post(
        f"/api/v1/confessions/{created['id']}/forward",
        json=body,
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_forward_confession_sets_recipient_department(
    client: AsyncClient,
) -> None:
    # Arrange
    created = await _create_confession(client)
    body = {"department": "Engineering"}

    # Act
    response = await client.post(
        f"/api/v1/confessions/{created['id']}/forward",
        json=body,
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["recipient_dept"] == "Engineering"
    assert response.json()["status"] == "forwarded"


@pytest.mark.asyncio
async def test_forward_confession_rejects_unknown_department(
    client: AsyncClient,
) -> None:
    # Arrange — regression: department must come from the configured
    # directory (GET /api/v1/departments), not arbitrary free text.
    created = await _create_confession(client)
    body = {"department": "Nonexistent Dept"}

    # Act
    response = await client.post(
        f"/api/v1/confessions/{created['id']}/forward",
        json=body,
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_confessions_returns_only_requesting_devices_history(
    client: AsyncClient,
) -> None:
    # Arrange
    await _create_confession(client, device_hash=DEVICE_HASH)
    await _create_confession(client, device_hash=OTHER_DEVICE_HASH)

    # Act
    response = await client.get(
        "/api/v1/confessions",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert len(body) == 1
    assert body[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_list_confessions_orders_newest_first(client: AsyncClient) -> None:
    # Arrange — created_at is a DB-side server_default (real wall clock, not
    # the injected app clock used for rate limiting), and SQLite's
    # CURRENT_TIMESTAMP only has 1-second resolution, so a real sleep is
    # needed to force two distinguishable timestamps.
    first = await _create_confession(client)
    _set_clock(FROZEN_NOW + timedelta(seconds=301))
    await asyncio.sleep(1.1)
    second = await _create_confession(client)

    # Act
    response = await client.get(
        "/api/v1/confessions",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    body = response.json()
    assert [item["id"] for item in body] == [second["id"], first["id"]]


@pytest.mark.asyncio
async def test_list_confessions_excludes_soft_deleted(client: AsyncClient) -> None:
    # Arrange
    created = await _create_confession(client)
    await client.delete(
        f"/api/v1/confessions/{created['id']}",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Act
    response = await client.get(
        "/api/v1/confessions",
        headers={"X-Device-Token-Hash": DEVICE_HASH},
    )

    # Assert
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_confessions_returns_empty_for_unknown_device(
    client: AsyncClient,
) -> None:
    # Act
    response = await client.get(
        "/api/v1/confessions",
        headers={"X-Device-Token-Hash": "c" * 32},
    )

    # Assert
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_confession_moderates_raw_transcript_not_deidentified(
    client: AsyncClient,
) -> None:
    # Arrange — regression (2026-07-18): moderation must run on the original
    # transcript, not the de-identified one, so a broken/refused deidentify
    # call can never blind the safety check. Found via live testing against
    # a local Ollama model, which refused a self-harm-adjacent redaction
    # prompt outright, silently causing moderate() to miss it.
    raw_transcript = "raw transcript carrying the real safety signal"
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": raw_transcript,
    }

    # Act
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief summary.",
        ),
        patch(
            "app.api.v1.confessions.LLMService.moderate", return_value=False
        ) as mock_moderate,
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 201
    mock_moderate.assert_called_once_with(raw_transcript)


@pytest.mark.asyncio
async def test_create_confession_includes_counselor_response(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "raw with john@example.com",
    }

    # Act
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief summary.",
        ),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
        patch(
            "app.api.v1.confessions.LLMService.counsel",
            return_value="You have been heard.",
        ) as mock_counsel,
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    body = response.json()
    assert response.status_code == 201
    assert body["counselor_response"] == "You have been heard."
    mock_counsel.assert_called_once_with(DEIDENTIFIED_TEXT)


@pytest.mark.asyncio
async def test_create_confession_falls_back_when_counsel_fails(
    client: AsyncClient,
) -> None:
    # Arrange — a confessor must never see an empty response, even if the
    # LLM call for it fails; the confession itself still saves fine.
    payload = {
        "device_token_hash": DEVICE_HASH,
        "voice_mask": "warm",
        "transcript": "raw with john@example.com",
    }

    # Act
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief summary.",
        ),
        patch("app.api.v1.confessions.LLMService.moderate", return_value=False),
        patch(
            "app.api.v1.confessions.LLMService.counsel",
            side_effect=RuntimeError("LLM unavailable"),
        ),
    ):
        response = await client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 201
    assert response.json()["counselor_response"] == _FALLBACK_COUNSELOR_RESPONSE


@pytest.mark.asyncio
async def test_preview_confession_returns_summary_without_persisting(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {"transcript": "raw with john@example.com"}

    # Act
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify",
            return_value=DEIDENTIFIED_TEXT,
        ),
        patch(
            "app.api.v1.confessions.LLMService.summarize",
            return_value="A brief preview summary.",
        ) as mock_summarize,
        patch("app.api.v1.confessions.LLMService.counsel") as mock_counsel,
    ):
        response = await client.post("/api/v1/confessions/preview", json=payload)
        history = await client.get(
            "/api/v1/confessions",
            headers={"X-Device-Token-Hash": DEVICE_HASH},
        )

    # Assert
    assert response.status_code == 200
    assert response.json() == {"ai_summary": "A brief preview summary."}
    mock_summarize.assert_called_once_with(DEIDENTIFIED_TEXT)
    mock_counsel.assert_not_called()
    assert history.json() == []
