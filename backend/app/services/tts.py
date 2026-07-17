"""Text-to-speech service using Microsoft Edge TTS."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class EdgeTTS:
    """Synthesise speech from text using the ``edge-tts`` library.

    Audio files are written to the configured output directory as 16-bit
    PCM WAV files.
    """

    def __init__(self, output_dir: str | Path | None = None) -> None:
        """Initialise the TTS service.

        Args:
            output_dir: Directory to write generated audio files.
                Defaults to ``{cwd}/data/tts_output/``.
        """
        self._output_dir: Path = Path(output_dir) if output_dir else (
            Path.cwd() / "data" / "tts_output"
        )
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def synthesize(self, text: str, voice: str = "en-US-JennyNeural") -> str:
        """Generate speech audio for *text* using *voice*.

        Must be called from within a running event loop (``await``ed
        directly) — it no longer spawns its own loop via ``asyncio.run()``,
        which would crash if one is already running (e.g. inside FastAPI).

        Args:
            text: The text to be spoken.
            voice: Edge TTS voice name (e.g. ``"en-US-JennyNeural"``,
                ``"en-GB-SoniaNeural"``).  See ``edge-tts --list-voices``.

        Returns:
            Absolute path to the generated WAV file.
        """
        output_path = self._output_dir / f"tts_{uuid.uuid4().hex}.wav"
        try:
            await self._run_tts(text, voice, output_path)
            logger.info("TTS audio written to %s", output_path)
        except Exception as exc:
            logger.error("TTS synthesis failed: %s", exc)
            raise
        return str(output_path.resolve())

    @staticmethod
    async def _run_tts(text: str, voice: str, output_path: Path) -> None:
        """Execute the edge-tts CLI communicator in an async context."""
        import edge_tts  # type: ignore[import-untyped]

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
