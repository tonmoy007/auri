"""Integration tests for the voice masking API (app.api.v1.voice).

Only the VoiceModulator service boundary (``modulate``) is mocked — request
validation, rate limiting, upload-size guards, and temp-file cleanup run
for real, per AGENTS.md §16.4. Each test gets a frozen, dependency-injected
clock (AGENTS.md §16.5) and a cleared rate-limit store (AGENTS.md §16.3).
"""

from __future__ import annotations

import asyncio
import base64
import threading
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.api.v1.voice import _last_mask_at, get_clock
from app.main import app
from httpx import ASGITransport, AsyncClient

FROZEN_NOW = datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc)
DEVICE_HASH = "a" * 32
OTHER_DEVICE_HASH = "b" * 32
FAKE_AUDIO_BYTES = b"fake AAC audio payload"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Frozen clock + cleared rate-limit store for a single test."""
    app.dependency_overrides[get_clock] = lambda: lambda: FROZEN_NOW
    _last_mask_at.clear()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    _last_mask_at.clear()


def _audio_file(name: str = "confession.aac") -> dict:
    return {"audio": (name, FAKE_AUDIO_BYTES, "audio/aac")}


@pytest.mark.asyncio
async def test_mask_voice_returns_masked_audio_wav(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    masked = tmp_path / "masked.wav"
    masked.write_bytes(b"RIFF....WAVEfmt masked")
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate", return_value=str(masked)
    ) as mock_modulate:
        response = await client.post(
            "/api/v1/voice/mask",
            files=_audio_file(),
            data={"mask": "ethereal"},
            headers=headers,
        )

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert base64.b64decode(body["audio_base64"]) == b"RIFF....WAVEfmt masked"
    called_path, called_mask = mock_modulate.call_args.args
    assert called_mask == "ethereal"
    assert called_path.name.startswith("auri_mask_")


@pytest.mark.asyncio
async def test_mask_voice_defaults_to_warm_mask(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    masked = tmp_path / "masked_default.wav"
    masked.write_bytes(b"RIFF")
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate", return_value=str(masked)
    ) as mock_modulate:
        response = await client.post(
            "/api/v1/voice/mask", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 200
    assert mock_modulate.call_args.args[1] == "warm"


@pytest.mark.asyncio
async def test_mask_voice_deletes_upload_and_output_after_response(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: neither the raw upload nor the masked output
    # should linger on disk, per the plan's Data Privacy Design
    masked = tmp_path / "masked_cleanup.wav"
    masked.write_bytes(b"RIFF")
    headers = {"X-Device-Token-Hash": DEVICE_HASH}
    captured_path = {}

    def fake_modulate(self, audio_path, mask):
        captured_path["path"] = audio_path
        assert audio_path.exists()
        return str(masked)

    # Act
    with patch("app.api.v1.voice.VoiceModulator.modulate", fake_modulate):
        response = await client.post(
            "/api/v1/voice/mask", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 200
    assert not captured_path["path"].exists()
    assert not masked.exists()


@pytest.mark.asyncio
async def test_mask_voice_rejects_empty_file(client: AsyncClient) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    response = await client.post(
        "/api/v1/voice/mask",
        files={"audio": ("empty.aac", b"", "audio/aac")},
        headers=headers,
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mask_voice_rejects_oversized_file(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — regression: an unbounded upload must not reach SoX at all
    monkeypatch.setattr("app.api.v1.voice.settings.STT_MAX_UPLOAD_BYTES", 10)
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    response = await client.post(
        "/api/v1/voice/mask", files=_audio_file(), headers=headers
    )

    # Assert
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_mask_voice_rejects_missing_device_token_header(
    client: AsyncClient,
) -> None:
    # Act
    response = await client.post("/api/v1/voice/mask", files=_audio_file())

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mask_voice_returns_500_when_service_fails(client: AsyncClient) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate",
        side_effect=RuntimeError("SoX exploded"),
    ):
        response = await client.post(
            "/api/v1/voice/mask", files=_audio_file(), headers=headers
        )

    # Assert
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_mask_voice_second_call_same_token_is_rate_limited(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: unauthenticated masking must not allow unbounded
    # cost-abuse (SoX/ffmpeg CPU work) from a single device
    masked = tmp_path / "masked_rl.wav"
    masked.write_bytes(b"RIFF")
    headers = {"X-Device-Token-Hash": DEVICE_HASH}

    # Act
    with patch("app.api.v1.voice.VoiceModulator.modulate", return_value=str(masked)):
        first_response = await client.post(
            "/api/v1/voice/mask", files=_audio_file(), headers=headers
        )
        second_response = await client.post(
            "/api/v1/voice/mask", files=_audio_file(), headers=headers
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 429


@pytest.mark.asyncio
async def test_mask_voice_different_token_not_rate_limited(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — regression: one device's rate-limit window must not
    # cross-contaminate another device's requests
    masked_one = tmp_path / "masked_one.wav"
    masked_one.write_bytes(b"RIFF")
    masked_two = tmp_path / "masked_two.wav"
    masked_two.write_bytes(b"RIFF")

    # Act
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate",
        side_effect=[str(masked_one), str(masked_two)],
    ):
        first_response = await client.post(
            "/api/v1/voice/mask",
            files=_audio_file(),
            headers={"X-Device-Token-Hash": DEVICE_HASH},
        )
        second_response = await client.post(
            "/api/v1/voice/mask",
            files=_audio_file(),
            headers={"X-Device-Token-Hash": OTHER_DEVICE_HASH},
        )

    # Assert
    assert first_response.status_code == 200
    assert second_response.status_code == 200


@pytest.mark.asyncio
async def test_a_mask_does_not_block_other_requests(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — the first mask can only finish once the second has run; if SoX ran
    # on the event loop the second could never start (plan task 16.8)
    second_has_run = threading.Event()

    def fake_modulate(self: object, audio_path: Path, mask: str) -> str:
        first = Path(audio_path).read_bytes().startswith(b"first")
        out = tmp_path / ("first.wav" if first else "second.wav")
        if first and not second_has_run.wait(timeout=3):
            raise RuntimeError("blocked")
        second_has_run.set()
        out.write_bytes(b"masked " + (b"first" if first else b"second"))
        return str(out)

    with patch("app.api.v1.voice.VoiceModulator.modulate", fake_modulate):
        # Act
        first, second = await asyncio.gather(
            client.post(
                "/api/v1/voice/mask",
                files={"audio": ("a.aac", b"first recording", "audio/aac")},
                headers={"X-Device-Token-Hash": DEVICE_HASH},
            ),
            client.post(
                "/api/v1/voice/mask",
                files={"audio": ("b.aac", b"second recording", "audio/aac")},
                headers={"X-Device-Token-Hash": OTHER_DEVICE_HASH},
            ),
        )

    # Assert
    assert first.status_code == 200
    assert base64.b64decode(first.json()["audio_base64"]) == b"masked first"
    assert base64.b64decode(second.json()["audio_base64"]) == b"masked second"
