"""Voice masking endpoint — applies a voice mask to a recorded confession.

Closes a real gap found 2026-08-13: `VoiceModulator` (SoX pitch/formant
shifting) existed as a service since Phase 2 but was never exposed over
HTTP or called from anywhere. The mobile Review screen's "Play anonymized
recording" button only ever replayed the local, unmasked recording — no
actual voice masking happened anywhere in the pipeline.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import settings
from app.exceptions import RateLimitError, VoiceModulationError
from app.services.voice_mod import VoiceModulator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

ClockDependency = Callable[[], datetime]

# In-process rate-limit store keyed by device token hash — same pattern as
# POST /api/v1/stt and POST /api/v1/tts (no DB row backs this endpoint).
_last_mask_at: dict[str, datetime] = {}


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


@router.post(
    "/mask",
    response_model=VoiceMaskResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply a voice mask to a recorded confession",
)
async def mask_voice(
    audio: UploadFile,
    mask: str = Form("warm"),
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    clock: ClockDependency = Depends(get_clock),
) -> VoiceMaskResponse:
    """Apply *mask* to an uploaded recording and return the masked WAV as base64.

    The uploaded file and the masked output are both written to temp paths
    only for the duration of the SoX call and deleted immediately after —
    per the Data Privacy Design in the project plan, audio is never
    retained server-side.
    """
    _check_voice_mask_rate_limit(x_device_token_hash, clock())

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
    fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, prefix="auri_mask_")
    tmp_path = Path(tmp_path_str)
    masked_path: str | None = None
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(body)

        modulator = VoiceModulator()
        try:
            masked_path = modulator.modulate(tmp_path, mask)
        except Exception as exc:
            logger.error("voice masking failed: %s", exc)
            raise VoiceModulationError("could not apply voice mask") from exc

        masked_bytes = Path(masked_path).read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
        if masked_path is not None:
            Path(masked_path).unlink(missing_ok=True)

    return VoiceMaskResponse(
        audio_base64=base64.b64encode(masked_bytes).decode("ascii")
    )
