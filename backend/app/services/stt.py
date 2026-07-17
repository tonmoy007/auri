"""Speech-to-text service using faster-whisper with OpenAI API fallback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings
from app.exceptions import STTError

if TYPE_CHECKING:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """Transcribe audio files to text using ``faster-whisper`` locally.

    If the local model fails to load or returns an empty result the class
    falls back to the OpenAI Whisper API (requires ``OPENAI_API_KEY``).
    """

    def __init__(self, model_name: str | None = None) -> None:
        """Initialise the transcriber.

        Args:
            model_name: Path or size tag for the faster-whisper model
                (defaults to ``settings.WHISPER_MODEL``).
        """
        self._model_name: str = model_name or settings.WHISPER_MODEL
        self._model: WhisperModel | None = None  # Lazy-loaded on first call.

    def _load_model(self) -> None:
        """Import and cache the faster-whisper model instance."""
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed; run: pip install faster-whisper"
            ) from exc

        # Use CPU by default; GPU can be enabled via config in production.
        self._model = WhisperModel(self._model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio_path: str | Path) -> str:
        """Run Whisper transcription on an audio file.

        Args:
            audio_path: Path to the audio file (WAV, MP3, etc.).

        Returns:
            The transcribed text, or an empty string if transcription failed.

        Raises:
            STTError: If the local Whisper model cannot be loaded.
        """
        if self._model is None:
            self._load_model()

        model = self._model
        if model is None:
            logger.error("Whisper model failed to load for %s", audio_path)
            raise STTError("Whisper model is not available")

        audio_path = Path(audio_path)
        if not audio_path.exists():
            logger.error("Audio file not found: %s", audio_path)
            return ""

        try:
            segments, _ = model.transcribe(str(audio_path))
            text = " ".join(segment.text for segment in segments).strip()
            if not text:
                logger.warning("Local Whisper returned empty transcript; trying API fallback.")
                return self._api_fallback(audio_path)
            return text
        except Exception as exc:
            logger.warning("Local Whisper failed (%s); trying API fallback.", exc)
            return self._api_fallback(audio_path)

    def _api_fallback(self, audio_path: Path) -> str:
        """Transcribe via the OpenAI Whisper API as a fallback.

        Requires ``OPENAI_API_KEY`` to be set in the environment.
        """
        api_key = settings.LLM_API_KEY
        if not api_key:
            logger.error("OPENAI_API_KEY is not set — cannot use API fallback.")
            return ""

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            with audio_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
            return response.text.strip()
        except Exception as exc:
            logger.error("OpenAI Whisper API fallback also failed: %s", exc)
            return ""
