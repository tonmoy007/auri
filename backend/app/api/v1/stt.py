"""Speech-to-text endpoint — the actual audio-to-transcript entry point.

Nothing else in this codebase exposed WhisperTranscriber over HTTP before
this file existed (found 2026-07-20 while reviewing the plan): the mobile
app has no way to turn a recording into the `transcript` string that
`POST /api/v1/confessions` requires. This closes that gap.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.config import settings
from app.exceptions import RateLimitError, STTError
from app.services.audio_probe import enforce_recording_limit
from app.services.stt import WhisperTranscriber
from app.services.stt_jobs import JobStore, JobStoreFull

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stt", tags=["stt"])

ClockDependency = Callable[[], datetime]

# In-process rate-limit store keyed by device token hash — same pattern as
# POST /api/v1/tts (backend/app/api/v1/tts.py): STT has no DB row to hang a
# limit off of, so a lightweight module-level dict is sufficient for a
# single-instance deployment; swap for a shared cache (Redis) if scaled out.
_last_transcription_at: dict[str, datetime] = {}

# Whisper is blocking and CPU-heavy: it runs on its own small pool so that a
# minutes-long transcription never holds the event loop, which used to stall
# every other request in the process until it finished (plan task 16.8).
_TRANSCRIPTION_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")

# Background jobs (plan 16.1): one store per process, like the rate-limit dict.
stt_jobs = JobStore()
JOB_RETRY_AFTER_SECONDS = 30


class TranscriptionResponse(BaseModel):
    """Response body for a successful transcription."""

    transcript: str


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function.

    Tests can override this dependency (``app.dependency_overrides``) to
    freeze time per AGENTS.md §16.5, instead of calling ``datetime.now()``
    directly inside the route handler.
    """
    return lambda: datetime.now(timezone.utc)


def _check_stt_rate_limit(device_token_hash: str, now: datetime) -> None:
    """Raise ``RateLimitError`` if *device_token_hash* transcribed too recently.

    Args:
        device_token_hash: Value of the ``X-Device-Token-Hash`` request header.
        now: Current time, from the injected clock.

    Raises:
        RateLimitError: If the device's last STT call is inside the
            configured window (cost-abuse guard, no auth required).
    """
    window = timedelta(seconds=settings.STT_RATE_LIMIT_SECONDS)
    last_call = _last_transcription_at.get(device_token_hash)
    if last_call is not None and now - last_call < window:
        retry_after = (window - (now - last_call)).seconds
        raise RateLimitError(f"rate limit exceeded; retry in {retry_after}s")
    _last_transcription_at[device_token_hash] = now


class JobAcceptedResponse(BaseModel):
    """Job mode: the upload was accepted; poll ``/stt/jobs/{job_id}``."""

    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    """A job's state; ``transcript`` on ready, a fixed ``detail`` code on failure."""

    status: str
    transcript: str | None
    detail: str | None


async def _read_upload(audio: UploadFile) -> bytes:
    """The uploaded bytes, or a 422/413 for an empty or oversized file."""
    body = await audio.read()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded audio file is empty",
        )
    if len(body) > settings.STT_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Audio file exceeds the {settings.STT_MAX_UPLOAD_BYTES} byte limit",
        )
    return body


def _write_temp(body: bytes, filename: str | None) -> Path:
    """Write the upload to a private temp file for the transcriber."""
    suffix = Path(filename or "").suffix or ".m4a"
    fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, prefix="auri_stt_")
    with os.fdopen(fd, "wb") as tmp_file:
        tmp_file.write(body)
    return Path(tmp_path_str)


async def _transcribe(path: Path, local_only: bool) -> str:
    """Run Whisper on the transcription pool (never on the event loop)."""
    transcriber = WhisperTranscriber(allow_api_fallback=not local_only)
    return await asyncio.get_running_loop().run_in_executor(
        _TRANSCRIPTION_POOL, transcriber.transcribe, path
    )


async def _run_job(
    job_id: str, path: Path, local_only: bool, clock: ClockDependency
) -> None:
    """Transcribe in the background, record the outcome, delete the audio."""
    try:
        transcript = await _transcribe(path, local_only)
    except Exception as exc:  # noqa: BLE001 — any failure ends the job as failed; the class name is logged, never the message, which could carry audio-derived text
        logger.error("background transcription failed (%s)", type(exc).__name__)
        stt_jobs.fail(job_id, "transcription_failed", clock())
        return
    finally:
        path.unlink(missing_ok=True)
    if transcript.strip():
        stt_jobs.finish(job_id, transcript, clock())
    else:
        stt_jobs.fail(job_id, "no_text", clock())


def _start_job(
    device: str, path: Path, local_only: bool, clock: ClockDependency
) -> str:
    """Open a job and start it; a 503 with Retry-After when too many are waiting."""
    try:
        job_id = stt_jobs.create(device, clock())
    except JobStoreFull:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many recordings are being transcribed; try again shortly",
            headers={"Retry-After": str(JOB_RETRY_AFTER_SECONDS)},
        ) from None
    task = asyncio.create_task(_run_job(job_id, path, local_only, clock))
    stt_jobs.track(task)
    return job_id


@router.post(
    "",
    response_model=TranscriptionResponse | JobAcceptedResponse,
    status_code=status.HTTP_200_OK,
    summary="Transcribe an audio recording to text",
)
async def transcribe_audio(
    audio: UploadFile,
    response: Response,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    local_only: bool = Query(
        False,
        description="Never retry through a hosted provider (used for Guide questions)",
    ),
    mode: Literal["sync", "job"] = Query(
        "sync",
        description="'job' returns at once with a job id to poll (plan 16.1)",
    ),
    clock: ClockDependency = Depends(get_clock),
) -> TranscriptionResponse | JobAcceptedResponse:
    """Transcribe an uploaded audio file with Whisper.

    In the default mode the transcript comes back in this response. In job mode
    the response is a 202 with a job id and the app polls ``/stt/jobs/{id}``.
    The audio is written to a temp path only while Whisper runs and deleted
    straight after; per the Data Privacy Design it is never retained. If the
    local step fails and an OpenAI key is configured, that provider receives the
    audio instead, unless ``local_only`` is set.
    """
    _check_stt_rate_limit(x_device_token_hash, clock())
    tmp_path = _write_temp(await _read_upload(audio), audio.filename)
    try:
        await enforce_recording_limit(tmp_path)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    if mode == "job":
        job_id = _start_job(x_device_token_hash, tmp_path, local_only, clock)
        response.status_code = status.HTTP_202_ACCEPTED
        return JobAcceptedResponse(job_id=job_id, status="pending")

    try:
        transcript = await _transcribe(tmp_path, local_only)
    except Exception as exc:
        logger.error("audio transcription failed: %s", exc)
        raise STTError("could not transcribe audio") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Transcription produced no text — audio may be silent or unintelligible",
        )
    return TranscriptionResponse(transcript=transcript)


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll a background transcription job",
)
async def read_job(
    job_id: str,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    clock: ClockDependency = Depends(get_clock),
) -> JobStatusResponse:
    """A job's state. A finished job is handed over once, then deleted.

    An unknown, expired or another device's job is the same 404, so a wrong
    device cannot learn that a job exists.
    """
    job = stt_jobs.take(job_id, x_device_token_hash, clock())
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    return JobStatusResponse(
        status=job.status, transcript=job.transcript, detail=job.detail
    )
