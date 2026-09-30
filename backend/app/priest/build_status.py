"""The index build's status file and lock, shared between processes.

The admin API starts the builder as a subprocess and later reads these two files; they
are all the two sides share. Neither holds any note text: only state, counts and an
error code. A corrupt file reads as idle, and a build whose process has died is
reported as failed rather than running for ever.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

BUILD_STATUS_FILE: Final = "build_status.json"
LOCK_FILE: Final = "build.lock"


class BuildState(str, Enum):
    """Where an index build is."""

    idle = "idle"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


@dataclass(frozen=True)
class BuildStatus:
    """What the last (or current) build did, as counts and an error code only."""

    state: BuildState = BuildState.idle
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    notes_total: int = 0
    notes_indexed: int = 0
    chunks: int = 0
    exclusions: Mapping[str, int] = field(default_factory=dict)
    error_code: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the status as a JSON-ready dict."""
        body = asdict(self)
        body["state"] = self.state.value
        body["exclusions"] = dict(self.exclusions)
        return body


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read(
    index_dir: Path, *, is_alive: Callable[[int], bool] = _pid_alive
) -> BuildStatus:
    """Read the build status; missing, corrupt or unknown reads as idle.

    A status that says running while its process is gone is reported as failed with
    the code ``builder_died``.
    """
    try:
        raw = json.loads((index_dir / BUILD_STATUS_FILE).read_text())
        status = BuildStatus(
            state=BuildState(raw["state"]),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            pid=raw.get("pid"),
            notes_total=int(raw.get("notes_total", 0)),
            notes_indexed=int(raw.get("notes_indexed", 0)),
            chunks=int(raw.get("chunks", 0)),
            exclusions={
                str(k): int(v) for k, v in dict(raw.get("exclusions", {})).items()
            },
            error_code=raw.get("error_code"),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return BuildStatus()
    if (
        status.state is BuildState.running
        and status.pid is not None
        and not is_alive(status.pid)
    ):
        return BuildStatus(
            state=BuildState.failed,
            started_at=status.started_at,
            pid=status.pid,
            notes_total=status.notes_total,
            error_code="builder_died",
        )
    return status


def write(index_dir: Path, status: BuildStatus) -> None:
    """Write the status atomically (temp file, then replace)."""
    index_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=index_dir, prefix=".status-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(status.to_json(), handle)
        os.replace(tmp, index_dir / BUILD_STATUS_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def lock_owner(index_dir: Path) -> int | None:
    """Return the pid holding the lock, or ``None`` if unheld or unreadable."""
    try:
        return int((index_dir / LOCK_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def acquire_lock(
    index_dir: Path, *, pid: int, is_alive: Callable[[int], bool] = _pid_alive
) -> bool:
    """Take the build lock; a lock whose owner is dead or unreadable is taken over.

    Returns:
        ``True`` if this process now holds the lock, ``False`` if a live builder does.
    """
    index_dir.mkdir(parents=True, exist_ok=True)
    path = index_dir / LOCK_FILE
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = lock_owner(index_dir)
            if owner is not None and is_alive(owner):
                return False
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        with os.fdopen(fd, "w") as handle:
            handle.write(str(pid))
        return True
    return False


def release_lock(index_dir: Path, *, pid: int) -> None:
    """Remove the lock, but only if *pid* holds it."""
    if lock_owner(index_dir) == pid:
        with contextlib.suppress(OSError):
            (index_dir / LOCK_FILE).unlink()
