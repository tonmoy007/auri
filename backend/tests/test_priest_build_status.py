"""Tests for the index build status file and lock.

The admin API and the builder run as separate processes and share only these two
files, so they must survive a crash, a half-written file and a dead builder.
"""

from __future__ import annotations

from pathlib import Path

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


def test_the_lock_is_exclusive_while_its_owner_is_alive(tmp_path: Path) -> None:
    # Arrange
    alive = lambda pid: True

    # Act
    first = build_status.acquire_lock(tmp_path, pid=111, is_alive=alive)
    second = build_status.acquire_lock(tmp_path, pid=222, is_alive=alive)

    # Assert
    assert first is True and second is False


def test_a_lock_left_by_a_dead_builder_is_taken_over(tmp_path: Path) -> None:
    # Arrange
    build_status.acquire_lock(tmp_path, pid=111, is_alive=lambda pid: True)

    # Act
    taken = build_status.acquire_lock(tmp_path, pid=222, is_alive=lambda pid: False)

    # Assert
    assert taken is True
    assert build_status.lock_owner(tmp_path) == 222


def test_releasing_only_removes_the_callers_own_lock(tmp_path: Path) -> None:
    # Arrange
    build_status.acquire_lock(tmp_path, pid=111, is_alive=lambda pid: True)

    # Act
    build_status.release_lock(tmp_path, pid=999)
    still_held = build_status.lock_owner(tmp_path)
    build_status.release_lock(tmp_path, pid=111)

    # Assert
    assert still_held == 111 and build_status.lock_owner(tmp_path) is None


def test_a_garbage_lock_file_counts_as_stale(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / build_status.LOCK_FILE).write_text("not a pid")

    # Act / Assert
    assert build_status.acquire_lock(tmp_path, pid=5, is_alive=lambda pid: True) is True


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
