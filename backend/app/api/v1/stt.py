"""Speech-to-text endpoint — the actual audio-to-transcript entry point.

Nothing else in this codebase exposed WhisperTranscriber over HTTP before
this file existed (found 2026-07-20 while reviewing the plan): the mobile
app has no way to turn a recording into the `transcript` string that
`POST /api/v1/confessions` requires. This closes that gap.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import settings
from app.exceptions import RateLimitError, STTError
from app.services.stt import WhisperTranscriber

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stt", tags=["stt"])

ClockDependency = Callable[[], datetime]

# In-process rate-limit store keyed by device token hash — same pattern as
# POST /api/v1/tts (backend/app/api/v1/tts.py): STT has no DB row to hang a
# limit off of, so a lightweight module-level dict is sufficient for a
# single-instance deployment; swap for a shared cache (Redis) if scaled out.
_last_transcription_at: dict[str, datetime] = {}


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


@router.post(
    "",
    response_model=TranscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Transcribe an audio recording to text",
)
async def transcribe_audio(
    audio: UploadFile,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    clock: ClockDependency = Depends(get_clock),
) -> TranscriptionResponse:
    """Transcribe an uploaded audio file with Whisper and return the text.

    The uploaded file is written to a temp path only for the duration of
    the Whisper call and deleted immediately after — per the Data Privacy
    Design in the project plan, audio is never retained server-side.
    """
    _check_stt_rate_limit(x_device_token_hash, clock())

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

    suffix = Path(audio.filename or "").suffix or ".m4a"
    fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, prefix="auri_stt_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(body)

        transcriber = WhisperTranscriber()
        try:
            transcript = transcriber.transcribe(tmp_path)
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
