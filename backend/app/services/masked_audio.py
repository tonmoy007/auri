"""Masked recordings held briefly for a one-time download (plan task 16.4).

Masking a five-minute recording used to come back as about 33.6 MB of base64
JSON, which the app had to hold in memory. With ``delivery=download`` the masked
WAV stays in a temp file here for at most ``ttl``, the app streams it straight to
disk with ``expo-file-system``, and the file is deleted on that single fetch or
when it expires. Only the device that uploaded the recording can fetch it, and a
cap on files held at once bounds the disk this can use.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


class MaskedStoreFull(Exception):
    """Too many masked files are waiting to be fetched."""


@dataclass(frozen=True)
class _Held:
    device: str
    path: Path
    held_at: datetime


class MaskedAudioStore:
    """Thread-safe holder of masked files keyed by an unguessable id."""

    def __init__(
        self, max_held: int = 8, ttl: timedelta = timedelta(minutes=2)
    ) -> None:
        """Create a store.

        Args:
            max_held: Masked files that may wait to be fetched at once.
            ttl: How long a file waits before it is deleted unfetched.
        """
        self.max_held = max_held
        self._ttl = ttl
        self._held: dict[str, _Held] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Delete every held file (tests, and a process that is shutting down)."""
        with self._lock:
            for held in self._held.values():
                held.path.unlink(missing_ok=True)
            self._held.clear()

    def _sweep(self, now: datetime) -> None:
        for download_id, held in list(self._held.items()):
            if now - held.held_at > self._ttl:
                held.path.unlink(missing_ok=True)
                del self._held[download_id]

    def hold(self, device: str, path: Path, now: datetime) -> str:
        """Take ownership of the masked file at *path* and return its id.

        Raises:
            MaskedStoreFull: If ``max_held`` files are already waiting.
        """
        with self._lock:
            self._sweep(now)
            if len(self._held) >= self.max_held:
                raise MaskedStoreFull
            download_id = secrets.token_urlsafe(18)
            self._held[download_id] = _Held(device=device, path=path, held_at=now)
            return download_id

    def take(self, download_id: str, device: str, now: datetime) -> Path | None:
        """Hand the file to *device* once; the caller deletes it after sending.

        Returns ``None`` for an unknown, expired or another device's id, without
        using up the owner's fetch.
        """
        with self._lock:
            self._sweep(now)
            held = self._held.get(download_id)
            if held is None or not secrets.compare_digest(held.device, device):
                return None
            del self._held[download_id]
            return held.path

    def held_path(self, download_id: str) -> Path | None:
        """Where a held file is, without taking it (diagnostics and tests)."""
        with self._lock:
            held = self._held.get(download_id)
            return held.path if held else None
