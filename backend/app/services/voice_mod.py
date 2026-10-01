"""Voice modulation service using SoX for pitch/formant shifting."""

from __future__ import annotations

import logging
import os
import random
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from app.services.settings_service import get_config_json

logger = logging.getLogger(__name__)

Mask = Literal["warm", "robotic", "ethereal", "deep", "random"]

# Pre-defined SoX effect chains for each voice mask.
MASKS: dict[str, list[str]] = {
    "warm": ["pitch", "-300", "overdrive", "5"],
    # No trailing "vocoder": SoX has no effect by that name, so it was parsed
    # as an extra chorus argument and every robotic mask failed with
    # "sox FAIL chorus: usage: gain-in gain-out delay decay speed depth".
    # Present since the initial scaffold — the mask has never worked.
    "robotic": ["chorus", "0.5", "0.9", "50", "0.5", "0.25", "2", "-t"],
    "ethereal": ["reverb", "80", "50", "80", "100", "5", "pitch", "+600"],
    "deep": ["pitch", "-800", "bass", "+10"],
}

OUTPUT_DIR = Path.cwd() / "data" / "modulated"

# SoX's stock build (no libfdk-aac) can't decode AAC/M4A — the format the
# mobile app actually records in (`useAudioRecorder.ts`'s `.aac` / MPEG4AAC
# output). Anything outside this set is transcoded to WAV via ffmpeg first.
_SOX_NATIVE_SUFFIXES = frozenset(
    {".wav", ".aiff", ".aif", ".flac", ".ogg", ".mp3", ".au", ".raw"}
)


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
        A dashboard-configured ``VOICE_MASK_<NAME>`` override (JSON list of
        SoX args) takes precedence over the built-in default for that mask.
        """
        if mask == "random":
            mask = random.choice(list(MASKS.keys()))
        if mask not in MASKS:
            logger.warning("Unknown mask '%s'; falling back to 'warm'", mask)
            mask = "warm"

        chain = get_config_json(f"VOICE_MASK_{mask.upper()}", MASKS[mask])
        if not isinstance(chain, list) or not all(isinstance(a, str) for a in chain):
            logger.warning(
                "Ignoring non-list VOICE_MASK_%s override; using built-in default",
                mask.upper(),
            )
            return MASKS[mask]
        return chain

    @staticmethod
    def _transcode_to_wav(src: Path) -> Path:
        """Convert *src* to a temp WAV file via ffmpeg for SoX to consume.

        Only called for formats outside ``_SOX_NATIVE_SUFFIXES`` — in
        practice this is the recorded confession's AAC/M4A container, which
        this build of SoX (no libfdk-aac) cannot decode directly. Caller
        owns cleanup of the returned path.

        Raises:
            RuntimeError: If ffmpeg is not installed or transcoding fails.
        """
        fd, tmp_path_str = tempfile.mkstemp(suffix=".wav", prefix="auri_mask_src_")
        os.close(fd)
        tmp_path = Path(tmp_path_str)

        cmd = ["ffmpeg", "-y", "-i", str(src), str(tmp_path)]
        logger.info("Transcoding to WAV via ffmpeg: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, check=False
            )
        except FileNotFoundError as exc:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(
                "ffmpeg is not installed. Install it with: brew install ffmpeg "
                "or: apt-get install ffmpeg"
            ) from exc
        except BaseException:
            # A timeout, or anything else: the half-converted copy of the
            # confessor's voice must not stay in the temp directory.
            tmp_path.unlink(missing_ok=True)
            raise

        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg transcoding failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return tmp_path

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
            RuntimeError: If SoX/ffmpeg is not installed or processing fails.
        """
        src = Path(audio_path)
        if not src.exists():
            raise FileNotFoundError(f"Audio file not found: {src}")

        transcoded: Path | None = None
        if src.suffix.lower() not in _SOX_NATIVE_SUFFIXES:
            transcoded = self._transcode_to_wav(src)
            src = transcoded

        dst = self._output_dir / f"mod_{uuid.uuid4().hex}.wav"
        effects = self._resolve_mask(mask)

        cmd = ["sox", str(src), str(dst)] + effects
        logger.info("Running SoX: %s", " ".join(cmd))

        # SoX creates its output file before it has processed any audio, so every
        # way this can fail leaves a truncated recording of the confessor's voice
        # in the output directory. Nothing else would ever delete it.
        succeeded = False
        try:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
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
            succeeded = True
        finally:
            # Tidying is best effort and must neither skip the other cleanup nor
            # turn a good mask into a failure: the caller never learns the output
            # path of a call that raised, so raising after success would orphan it.
            if transcoded is not None:
                try:
                    transcoded.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Could not remove temporary transcode %s", transcoded
                    )
            if not succeeded:
                try:
                    dst.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove failed mask output %s", dst)

        return str(dst.resolve())
