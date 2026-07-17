"""Integration tests for the TTS API (app.api.v1.tts).

Only the EdgeTTS service boundary (``synthesize``) is mocked — request
validation, rate limiting, and file-response wiring run for real, per
AGENTS.md §16.4. Each test gets a frozen, dependency-injected clock
(AGENTS.md §16.5) and a cleared rate-limit store (AGENTS.md §16.3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.tts import _last_synthesis_at, get_clock
from app.main import app

FROZEN_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
DEVICE_HASH = "a" * 32
OTHER_DEVICE_HASH = "b" * 32


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Frozen clock + cleared rate-limit store for a single test."""
    app.dependency_overrides[get_clock] = lambda: lambda: FROZEN_NOW
    _last_synthesis_at.clear()

    # raise_app_exceptions=False: let unhandled domain errors turn into the
    # same 500 response a real server would return, instead of the test
    # transport re-raising them for local debugging.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    _last_synthesis_at.clear()


@pytest.mark.asyncio
async def test_synthesize_speech_returns_audio_wav(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    fake_audio = tmp_path / "tts_fake.wav"
    fake_audio.write_bytes(b"RIFF....WAVEfmt ")
    payload = {"text": "Speak freely, this is sacred."}
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", return_value=str(fake_audio)):
        response = await client.post("/api/v1/tts", json=payload, headers=headers)

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF....WAVEfmt "


@pytest.mark.asyncio
async def test_synthesize_speech_deletes_file_after_response(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: audio must not linger on disk after being sent
    fake_audio = tmp_path / "tts_cleanup.wav"
    fake_audio.write_bytes(b"RIFF")
    payload = {"text": "This message should not persist server-side."}
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", return_value=str(fake_audio)):
        response = await client.post("/api/v1/tts", json=payload, headers=headers)

    # Assert
    assert response.status_code == 200
    assert not fake_audio.exists()


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_empty_text(client: AsyncClient) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    response = await client.post("/api/v1/tts", json={"text": ""}, headers=headers)

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_missing_device_token_header(
    client: AsyncClient,
) -> None:
    # Act
    response = await client.post("/api/v1/tts", json={"text": "Hello."})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_synthesize_speech_returns_500_when_service_fails(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {"text": "This will fail to synthesize."}
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", side_effect=RuntimeError("boom")):
        response = await client.post("/api/v1/tts", json=payload, headers=headers)

    # Assert
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_synthesize_speech_second_call_same_token_is_rate_limited(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: unauthenticated TTS must not allow unbounded
    # cost-abuse from a single device hammering the endpoint
    fake_audio = tmp_path / "tts_rl_first.wav"
    fake_audio.write_bytes(b"RIFF")
    payload = {"text": "First call in the window."}
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", return_value=str(fake_audio)):
        first_response = await client.post("/api/v1/tts", json=payload, headers=headers)
        second_response = await client.post(
            "/api/v1/tts", json=payload, headers=headers
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 429


@pytest.mark.asyncio
async def test_synthesize_speech_different_token_not_rate_limited(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: one device's rate-limit window must not
    # cross-contaminate another device's requests
    fake_audio_one = tmp_path / "tts_rl_one.wav"
    fake_audio_one.write_bytes(b"RIFF")
    fake_audio_two = tmp_path / "tts_rl_two.wav"
    fake_audio_two.write_bytes(b"RIFF")
    payload = {"text": "Hello there."}

    # Act
    with patch(
        "app.api.v1.tts.EdgeTTS.synthesize",
        side_effect=[str(fake_audio_one), str(fake_audio_two)],
    ):
        first_response = await client.post(
            "/api/v1/tts",
            json=payload,
            headers={"X-Device-Token-Hash": DEVICE_HASH},
        )
        second_response = await client.post(
            "/api/v1/tts",
            json=payload,
            headers={"X-Device-Token-Hash": OTHER_DEVICE_HASH},
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 200
