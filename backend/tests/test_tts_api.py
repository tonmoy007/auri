"""Integration tests for the TTS API (app.api.v1.tts).

Only the EdgeTTS service boundary (``synthesize``) is mocked — request
validation and file-response wiring run for real, per AGENTS.md §16.4.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: let unhandled domain errors turn into the
    # same 500 response a real server would return, instead of the test
    # transport re-raising them for local debugging.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_synthesize_speech_returns_audio_wav(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    fake_audio = tmp_path / "tts_fake.wav"
    fake_audio.write_bytes(b"RIFF....WAVEfmt ")
    payload = {"text": "Speak freely, this is sacred."}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", return_value=str(fake_audio)):
        response = await client.post("/api/v1/tts", json=payload)

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

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", return_value=str(fake_audio)):
        response = await client.post("/api/v1/tts", json=payload)

    # Assert
    assert response.status_code == 200
    assert not fake_audio.exists()


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_empty_text(client: AsyncClient) -> None:
    # Act
    response = await client.post("/api/v1/tts", json={"text": ""})

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_synthesize_speech_returns_500_when_service_fails(
    client: AsyncClient,
) -> None:
    # Arrange
    payload = {"text": "This will fail to synthesize."}

    # Act
    with patch("app.api.v1.tts.EdgeTTS.synthesize", side_effect=RuntimeError("boom")):
        response = await client.post("/api/v1/tts", json=payload)

    # Assert
    assert response.status_code == 500
