"""Masked audio as a one-time binary download (plan task 16.4).

Masking a five-minute recording used to return about 33.6 MB of base64 JSON,
which the app held in memory before writing it out. With delivery=download the
upload returns a short-lived id instead, and the app streams the WAV straight to
a file. The file is visible only to the device that made it, can be fetched once,
and is deleted on that fetch or when it expires.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.api.v1.voice import _last_mask_at, get_clock, masked_downloads
from app.main import app
from app.services.masked_audio import MaskedAudioStore
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DEVICE = "a" * 32
OTHER = "b" * 32
AUDIO = {"audio": ("c.aac", b"a recording", "audio/aac")}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    _last_mask_at.clear()
    masked_downloads.clear()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    _last_mask_at.clear()
    masked_downloads.clear()


def _masked(tmp_path: Path, payload: bytes = b"RIFF masked wav") -> str:
    out = tmp_path / "masked.wav"
    out.write_bytes(payload)
    return str(out)


async def _upload(client: AsyncClient, tmp_path: Path) -> str:
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate", return_value=_masked(tmp_path)
    ):
        response = await client.post(
            "/api/v1/voice/mask",
            params={"delivery": "download"},
            files=AUDIO,
            data={"mask": "warm"},
            headers={"X-Device-Token-Hash": DEVICE},
        )
    assert response.status_code == 200
    return response.json()["download_id"]


@pytest.mark.asyncio
async def test_the_masked_audio_is_downloaded_once_as_a_wav(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    download_id = await _upload(client, tmp_path)

    # Act
    first = await client.get(
        f"/api/v1/voice/masked/{download_id}", headers={"X-Device-Token-Hash": DEVICE}
    )
    second = await client.get(
        f"/api/v1/voice/masked/{download_id}", headers={"X-Device-Token-Hash": DEVICE}
    )

    # Assert — raw audio, not base64; and gone after the first fetch
    assert first.status_code == 200
    assert first.headers["content-type"] == "audio/wav"
    assert first.content == b"RIFF masked wav"
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_the_held_file_is_deleted_once_fetched(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    download_id = await _upload(client, tmp_path)
    held = masked_downloads.held_path(download_id)

    # Act
    await client.get(
        f"/api/v1/voice/masked/{download_id}", headers={"X-Device-Token-Hash": DEVICE}
    )

    # Assert — audio is never kept server-side beyond the hand-over
    assert held is not None
    assert not held.exists()


@pytest.mark.asyncio
async def test_another_device_cannot_fetch_the_masked_audio(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange
    download_id = await _upload(client, tmp_path)

    # Act
    theirs = await client.get(
        f"/api/v1/voice/masked/{download_id}", headers={"X-Device-Token-Hash": OTHER}
    )
    mine = await client.get(
        f"/api/v1/voice/masked/{download_id}", headers={"X-Device-Token-Hash": DEVICE}
    )

    # Assert — the wrong device gets the same 404 as an unknown id, and does not
    # use up the owner's one fetch
    assert theirs.status_code == 404
    assert mine.status_code == 200


def test_an_unfetched_masked_file_expires_and_is_deleted(tmp_path: Path) -> None:
    # Arrange
    store = MaskedAudioStore(max_held=4, ttl=timedelta(minutes=2))
    source = tmp_path / "m.wav"
    source.write_bytes(b"x")
    download_id = store.hold(DEVICE, source, NOW)
    held = store.held_path(download_id)

    # Act
    later = store.take(download_id, DEVICE, NOW + timedelta(minutes=3))

    # Assert
    assert later is None
    assert held is not None and not held.exists()


@pytest.mark.asyncio
async def test_the_default_delivery_is_still_base64_json(
    client: AsyncClient, tmp_path: Path
) -> None:
    # Arrange — current app builds expect audio_base64 in the response
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate", return_value=_masked(tmp_path)
    ):
        # Act
        response = await client.post(
            "/api/v1/voice/mask",
            files=AUDIO,
            data={"mask": "warm"},
            headers={"X-Device-Token-Hash": DEVICE},
        )

    # Assert
    assert set(response.json()) == {"audio_base64"}


@pytest.mark.asyncio
async def test_a_full_store_answers_busy(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr(masked_downloads, "max_held", 1)
    await _upload(client, tmp_path)
    _last_mask_at.clear()

    # Act
    with patch(
        "app.api.v1.voice.VoiceModulator.modulate", return_value=_masked(tmp_path, b"2")
    ):
        response = await client.post(
            "/api/v1/voice/mask",
            params={"delivery": "download"},
            files=AUDIO,
            data={"mask": "warm"},
            headers={"X-Device-Token-Hash": OTHER},
        )

    # Assert
    assert response.status_code == 503
