"""Integration tests for the STT API (app.api.v1.stt).

Only the WhisperTranscriber service boundary (``transcribe``) is mocked —
request validation, rate limiting, upload-size guards, and temp-file
cleanup run for real, per AGENTS.md §16.4. Each test gets a frozen,
dependency-injected clock (AGENTS.md §16.5) and a cleared rate-limit
store (AGENTS.md §16.3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.stt import _last_transcription_at, get_clock
from app.main import app

FROZEN_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
DEVICE_HASH = "a" * 32
OTHER_DEVICE_HASH = "b" * 32
FAKE_AUDIO_BYTES = b"RIFF....WAVEfmt fake audio payload"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Frozen clock + cleared rate-limit store for a single test."""
    app.dependency_overrides[get_clock] = lambda: lambda: FROZEN_NOW
    _last_transcription_at.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    _last_transcription_at.clear()


def _audio_file(name: str = "confession.m4a") -> dict:
    return {"audio": (name, FAKE_AUDIO_BYTES, "audio/m4a")}


@pytest.mark.asyncio
async def test_transcribe_audio_returns_transcript(client: AsyncClient) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch(
        "app.api.v1.stt.WhisperTranscriber.transcribe",
        return_value="this is what I said",
    ):
        response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 200
    assert response.json() == {"transcript": "this is what I said"}


@pytest.mark.asyncio
async def test_transcribe_audio_deletes_temp_file_after_response(
    client: AsyncClient,
) -> None:
    # Arrange — regression: uploaded audio must not linger on disk, per the
    # plan's Data Privacy Design ("audio deleted from server after transcription")
    headers = {"X-Device-Token-Hash": DEVICE_HASH}
    captured_path = {}

    def fake_transcribe(self, audio_path):
        captured_path["path"] = audio_path
        assert audio_path.exists()
        return "captured mid-call"

    # Act
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", fake_transcribe):
        response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 200
    assert not captured_path["path"].exists()


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_empty_file(client: AsyncClient) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    response = await client.post(
        "/api/v1/stt",
        files={"audio": ("empty.m4a", b"", "audio/m4a")},
        headers=headers,
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_oversized_file(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — regression: an unbounded upload must not reach Whisper at all
    monkeypatch.setattr("app.api.v1.stt.settings.STT_MAX_UPLOAD_BYTES", 10)
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    response = await client.post("/api/v1/stt", files=_audio_file(), headers=headers)

    # Assert
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_missing_device_token_header(
    client: AsyncClient,
) -> None:
    # Act
    response = await client.post("/api/v1/stt", files=_audio_file())

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_silent_result(client: AsyncClient) -> None:
    # Arrange — Whisper returning empty/whitespace means no usable speech
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="   "):
        response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transcribe_audio_returns_500_when_service_fails(
    client: AsyncClient,
) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch(
        "app.api.v1.stt.WhisperTranscriber.transcribe",
        side_effect=RuntimeError("boom"),
    ):
        response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_transcribe_audio_second_call_same_token_is_rate_limited(
    client: AsyncClient,
) -> None:
    # Arrange — regression: unauthenticated STT must not allow unbounded
    # cost-abuse (CPU-heavy Whisper inference) from a single device
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="hello"):
        first_response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )
        second_response = await client.post(
            "/api/v1/stt", files=_audio_file(), headers=headers
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 429


@pytest.mark.asyncio
async def test_transcribe_audio_different_token_not_rate_limited(
    client: AsyncClient,
) -> None:
    # Arrange — regression: one device's rate-limit window must not
    # cross-contaminate another device's requests
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="hello"):
        first_response = await client.post(
            "/api/v1/stt",
            files=_audio_file(),
            headers={"X-Device-Token-Hash": DEVICE_HASH},
        )
        second_response = await client.post(
            "/api/v1/stt",
            files=_audio_file(),
            headers={"X-Device-Token-Hash": OTHER_DEVICE_HASH},
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 200
