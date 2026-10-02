"""Voice masking endpoint — applies a voice mask to a recorded confession.

Closes a real gap found 2026-08-13: `VoiceModulator` (SoX pitch/formant
shifting) existed as a service since Phase 2 but was never exposed over
HTTP or called from anywhere. The mobile Review screen's "Play anonymized
recording" button only ever replayed the local, unmasked recording — no
actual voice masking happened anywhere in the pipeline.
"""

from __future__ import annotations

import asyncio
import base64
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
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import settings
from app.exceptions import RateLimitError, VoiceModulationError
from app.services.audio_probe import enforce_recording_limit
from app.services.masked_audio import MaskedAudioStore, MaskedStoreFull
from app.services.voice_mod import VoiceModulator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

ClockDependency = Callable[[], datetime]

# In-process rate-limit store keyed by device token hash — same pattern as
# POST /api/v1/stt and POST /api/v1/tts (no DB row backs this endpoint).
_last_mask_at: dict[str, datetime] = {}

# ffmpeg and SoX are blocking subprocesses: they run on their own small pool so a
# long recording's mask never holds the event loop (plan task 16.8).
_MASK_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-mask")

# One-time downloads of masked files (plan 16.4); one store per process.
MASKED_DOWNLOAD_TTL_SECONDS = 120
masked_downloads = MaskedAudioStore(ttl=timedelta(seconds=MASKED_DOWNLOAD_TTL_SECONDS))


class VoiceMaskResponse(BaseModel):
    """Response body for a successful voice-mask call.

    The masked audio comes back as base64 JSON rather than a streamed file
    response — React Native's `fetch().blob()` handling is unreliable on
    this stack (new-architecture/Fabric), so this trades a slightly larger
    payload for a response shape the mobile client can consume without
    Blob/FileReader gymnastics.
    """

    audio_base64: str


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function.

    Tests can override this dependency (``app.dependency_overrides``) to
    freeze time per AGENTS.md §16.5, instead of calling ``datetime.now()``
    directly inside the route handler.
    """
    return lambda: datetime.now(timezone.utc)


def _check_voice_mask_rate_limit(device_token_hash: str, now: datetime) -> None:
    """Raise ``RateLimitError`` if *device_token_hash* masked audio too recently.

    Args:
        device_token_hash: Value of the ``X-Device-Token-Hash`` request header.
        now: Current time, from the injected clock.

    Raises:
        RateLimitError: If the device's last mask call is inside the
            configured window (cost-abuse guard, no auth required).
    """
    window = timedelta(seconds=settings.VOICE_MASK_RATE_LIMIT_SECONDS)
    last_call = _last_mask_at.get(device_token_hash)
    if last_call is not None and now - last_call < window:
        retry_after = (window - (now - last_call)).seconds
        raise RateLimitError(f"rate limit exceeded; retry in {retry_after}s")
    _last_mask_at[device_token_hash] = now


class MaskDownloadResponse(BaseModel):
    """``delivery=download``: fetch the masked WAV once from ``/voice/masked/{id}``."""

    download_id: str
    expires_in_seconds: int


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


async def _masked_file(body: bytes, filename: str | None, mask: str) -> Path:
    """Mask the upload into a new temp file; the upload's own temp file is deleted."""
    suffix = Path(filename or "").suffix or ".m4a"
    fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, prefix="auri_mask_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(body)
        await enforce_recording_limit(tmp_path)
        modulator = VoiceModulator()
        try:
            masked_path = await asyncio.get_running_loop().run_in_executor(
                _MASK_POOL, modulator.modulate, tmp_path, mask
            )
        except Exception as exc:
            logger.error("voice masking failed: %s", exc)
            raise VoiceModulationError("could not apply voice mask") from exc
        return Path(masked_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _hold_for_download(
    device: str, masked: Path, now: datetime
) -> MaskDownloadResponse:
    """Hand the masked file to the download store, or 503 if it is full."""
    try:
        download_id = masked_downloads.hold(device, masked, now)
    except MaskedStoreFull:
        masked.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many masked recordings are waiting; try again shortly",
            headers={"Retry-After": "30"},
        ) from None
    return MaskDownloadResponse(
        download_id=download_id, expires_in_seconds=MASKED_DOWNLOAD_TTL_SECONDS
    )


@router.post(
    "/mask",
    response_model=VoiceMaskResponse | MaskDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply a voice mask to a recorded confession",
)
async def mask_voice(
    audio: UploadFile,
    mask: str = Form("warm"),
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    delivery: Literal["base64", "download"] = Query(
        "base64",
        description="'download' returns an id to fetch the WAV once (plan 16.4)",
    ),
    clock: ClockDependency = Depends(get_clock),
) -> VoiceMaskResponse | MaskDownloadResponse:
    """Apply *mask* to an uploaded recording.

    By default the masked WAV comes back as base64 JSON (current app builds). With
    ``delivery=download`` it is held for a single fetch from
    ``/voice/masked/{id}`` for at most two minutes. Either way the upload's temp
    file is deleted straight after masking; per the Data Privacy Design no audio
    is kept beyond the hand-over.
    """
    _check_voice_mask_rate_limit(x_device_token_hash, clock())
    masked = await _masked_file(await _read_upload(audio), audio.filename, mask)
    if delivery == "download":
        return _hold_for_download(x_device_token_hash, masked, clock())
    try:
        masked_bytes = masked.read_bytes()
    finally:
        masked.unlink(missing_ok=True)
    return VoiceMaskResponse(
        audio_base64=base64.b64encode(masked_bytes).decode("ascii")
    )


@router.get(
    "/masked/{download_id}",
    response_class=FileResponse,
    summary="Fetch a masked recording once",
)
async def fetch_masked(
    download_id: str,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    clock: ClockDependency = Depends(get_clock),
) -> FileResponse:
    """Stream the masked WAV and delete it once sent.

    An unknown, expired or another device's id is the same 404.
    """
    path = masked_downloads.take(download_id, x_device_token_hash, clock())
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return FileResponse(
        path,
        media_type="audio/wav",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
