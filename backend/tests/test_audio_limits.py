"""Server-side recording limits that match the app's (plan task 16.5).

The app stops recording at five minutes (MAX_RECORDING_DURATION_MS); the server
used to check only the file size, so a longer upload was transcribed for many
minutes before anything noticed. Now a recording over the limit is refused with
a typed 413 before any work starts.
"""

from __future__ import annotations

import io
import shutil
import wave
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.api.v1 import stt, voice
from app.main import app
from app.services.audio_probe import duration_seconds
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
needs_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe not installed"
)


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buffer.getvalue()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[stt.get_clock] = lambda: lambda: NOW
    app.dependency_overrides[voice.get_clock] = lambda: lambda: NOW
    stt._last_transcription_at.clear()
    voice._last_mask_at.clear()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@needs_ffprobe
def test_the_duration_of_a_recording_is_measured(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "three.wav"
    path.write_bytes(_wav(3))

    # Act
    seconds = duration_seconds(path)

    # Assert
    assert seconds is not None and abs(seconds - 3) < 0.1


def test_an_unreadable_file_has_no_duration(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "junk.m4a"
    path.write_bytes(b"not audio at all")

    # Act
    seconds = duration_seconds(path)

    # Assert — unknown, so only the size cap applies
    assert seconds is None


def test_without_ffprobe_the_duration_is_unknown(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "x.wav"
    path.write_bytes(_wav(1))

    # Act
    with patch("app.services.audio_probe.shutil.which", return_value=None):
        seconds = duration_seconds(path)

    # Assert
    assert seconds is None


@needs_ffprobe
@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/api/v1/stt", "/api/v1/voice/mask"])
async def test_a_recording_over_the_limit_is_refused_before_any_work(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    # Arrange — a 3-second limit and a 5-second recording
    monkeypatch.setattr("app.config.settings.MAX_RECORDING_SECONDS", 3)
    files = {"audio": ("long.wav", _wav(5), "audio/wav")}

    # Act
    with (
        patch("app.api.v1.stt.WhisperTranscriber.transcribe") as transcribe,
        patch("app.api.v1.voice.VoiceModulator.modulate") as modulate,
    ):
        response = await client.post(
            endpoint, files=files, headers={"X-Device-Token-Hash": "a" * 32}
        )

    # Assert
    assert response.status_code == 413
    assert response.json()["detail"] == "Recording is longer than the 3-second limit"
    assert (transcribe.called, modulate.called) == (False, False)


@needs_ffprobe
@pytest.mark.asyncio
async def test_a_recording_inside_the_limit_is_transcribed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — the limit has a small margin for encoder padding
    monkeypatch.setattr("app.config.settings.MAX_RECORDING_SECONDS", 3)
    files = {"audio": ("ok.wav", _wav(3.2), "audio/wav")}

    # Act
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="fine"):
        response = await client.post(
            "/api/v1/stt", files=files, headers={"X-Device-Token-Hash": "a" * 32}
        )

    # Assert
    assert (response.status_code, response.json()) == (200, {"transcript": "fine"})
