"""Voice modulation service using SoX for pitch/formant shifting."""

from __future__ import annotations

import logging
import random
import subprocess
import uuid
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Mask = Literal["warm", "robotic", "ethereal", "deep", "random"]

# Pre-defined SoX effect chains for each voice mask.
MASKS: dict[str, list[str]] = {
    "warm": ["pitch", "-300", "overdrive", "5"],
    "robotic": ["chorus", "0.5", "0.9", "50", "0.5", "0.25", "2", "-t", "vocoder"],
    "ethereal": ["reverb", "80", "50", "80", "100", "5", "pitch", "+600"],
    "deep": ["pitch", "-800", "bass", "+10"],
}

OUTPUT_DIR = Path.cwd() / "data" / "modulated"


class VoiceModulator:
    """Apply voice-mask effects to audio files via SoX.

    SoX (Sound eXchange) must be installed on the system — the class
    delegates all audio processing to the ``sox`` CLI binary.
    """

    def __init__(self, output_dir: str | Path | None = None) -> None:
        """Initialise the modulator.

        Args:
            output_dir: Directory for processed audio files.
        """
        self._output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_mask(mask: str) -> list[str]:
        """Return the SoX effect chain for *mask*.

        If the mask is ``"random"``, pick one of the known masks at random.
        """
        if mask == "random":
            chosen = random.choice(list(MASKS.keys()))
            return MASKS[chosen]
        if mask not in MASKS:
            logger.warning("Unknown mask '%s'; falling back to 'warm'", mask)
            return MASKS["warm"]
        return MASKS[mask]

    def modulate(self, audio_path: str | Path, mask: str = "warm") -> str:
        """Apply *mask* to the audio at *audio_path* and write the result.

        Args:
            audio_path: Input audio file path.
            mask: Voice mask identifier (``"warm"``, ``"robotic"``,
                ``"ethereal"``, ``"deep"``, ``"random"``).

        Returns:
            Absolute path to the modulated audio file (WAV).

        Raises:
            FileNotFoundError: If *audio_path* does not exist.
            RuntimeError: If SoX is not installed or processing fails.
        """
        src = Path(audio_path)
        if not src.exists():
            raise FileNotFoundError(f"Audio file not found: {src}")

        dst = self._output_dir / f"mod_{uuid.uuid4().hex}.wav"
        effects = self._resolve_mask(mask)

        cmd = ["sox", str(src), str(dst)] + effects
        logger.info("Running SoX: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "SoX (sox) is not installed. Install it with: brew install sox "
                "or: apt-get install sox"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"SoX processing failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )

        return str(dst.resolve())
