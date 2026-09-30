"""The index build's status file and lock, shared between processes.

The admin API starts the builder as a subprocess and later reads these two files; they
are all the two sides share. Neither holds any note text: only state, counts and an
error code. A corrupt file reads as idle, and a build whose process has died is
reported as failed rather than running for ever.

The build lock is a kernel lock (``flock``) on the lock file, so the kernel drops it
the moment its holder dies, however it dies: there is no stale lock to detect and no
pid to mistake for another process. The pid in the file is for display only.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import threading
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


# What a holder writes after its pid. A file with only a number was written by a
# builder of the previous version, which held no kernel lock.
_LOCK_MARK: Final = "flock"
# lock file path -> (open descriptor holding the kernel lock, pid recorded for it)
_held: dict[str, tuple[int, int]] = {}
_held_guard = threading.Lock()


def _lock_path(index_dir: Path) -> Path:
    return index_dir / LOCK_FILE


def _read_lock_file(index_dir: Path) -> tuple[int, bool] | None:
    """The pid in the lock file and whether it is a bare pid (previous version)."""
    try:
        parts = _lock_path(index_dir).read_text().split()
    except OSError:
        return None
    if not parts or not parts[0].isdigit():
        return None
    return int(parts[0]), len(parts) == 1


def _kernel_lock_held(index_dir: Path) -> bool:
    """Whether some open file description holds the kernel lock right now."""
    try:
        fd = os.open(_lock_path(index_dir), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
    return False


def lock_owner(
    index_dir: Path, *, is_alive: Callable[[int], bool] = _pid_alive
) -> int | None:
    """Return the pid of the builder holding the lock, for display; else ``None``.

    The lock is held when the kernel says so. A file with only a pid in it is held
    while that process lives (a builder of the previous version).
    """
    recorded = _read_lock_file(index_dir)
    if recorded is None:
        return None
    pid, bare = recorded
    if bare:
        return pid if is_alive(pid) else None
    return pid if _kernel_lock_held(index_dir) else None


def _open_locked(path: Path) -> int | None:
    """Open *path* and take the kernel lock; ``None`` if held or the file was swapped.

    A release removes the file while still holding the lock, so a taker that got the
    lock on a file that is no longer the one at *path* must start again.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_ino != os.stat(path).st_ino:
            raise FileNotFoundError
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError:
        os.close(fd)
        return -1
    return fd


def acquire_lock(
    index_dir: Path, *, pid: int, is_alive: Callable[[int], bool] = _pid_alive
) -> bool:
    """Take the build lock.

    Args:
        index_dir: The index root.
        pid: Recorded in the lock file, for display.
        is_alive: Decides whether a bare-pid lock file, from a builder of the previous
            version, still has a live owner.

    Returns:
        ``True`` if this process now holds the lock, ``False`` if a builder does.
    """
    index_dir.mkdir(parents=True, exist_ok=True)
    path = _lock_path(index_dir)
    legacy = _read_lock_file(index_dir)
    if legacy is not None and legacy[1] and is_alive(legacy[0]):
        return False
    for _ in range(3):
        fd = _open_locked(path)
        if fd is None:
            return False
        if fd < 0:
            continue
        os.ftruncate(fd, 0)
        os.write(fd, f"{pid} {_LOCK_MARK}\n".encode())
        with _held_guard:
            _held[str(path)] = (fd, pid)
        return True
    return False


def release_lock(index_dir: Path, *, pid: int) -> None:
    """Drop the lock and remove its file, but only if this process holds it for *pid*."""
    key = str(_lock_path(index_dir))
    with _held_guard:
        entry = _held.get(key)
        if entry is None or entry[1] != pid:
            return
        del _held[key]
    fd = entry[0]
    # Remove the file before dropping the lock: a taker that opened it meanwhile sees
    # that it is no longer the file at the path and starts again.
    with contextlib.suppress(OSError):
        _lock_path(index_dir).unlink()
    os.close(fd)
