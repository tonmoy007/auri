"""Background transcription jobs (plan task 16.1).

A long recording used to hold the upload request open for as long as Whisper took
(minutes). In job mode the upload returns at once with a job id and the app polls.
A job is visible only to the device that made it, is deleted on its first
successful read, and expires if nobody reads it.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.api.v1.stt import _last_transcription_at, get_clock, stt_jobs
from app.main import app
from app.services.stt_jobs import JobStore
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DEVICE = "a" * 32
OTHER = "b" * 32
AUDIO = {"audio": ("c.m4a", b"a recording", "audio/m4a")}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    _last_transcription_at.clear()
    stt_jobs.clear()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    _last_transcription_at.clear()
    stt_jobs.clear()


async def _submit(client: AsyncClient, device: str = DEVICE) -> str:
    response = await client.post(
        "/api/v1/stt",
        params={"mode": "job"},
        files=AUDIO,
        headers={"X-Device-Token-Hash": device},
    )
    assert response.status_code == 202
    return response.json()["job_id"]


async def _poll(client: AsyncClient, job_id: str, device: str = DEVICE):
    return await client.get(
        f"/api/v1/stt/jobs/{job_id}", headers={"X-Device-Token-Hash": device}
    )


async def _until_done(client: AsyncClient, job_id: str):
    for _ in range(200):
        response = await _poll(client, job_id)
        if response.status_code != 200 or response.json()["status"] != "pending":
            return response
        await stt_jobs.wait_idle(0.01)
    raise AssertionError("job never finished")


@pytest.mark.asyncio
async def test_a_job_returns_at_once_and_its_transcript_is_read_once(
    client: AsyncClient,
) -> None:
    # Arrange
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="hello"):
        job_id = await _submit(client)

        # Act
        done = await _until_done(client, job_id)
        again = await _poll(client, job_id)

    # Assert — the transcript is handed over once, then the job is gone
    assert done.json() == {"status": "ready", "transcript": "hello", "detail": None}
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_a_job_is_pending_while_whisper_works(client: AsyncClient) -> None:
    # Arrange
    release = threading.Event()

    def slow(self: object, path: str | Path) -> str:
        release.wait(timeout=3)
        return "late"

    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", slow):
        job_id = await _submit(client)

        # Act
        pending = await _poll(client, job_id)
        release.set()
        done = await _until_done(client, job_id)

    # Assert
    assert pending.json()["status"] == "pending"
    assert done.json()["transcript"] == "late"


@pytest.mark.asyncio
async def test_another_device_cannot_read_a_job(client: AsyncClient) -> None:
    # Arrange
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", return_value="mine"):
        job_id = await _submit(client)

        # Act
        theirs = await _poll(client, job_id, device=OTHER)
        mine = await _until_done(client, job_id)

    # Assert — a wrong device looks exactly like an unknown job
    assert theirs.status_code == 404
    assert mine.json()["transcript"] == "mine"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "detail"),
    [(RuntimeError("model missing"), "transcription_failed"), ("   ", "no_text")],
    ids=["error", "silence"],
)
async def test_a_failed_job_says_why_without_details(
    client: AsyncClient, outcome: object, detail: str
) -> None:
    # Arrange
    kwargs = (
        {"side_effect": outcome}
        if isinstance(outcome, Exception)
        else {"return_value": outcome}
    )
    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", **kwargs):
        job_id = await _submit(client)

        # Act
        done = await _until_done(client, job_id)

    # Assert
    assert done.json() == {"status": "failed", "transcript": None, "detail": detail}


@pytest.mark.asyncio
async def test_the_recording_is_deleted_when_the_job_ends(client: AsyncClient) -> None:
    # Arrange
    seen: list[Path] = []

    def remember(self: object, path: str | Path) -> str:
        seen.append(Path(path))
        return "done"

    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", remember):
        job_id = await _submit(client)

        # Act
        await _until_done(client, job_id)

    # Assert — audio is never retained server-side
    assert len(seen) == 1
    assert not seen[0].exists()


@pytest.mark.asyncio
async def test_a_full_queue_answers_busy(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — room for one waiting job; a second device's upload finds it full
    release = threading.Event()
    monkeypatch.setattr(stt_jobs, "max_open", 1)

    def slow(self: object, path: str | Path) -> str:
        release.wait(timeout=3)
        return "x"

    with patch("app.api.v1.stt.WhisperTranscriber.transcribe", slow):
        await _submit(client)

        # Act
        response = await client.post(
            "/api/v1/stt",
            params={"mode": "job"},
            files=AUDIO,
            headers={"X-Device-Token-Hash": OTHER},
        )
        release.set()
        await stt_jobs.wait_idle(1)

    # Assert
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_an_unread_job_expires() -> None:
    # Arrange
    store = JobStore(max_open=4, ttl=timedelta(minutes=10))
    job_id = store.create("d" * 32, NOW)
    store.finish(job_id, "hello", NOW)

    # Act
    later = store.take(job_id, "d" * 32, NOW + timedelta(minutes=11))

    # Assert
    assert later is None


def test_the_default_mode_still_answers_in_one_request() -> None:
    # Arrange — current app builds post without mode=job and expect the transcript
    from app.api.v1.stt import TranscriptionResponse

    # Act
    fields = set(TranscriptionResponse.model_fields)

    # Assert
    assert fields == {"transcript"}
