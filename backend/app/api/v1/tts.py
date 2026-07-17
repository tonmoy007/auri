"""Text-to-speech endpoint — synthesises the AI agent's spoken responses."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.config import settings
from app.exceptions import RateLimitError, TTSError
from app.services.tts import EdgeTTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

ClockDependency = Callable[[], datetime]

# In-process rate-limit store keyed by device token hash. TTS calls aren't
# tied to a DB row (unlike confessions), so a lightweight module-level dict
# is sufficient here; a multi-instance deployment should swap this for a
# shared cache such as Redis, keyed the same way.
_last_synthesis_at: dict[str, datetime] = {}


class TTSRequest(BaseModel):
    """Request body for synthesising an agent voice response."""

    text: str = Field(
        ..., min_length=1, max_length=1000, description="Text for the agent to speak"
    )
    voice: str = Field(
        "en-US-JennyNeural",
        pattern=r"^[A-Za-z]{2}-[A-Za-z]{2}-[A-Za-z]+Neural$",
        description="Edge TTS voice name, e.g. 'en-US-JennyNeural'",
    )


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function.

    Tests can override this dependency (``app.dependency_overrides``) to
    freeze time per AGENTS.md §16.5, instead of calling ``datetime.now()``
    directly inside route handlers.
    """
    return lambda: datetime.now(timezone.utc)


def _check_tts_rate_limit(device_token_hash: str, now: datetime) -> None:
    """Raise ``RateLimitError`` if *device_token_hash* called TTS too recently.

    Args:
        device_token_hash: Value of the ``X-Device-Token-Hash`` request header.
        now: Current time, from the injected clock.

    Raises:
        RateLimitError: If the device's last TTS call is inside the
            configured window (cost-abuse guard, no auth required).
    """
    window = timedelta(seconds=settings.TTS_RATE_LIMIT_SECONDS)
    last_call = _last_synthesis_at.get(device_token_hash)
    if last_call is not None and now - last_call < window:
        retry_after = (window - (now - last_call)).seconds
        raise RateLimitError(f"rate limit exceeded; retry in {retry_after}s")
    _last_synthesis_at[device_token_hash] = now


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Synthesise speech for the AI agent's spoken response",
    response_class=FileResponse,
)
async def synthesize_speech(
    body: TTSRequest,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    clock: ClockDependency = Depends(get_clock),
) -> FileResponse:
    """Generate a WAV audio clip of *body.text* spoken in *body.voice*.

    Requires the ``X-Device-Token-Hash`` header, used as the rate-limit key
    (no confession/user row backs this endpoint). The file is streamed back
    to the caller and deleted from disk once the response has been sent —
    nothing is retained server-side.
    """
    _check_tts_rate_limit(x_device_token_hash, clock())

    service = EdgeTTS()
    try:
        audio_path = await service.synthesize(body.text, body.voice)
    except Exception as exc:
        logger.error("tts synthesis failed: %s", exc)
        raise TTSError("could not synthesize speech") from exc

    return FileResponse(
        audio_path,
        media_type="audio/wav",
        filename="agent_response.wav",
        background=BackgroundTask(os.remove, audio_path),
    )
