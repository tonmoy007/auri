"""Text-to-speech endpoint — synthesises the AI agent's spoken responses."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.exceptions import TTSError
from app.services.tts import EdgeTTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])


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


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Synthesise speech for the AI agent's spoken response",
    response_class=FileResponse,
)
async def synthesize_speech(body: TTSRequest) -> FileResponse:
    """Generate a WAV audio clip of *body.text* spoken in *body.voice*.

    The file is streamed back to the caller and deleted from disk once the
    response has been sent — nothing is retained server-side.
    """
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
