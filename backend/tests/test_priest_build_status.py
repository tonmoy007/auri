"""Tests for the index build status file and lock.

The admin API and the builder run as separate processes and share only these two
files, so they must survive a crash, a half-written file and a dead builder.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.priest import build_status
from app.priest.build_status import BuildState, BuildStatus


def test_with_no_file_the_state_is_idle(tmp_path: Path) -> None:
    # Act / Assert
    assert build_status.read(tmp_path).state is BuildState.idle


def test_a_written_status_reads_back_and_carries_no_text(tmp_path: Path) -> None:
    # Arrange
    status = BuildStatus(
        state=BuildState.succeeded,
        started_at="2026-09-30T10:00:00+00:00",
        finished_at="2026-09-30T10:01:00+00:00",
        pid=123,
        notes_total=10,
        notes_indexed=8,
        chunks=40,
        exclusions={"type:moc": 2},
        error_code=None,
    )

    # Act
    build_status.write(tmp_path, status)

    # Assert
    assert build_status.read(tmp_path) == status
    assert set(build_status.read(tmp_path).to_json()) >= {
        "state",
        "chunks",
        "error_code",
    }


def test_a_corrupt_file_reads_as_idle_not_as_a_crash(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / build_status.BUILD_STATUS_FILE).write_text("{ not json")

    # Act / Assert
    assert build_status.read(tmp_path).state is BuildState.idle


def test_a_status_with_an_unknown_state_reads_as_idle(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / build_status.BUILD_STATUS_FILE).write_text('{"state": "exploding"}')

    # Act / Assert
    assert build_status.read(tmp_path).state is BuildState.idle


def test_the_lock_is_exclusive_while_it_is_held(tmp_path: Path) -> None:
    # Act
    first = build_status.acquire_lock(tmp_path, pid=111)
    second = build_status.acquire_lock(tmp_path, pid=222)

    # Assert
    assert first is True and second is False
    assert build_status.lock_owner(tmp_path) == 111


def test_the_lock_can_be_taken_again_after_it_is_released(tmp_path: Path) -> None:
    # Arrange
    build_status.acquire_lock(tmp_path, pid=111)
    build_status.release_lock(tmp_path, pid=111)

    # Act
    again = build_status.acquire_lock(tmp_path, pid=222)

    # Assert
    assert again is True and build_status.lock_owner(tmp_path) == 222


def test_releasing_leaves_no_lock_file_behind(tmp_path: Path) -> None:
    # Arrange
    build_status.acquire_lock(tmp_path, pid=111)

    # Act
    build_status.release_lock(tmp_path, pid=111)

    # Assert
    assert not (tmp_path / build_status.LOCK_FILE).exists()
    assert build_status.lock_owner(tmp_path) is None


def test_releasing_only_removes_the_callers_own_lock(tmp_path: Path) -> None:
    # Arrange
    build_status.acquire_lock(tmp_path, pid=111)

    # Act
    build_status.release_lock(tmp_path, pid=999)
    still_held = build_status.lock_owner(tmp_path)
    second = build_status.acquire_lock(tmp_path, pid=222)
    build_status.release_lock(tmp_path, pid=111)

    # Assert
    assert still_held == 111 and second is False
    assert build_status.lock_owner(tmp_path) is None


def test_a_lock_file_left_behind_by_a_crash_does_not_block(tmp_path: Path) -> None:
    # Arrange: what a killed builder leaves; the kernel dropped its lock with it
    (tmp_path / build_status.LOCK_FILE).write_text(f"{os.getpid()} flock")

    # Act / Assert
    assert build_status.lock_owner(tmp_path) is None
    assert build_status.acquire_lock(tmp_path, pid=5) is True


def test_a_reused_pid_in_a_stale_lock_file_does_not_keep_the_lock(
    tmp_path: Path,
) -> None:
    # Arrange: the recorded pid belongs to some live, unrelated process
    (tmp_path / build_status.LOCK_FILE).write_text("1 flock")

    # Act / Assert
    assert build_status.acquire_lock(tmp_path, pid=5, is_alive=lambda pid: True)


def test_an_empty_or_garbage_lock_file_does_not_block(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / build_status.LOCK_FILE).write_text("not a pid")

    # Act / Assert
    assert build_status.acquire_lock(tmp_path, pid=5, is_alive=lambda pid: True) is True


def test_a_live_builder_of_the_previous_version_still_holds_the_lock(
    tmp_path: Path,
) -> None:
    # Arrange: the old builder wrote a bare pid and held no kernel lock
    (tmp_path / build_status.LOCK_FILE).write_text("4242")

    # Act
    taken = build_status.acquire_lock(tmp_path, pid=5, is_alive=lambda pid: True)

    # Assert
    assert taken is False
    assert build_status.lock_owner(tmp_path, is_alive=lambda pid: True) == 4242


def test_a_dead_builder_of_the_previous_version_is_taken_over(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / build_status.LOCK_FILE).write_text("4242")

    # Act
    taken = build_status.acquire_lock(tmp_path, pid=5, is_alive=lambda pid: False)

    # Assert
    assert taken is True and build_status.lock_owner(tmp_path) == 5


_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from app.priest import build_status
assert build_status.acquire_lock(Path(sys.argv[2]), pid=4321)
print("held", flush=True)
time.sleep(60)
"""


def test_the_kernel_drops_the_lock_when_its_holder_is_killed(tmp_path: Path) -> None:
    # Arrange: another process takes the lock and is then killed without releasing
    backend = str(Path(__file__).resolve().parents[1])
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, backend, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"

        # Act / Assert: refused while it lives, taken the moment it dies
        assert build_status.acquire_lock(tmp_path, pid=5) is False
        holder.kill()
        holder.wait(timeout=30)
        assert build_status.acquire_lock(tmp_path, pid=5) is True
    finally:
        holder.kill()
        holder.wait(timeout=30)
        if holder.stdout is not None:
            holder.stdout.close()


def test_a_reader_never_sees_a_half_written_lock_as_a_free_one(tmp_path: Path) -> None:
    # Arrange: the file exists and is empty, as between create and the pid write
    (tmp_path / build_status.LOCK_FILE).write_text("")
    build_status.acquire_lock(tmp_path, pid=111)

    # Act: a second taker while the first holds the kernel lock
    second = build_status.acquire_lock(tmp_path, pid=222)

    # Assert
    assert second is False


def test_a_running_builder_whose_process_died_is_reported_as_failed(
    tmp_path: Path,
) -> None:
    # Arrange — the builder crashed mid-run and never wrote its end state
    build_status.write(
        tmp_path,
        BuildStatus(state=BuildState.running, started_at="t", pid=4242),
    )

    # Act
    status = build_status.read(tmp_path, is_alive=lambda pid: False)

    # Assert
    assert status.state is BuildState.failed and status.error_code == "builder_died"


def test_a_failed_write_to_the_lock_file_does_not_leave_the_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — a full disk: the descriptor and the kernel lock must not be kept
    real_write = os.write

    def failing_write(fd: int, data: bytes) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build_status.os, "write", failing_write)

    # Act / Assert
    with pytest.raises(OSError):
        build_status.acquire_lock(tmp_path, pid=os.getpid())
    monkeypatch.setattr(build_status.os, "write", real_write)
    assert build_status.acquire_lock(tmp_path, pid=os.getpid()) is True
    build_status.release_lock(tmp_path, pid=os.getpid())
