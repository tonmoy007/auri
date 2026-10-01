"""Measure an uploaded recording and enforce the recording limit (plan task 16.5).

The app stops recording at ``MAX_RECORDING_SECONDS`` (its
``MAX_RECORDING_DURATION_MS``); the server used to check only the file size, so a
longer upload was transcribed or masked for minutes before anything noticed.
``ffprobe`` reads the duration from the container header, which is quick. When it
is missing or cannot read the file the duration is unknown and only the size cap
applies: refusing would break a development machine without ffmpeg, and the
deployed images include it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import Final

from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

# Encoders pad a little; a recording stopped exactly at the limit must still pass.
_MARGIN_SECONDS: Final = 1.0
_PROBE_TIMEOUT_SECONDS: Final = 10


def duration_seconds(path: Path) -> float | None:
    """The recording's duration in seconds, or ``None`` if it cannot be read."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not measure a recording (%s)", type(exc).__name__)
        return None
    try:
        seconds = float(result.stdout.strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


async def enforce_recording_limit(path: Path) -> None:
    """Raise a 413 if the recording at *path* is longer than the app allows.

    Raises:
        HTTPException: 413 with a fixed message naming the limit.
    """
    seconds = await asyncio.to_thread(duration_seconds, path)
    limit = settings.MAX_RECORDING_SECONDS
    if seconds is not None and seconds > limit + _MARGIN_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Recording is longer than the {_limit_text(limit)} limit",
        )


def _limit_text(limit: int) -> str:
    """'5-minute' for whole minutes, else '3-second'."""
    if limit % 60 == 0:
        return f"{limit // 60}-minute"
    return f"{limit}-second"
