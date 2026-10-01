"""Tests for the versioned index store and its hot-reloading loader.

Everything is synthetic: short invented chunks and small random vectors written under
``tmp_path``. The behaviours that matter are that a half-written version can never be
served, that ``ACTIVE`` only ever names a version that loads, and that the loader
follows ``ACTIVE`` when it moves without a restart.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from app.exceptions import PriestIndexError
from app.priest import build_status, index_store
from app.priest.embedder import EmbedModelInfo
from app.priest.index_store import (
    ActiveIndex,
    IndexBusyError,
    IndexManifest,
    LoadedIndex,
    activate,
    activate_exclusive,
    list_versions,
    load_version,
    prune,
    write_version,
)
from app.priest.types import Chunk, QuoteBlock

DIM = 4
DIGEST = "sha256:digest-one"


def make_chunk(i: int) -> Chunk:
    """A synthetic chunk; every field is exercised by the round trip."""
    return Chunk(
        chunk_id=f"c{i:03d}",
        note_path=f"stories/demo/note-{i // 2}.md",
        note_title=f"Demo Note {i // 2}",
        heading_path=("Demo Note", f"Section {i}"),
        obsidian_anchor=f"#Section {i}",
        note_type="story",
        traditions=("buddhism",) if i % 2 == 0 else ("jainism", "hinduism"),
        text=f"Synthetic passage number {i}, with an accent: café.",
        quote_blocks=(
            QuoteBlock(
                text=f"Invented quote {i}", attribution="Somebody", narrative=False
            ),
        )
        if i % 3 == 0
        else (),
        char_len=40 + i,
    )


def make_vectors(n: int, dim: int = DIM, seed: int = 0) -> np.ndarray:
    """Random unit vectors as float32."""
    raw = np.random.default_rng(seed).normal(size=(n, dim)).astype(np.float32)
    return (raw / np.linalg.norm(raw, axis=1, keepdims=True)).astype(np.float32)


def make_manifest(
    version: str, n: int, *, dim: int = DIM, digest: str = DIGEST
) -> IndexManifest:
    """A manifest that agrees with *n* chunks of dimension *dim*."""
    return IndexManifest(
        version=version,
        created_at="2026-09-30T00:00:00Z",
        embed_model="nomic-embed-text",
        embed_digest=digest,
        dim=dim,
        chunk_count=n,
        note_count=max(1, n // 2),
        note_hashes={
            f"stories/demo/note-{i}.md": f"{i:064x}" for i in range(max(1, n // 2))
        },
        exclusions={"redirect": 2, "planned": 1},
        unresolved_links=3,
        cleaner_version="c1",
        chunker_version="k1",
        warnings=["synthetic warning"],
        build_seconds=1.5,
        error_code=None,
    )


def build(
    root: Path, version: str, n: int = 4, *, digest: str = DIGEST, seed: int = 0
) -> Path:
    """Write one valid version and return its directory."""
    chunks = [make_chunk(i) for i in range(n)]
    return write_version(
        root,
        chunks,
        make_vectors(n, seed=seed),
        make_manifest(version, n, digest=digest),
    )


V1, V2, V3, V4, V5 = (f"2026093{d}T000000Z-abcd123{d}" for d in range(1, 6))


# ── Round trip and layout ───────────────────────────────────────────────


def test_a_written_version_loads_back_identically(tmp_path: Path) -> None:
    # Arrange
    chunks = [make_chunk(i) for i in range(5)]
    vectors = make_vectors(5)
    manifest = make_manifest(V1, 5)

    # Act
    write_version(tmp_path, chunks, vectors, manifest)
    loaded = load_version(tmp_path, V1)

    # Assert
    assert loaded.chunks == tuple(chunks)
    assert np.array_equal(loaded.vectors, vectors)
    assert loaded.vectors.dtype == np.float32
    assert loaded.manifest == manifest


def test_a_version_is_three_files_in_its_own_directory(tmp_path: Path) -> None:
    # Arrange / Act
    path = build(tmp_path, V1)

    # Assert
    assert path == tmp_path / "versions" / V1
    assert sorted(p.name for p in path.iterdir()) == [
        "chunks.jsonl",
        "manifest.json",
        "vectors.npy",
    ]
    assert len((path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()) == 4


def test_the_manifest_file_holds_counts_and_hashes_but_no_note_text(
    tmp_path: Path,
) -> None:
    # Arrange / Act
    path = build(tmp_path, V1)
    raw = (path / "manifest.json").read_text(encoding="utf-8")

    # Assert
    assert set(json.loads(raw)) == {f.name for f in dataclasses.fields(IndexManifest)}
    assert "Synthetic passage" not in raw


# ── Write-side validation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c, v, m: (c[:-1], v, m), id="fewer-chunks-than-rows"),
        pytest.param(lambda c, v, m: (c, v[:-1], m), id="fewer-rows-than-chunks"),
        pytest.param(
            lambda c, v, m: (c, np.zeros((len(c), DIM + 1), np.float32) + 1, m),
            id="dimension-differs-from-manifest",
        ),
        pytest.param(lambda c, v, m: (c, v.astype(np.float64), m), id="not-float32"),
        pytest.param(lambda c, v, m: (c, v[:, 0], m), id="not-two-dimensional"),
        pytest.param(
            lambda c, v, m: (c, v, dataclasses.replace(m, chunk_count=99)),
            id="manifest-chunk-count-wrong",
        ),
        pytest.param(
            lambda c, v, m: (
                c,
                np.where(np.arange(v.size).reshape(v.shape) == 0, np.nan, v).astype(
                    np.float32
                ),
                m,
            ),
            id="non-finite-value",
        ),
        pytest.param(
            lambda c, v, m: ([], v[:0], dataclasses.replace(m, chunk_count=0)),
            id="empty",
        ),
    ],
)
def test_a_misaligned_or_invalid_version_is_refused_and_leaves_nothing(
    tmp_path: Path, mutate: Callable[..., tuple[list[Chunk], np.ndarray, IndexManifest]]
) -> None:
    # Arrange
    chunks = [make_chunk(i) for i in range(4)]
    chunks, vectors, manifest = mutate(chunks, make_vectors(4), make_manifest(V1, 4))

    # Act
    with pytest.raises(PriestIndexError):
        write_version(tmp_path, chunks, vectors, manifest)

    # Assert
    assert list_versions(tmp_path) == []
    assert not (tmp_path / "versions").exists() or not any(
        (tmp_path / "versions").iterdir()
    )


def test_a_write_that_fails_part_way_leaves_no_version_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    def broken_save(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(index_store.np, "save", broken_save)

    # Act
    with pytest.raises(PriestIndexError):
        build(tmp_path, V1)

    # Assert
    assert list_versions(tmp_path) == []
    assert list((tmp_path / "versions").iterdir()) == []


def test_a_version_that_already_exists_is_not_overwritten(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1, seed=1)
    before = load_version(tmp_path, V1).vectors.copy()

    # Act
    with pytest.raises(PriestIndexError):
        build(tmp_path, V1, seed=2)

    # Assert
    assert np.array_equal(load_version(tmp_path, V1).vectors, before)


@pytest.mark.parametrize(
    "bad", ["../escape", "a/b", "", ".hidden", "with space", "a\x00b"]
)
def test_a_version_name_that_could_leave_the_index_directory_is_refused(
    tmp_path: Path, bad: str
) -> None:
    # Arrange
    build(tmp_path, V1)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        activate(tmp_path, bad)
    with pytest.raises(PriestIndexError):
        write_version(tmp_path, [make_chunk(0)], make_vectors(1), make_manifest(bad, 1))
    with pytest.raises(PriestIndexError):
        load_version(tmp_path, bad)


# ── Activation ──────────────────────────────────────────────────────────


def test_activation_writes_the_pointer_and_leaves_no_temp_files(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)

    # Act
    activate(tmp_path, V1)

    # Assert
    assert (tmp_path / "ACTIVE").read_text(encoding="utf-8").strip() == V1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ACTIVE", "versions"]


def test_activation_replaces_the_pointer_with_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)
    seen: list[tuple[str, str]] = []
    real = os.replace

    def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        seen.append((str(src), str(dst)))
        real(src, dst)

    monkeypatch.setattr(index_store.os, "replace", spy)

    # Act
    activate(tmp_path, V2)

    # Assert
    assert [dst for _, dst in seen] == [str(tmp_path / "ACTIVE")]
    assert seen[0][0] != seen[0][1]


def test_a_partly_written_version_is_never_activated(tmp_path: Path) -> None:
    # Arrange: a crash left a directory with only the chunk file in it
    build(tmp_path, V1)
    activate(tmp_path, V1)
    partial = tmp_path / "versions" / V2
    partial.mkdir()
    (partial / "chunks.jsonl").write_text("{}\n", encoding="utf-8")

    # Act
    with pytest.raises(PriestIndexError):
        activate(tmp_path, V2)

    # Assert: the pointer still names the good version
    assert (tmp_path / "ACTIVE").read_text(encoding="utf-8").strip() == V1


def test_activating_a_version_that_does_not_exist_is_refused_and_changes_nothing(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)

    # Act
    with pytest.raises(PriestIndexError):
        activate(tmp_path, V2)

    # Assert
    assert (tmp_path / "ACTIVE").read_text(encoding="utf-8").strip() == V1


def _corrupt_chunks_json(path: Path) -> None:
    (path / "chunks.jsonl").write_text("{not json\n", encoding="utf-8")


def _drop_a_chunk_line(path: Path) -> None:
    lines = (path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    (path / "chunks.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")


def _chunk_missing_a_field(path: Path) -> None:
    lines = (path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    del row["note_path"]
    lines[0] = json.dumps(row)
    (path / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _garbage_vectors(path: Path) -> None:
    (path / "vectors.npy").write_bytes(b"garbage")


def _wrong_width_vectors(path: Path) -> None:
    np.save(path / "vectors.npy", make_vectors(4, dim=DIM + 2))


def _float64_vectors(path: Path) -> None:
    np.save(path / "vectors.npy", make_vectors(4).astype(np.float64))


def _nan_vectors(path: Path) -> None:
    vectors = make_vectors(4)
    vectors[2, 1] = np.nan
    np.save(path / "vectors.npy", vectors)


def _manifest_not_json(path: Path) -> None:
    (path / "manifest.json").write_text("[[[", encoding="utf-8")


def _manifest_missing_key(path: Path) -> None:
    data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    del data["embed_digest"]
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _manifest_wrong_type(path: Path) -> None:
    data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    data["dim"] = "four"
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _manifest_warnings_not_a_list(path: Path) -> None:
    data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    data["warnings"] = "just a string"
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _manifest_names_another_version(path: Path) -> None:
    data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    data["version"] = "some-other-version"
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _manifest_count_off(path: Path) -> None:
    data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    data["chunk_count"] = 7
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _missing_vectors(path: Path) -> None:
    (path / "vectors.npy").unlink()


CORRUPTIONS = [
    pytest.param(_corrupt_chunks_json, id="chunks-not-json"),
    pytest.param(_drop_a_chunk_line, id="chunk-rows-fewer-than-vectors"),
    pytest.param(_chunk_missing_a_field, id="chunk-missing-field"),
    pytest.param(_garbage_vectors, id="vectors-garbage"),
    pytest.param(_wrong_width_vectors, id="vectors-wrong-dimension"),
    pytest.param(_float64_vectors, id="vectors-not-float32"),
    pytest.param(_nan_vectors, id="vectors-not-finite"),
    pytest.param(_manifest_not_json, id="manifest-not-json"),
    pytest.param(_manifest_missing_key, id="manifest-missing-key"),
    pytest.param(_manifest_wrong_type, id="manifest-wrong-type"),
    pytest.param(_manifest_warnings_not_a_list, id="manifest-warnings-not-a-list"),
    pytest.param(_manifest_names_another_version, id="manifest-other-version"),
    pytest.param(_manifest_count_off, id="manifest-count-off"),
    pytest.param(_missing_vectors, id="vectors-missing"),
]


@pytest.mark.parametrize("corrupt", CORRUPTIONS)
def test_a_corrupt_version_can_be_neither_loaded_nor_activated(
    tmp_path: Path, corrupt: Callable[[Path], None]
) -> None:
    # Arrange
    good = build(tmp_path, V1)
    activate(tmp_path, V1)
    bad = build(tmp_path, V2)
    corrupt(bad)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        load_version(tmp_path, V2)
    with pytest.raises(PriestIndexError):
        activate(tmp_path, V2)
    assert (tmp_path / "ACTIVE").read_text(encoding="utf-8").strip() == V1
    assert good.exists()


# ── Listing and pruning ─────────────────────────────────────────────────


def test_versions_are_listed_oldest_first_and_ignore_stray_entries(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V3)
    build(tmp_path, V1)
    build(tmp_path, V2)
    (tmp_path / "versions" / ".tmp-half").mkdir()
    (tmp_path / "versions" / "a-file.txt").write_text("x", encoding="utf-8")

    # Act
    versions = list_versions(tmp_path)

    # Assert
    assert versions == [V1, V2, V3]


def test_listing_an_index_that_does_not_exist_yet_is_empty(tmp_path: Path) -> None:
    # Act / Assert
    assert list_versions(tmp_path / "nowhere") == []


def test_prune_keeps_the_last_three(tmp_path: Path) -> None:
    # Arrange
    for v in (V1, V2, V3, V4, V5):
        build(tmp_path, v)
    activate(tmp_path, V5)

    # Act
    removed = prune(tmp_path)

    # Assert
    assert list_versions(tmp_path) == [V3, V4, V5]
    assert sorted(removed) == [V1, V2]


def test_prune_never_removes_the_active_version_even_when_it_is_old(
    tmp_path: Path,
) -> None:
    # Arrange: rolled back to the oldest version
    for v in (V1, V2, V3, V4, V5):
        build(tmp_path, v)
    activate(tmp_path, V1)

    # Act
    prune(tmp_path)

    # Assert
    assert list_versions(tmp_path) == [V1, V3, V4, V5]
    assert ActiveIndex(tmp_path).get().manifest.version == V1


def test_prune_with_no_pointer_keeps_only_the_last_three(tmp_path: Path) -> None:
    # Arrange
    for v in (V1, V2, V3, V4):
        build(tmp_path, v)

    # Act
    prune(tmp_path)

    # Assert
    assert list_versions(tmp_path) == [V2, V3, V4]


def test_prune_honours_a_smaller_keep_count(tmp_path: Path) -> None:
    # Arrange
    for v in (V1, V2, V3):
        build(tmp_path, v)
    activate(tmp_path, V3)

    # Act
    prune(tmp_path, keep=1)

    # Assert
    assert list_versions(tmp_path) == [V3]


def test_prune_refuses_a_keep_count_below_one(tmp_path: Path) -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="keep"):
        prune(tmp_path, keep=0)


# ── The hot-reloading loader ────────────────────────────────────────────


def test_a_loader_with_no_pointer_raises(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        ActiveIndex(tmp_path).get()


def test_the_loader_serves_the_active_version(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)

    # Act
    loaded = ActiveIndex(tmp_path).get()

    # Assert
    assert isinstance(loaded, LoadedIndex)
    assert loaded.manifest.version == V1
    assert len(loaded.chunks) == loaded.vectors.shape[0] == 4


def test_the_loader_does_not_reload_while_the_pointer_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)
    active = ActiveIndex(tmp_path)
    loads: list[str] = []
    real = index_store.load_version

    def counting(root: Path, version: str) -> LoadedIndex:
        loads.append(version)
        return real(root, version)

    monkeypatch.setattr(index_store, "load_version", counting)

    # Act
    first = active.get()
    second = active.get()

    # Assert
    assert first is second
    assert loads == [V1]


def test_the_loader_follows_the_pointer_to_a_new_version(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2, n=6)
    activate(tmp_path, V1)
    active = ActiveIndex(tmp_path)
    assert active.get().manifest.version == V1

    # Act
    activate(tmp_path, V2)
    reloaded = active.get()

    # Assert
    assert reloaded.manifest.version == V2
    assert len(reloaded.chunks) == 6


def test_a_rollback_is_just_activating_an_earlier_version(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1, n=3)
    build(tmp_path, V2, n=5)
    activate(tmp_path, V2)
    active = ActiveIndex(tmp_path)
    assert len(active.get().chunks) == 5

    # Act
    activate(tmp_path, V1)

    # Assert
    assert active.get().manifest.version == V1
    assert len(active.get().chunks) == 3


def test_the_loader_notices_a_pointer_rewritten_with_the_same_mtime(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)
    pointer = tmp_path / "ACTIVE"
    stamp = pointer.stat().st_mtime_ns
    active = ActiveIndex(tmp_path)
    assert active.get().manifest.version == V1

    # Act: same size, same mtime, different content
    pointer.write_text(V2 + "\n", encoding="utf-8")
    os.utime(pointer, ns=(stamp, stamp))

    # Assert
    assert active.get().manifest.version == V2


def test_a_pointer_to_a_missing_version_raises(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)
    (tmp_path / "ACTIVE").write_text(V2 + "\n", encoding="utf-8")

    # Act / Assert
    with pytest.raises(PriestIndexError):
        ActiveIndex(tmp_path).get()


def test_a_pointer_with_nonsense_in_it_raises(tmp_path: Path) -> None:
    # Arrange
    build(tmp_path, V1)
    (tmp_path / "ACTIVE").write_text("../../etc/passwd\n", encoding="utf-8")

    # Act / Assert
    with pytest.raises(PriestIndexError):
        ActiveIndex(tmp_path).get()


def test_a_version_that_turns_corrupt_is_refused_and_a_repair_is_picked_up(
    tmp_path: Path,
) -> None:
    # Arrange
    path = build(tmp_path, V1)
    activate(tmp_path, V1)
    active = ActiveIndex(tmp_path)
    assert active.get().manifest.version == V1
    build(tmp_path, V2)
    activate(tmp_path, V2)
    (tmp_path / "versions" / V2 / "vectors.npy").write_bytes(b"garbage")

    # Act / Assert: fails closed, then recovers when pointed back at a good version
    with pytest.raises(PriestIndexError):
        active.get()
    activate(tmp_path, V1)
    assert active.get().manifest.version == V1
    assert path.exists()


def test_the_loader_reloads_once_even_when_many_threads_ask_at_the_same_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)
    active = ActiveIndex(tmp_path)
    active.get()
    activate(tmp_path, V2)
    loads: list[str] = []
    real = index_store.load_version

    def slow_load(root: Path, version: str) -> LoadedIndex:
        loads.append(version)
        threading.Event().wait(0.05)
        return real(root, version)

    monkeypatch.setattr(index_store, "load_version", slow_load)
    results: list[LoadedIndex] = []

    # Act
    threads = [
        threading.Thread(target=lambda: results.append(active.get())) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert
    assert loads == [V2]
    assert len(results) == 8 and all(r is results[0] for r in results)


# ── Activation while a build runs ───────────────────────────────────────


def test_activate_exclusive_switches_the_pointer_when_no_build_runs(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)

    # Act
    activate_exclusive(tmp_path, V2)

    # Assert: the pointer moved and the lock was given back
    assert ActiveIndex(tmp_path).get().manifest.version == V2
    assert build_status.acquire_lock(tmp_path, pid=1) is True


def test_activate_exclusive_refuses_while_a_build_holds_the_lock(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)
    assert build_status.acquire_lock(tmp_path, pid=111)

    # Act
    with pytest.raises(IndexBusyError):
        activate_exclusive(tmp_path, V2)

    # Assert: the pointer was not touched, and the builder still owns the lock
    assert ActiveIndex(tmp_path).get().manifest.version == V1
    assert build_status.lock_owner(tmp_path) == 111


def test_a_busy_refusal_is_an_index_error_so_old_callers_still_catch_it() -> None:
    assert issubclass(IndexBusyError, PriestIndexError)


def test_activate_exclusive_gives_the_lock_back_when_the_version_is_bad(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)

    # Act
    with pytest.raises(PriestIndexError):
        activate_exclusive(tmp_path, V2)

    # Assert
    assert build_status.acquire_lock(tmp_path, pid=1) is True


# ── Durability ──────────────────────────────────────────────────────────


@pytest.fixture
def synced_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every directory the store fsyncs, while still doing the real thing."""
    seen: list[Path] = []
    real = index_store._fsync_dir

    def spy(path: Path) -> None:
        seen.append(path)
        real(path)

    monkeypatch.setattr(index_store, "_fsync_dir", spy)
    return seen


def test_writing_a_version_syncs_the_directories_after_the_rename(
    tmp_path: Path, synced_dirs: list[Path]
) -> None:
    # Act
    build(tmp_path, V1)

    # Assert: the versions directory holds the new name; the root holds versions/
    assert tmp_path / "versions" in synced_dirs
    assert tmp_path in synced_dirs


def test_activating_syncs_the_root_after_replacing_the_pointer(
    tmp_path: Path, synced_dirs: list[Path]
) -> None:
    # Arrange
    build(tmp_path, V1)
    synced_dirs.clear()

    # Act
    activate(tmp_path, V1)

    # Assert
    assert synced_dirs == [tmp_path]


def test_the_directory_sync_follows_the_rename_not_precedes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    build(tmp_path, V1)
    order: list[str] = []
    real_replace = os.replace

    def note_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        order.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(index_store.os, "replace", note_replace)

    def note_dirsync(path: Path) -> None:
        order.append("dirsync")

    monkeypatch.setattr(index_store, "_fsync_dir", note_dirsync)

    # Act
    activate(tmp_path, V1)

    # Assert
    assert order == ["replace", "dirsync"]


def test_a_directory_that_cannot_be_synced_does_not_fail_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: some filesystems refuse fsync on a directory
    build(tmp_path, V1)
    real_fsync = os.fsync

    def picky(fd: int) -> None:
        if os.path.isdir(f"/dev/fd/{fd}"):
            raise OSError("not supported")
        real_fsync(fd)

    monkeypatch.setattr(index_store.os, "fsync", picky)

    # Act / Assert
    activate(tmp_path, V1)
    assert ActiveIndex(tmp_path).get().manifest.version == V1


# ── Pruning while the pointer moves ─────────────────────────────────────


def test_prune_rereads_the_pointer_before_every_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: ACTIVE is V4 when prune starts; a rollback to V2 lands mid-run
    for v in (V1, V2, V3, V4, V5):
        build(tmp_path, v)
    activate(tmp_path, V5)
    real_rmtree = index_store.shutil.rmtree
    calls = {"n": 0}

    def rmtree_then_roll_back(path: Path) -> None:
        real_rmtree(path)
        calls["n"] += 1
        if calls["n"] == 1:  # V1 is gone; the admin activates V2 right now
            activate(tmp_path, V2)

    monkeypatch.setattr(index_store.shutil, "rmtree", rmtree_then_roll_back)

    # Act
    removed = prune(tmp_path)

    # Assert: V2 was next in line but is now active, so it stays
    assert removed == [V1]
    assert V2 in list_versions(tmp_path)
    assert ActiveIndex(tmp_path).get().manifest.version == V2


# ── Failed loads are not retried on every call ──────────────────────────


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A settable monotonic clock for the loader's retry window."""
    now = [1000.0]
    monkeypatch.setattr(index_store.time, "monotonic", lambda: now[0])
    return now


def _count_loads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    loads: list[str] = []
    real = index_store.load_version

    def counting(root: Path, version: str) -> LoadedIndex:
        loads.append(version)
        return real(root, version)

    monkeypatch.setattr(index_store, "load_version", counting)
    return loads


def test_a_corrupt_active_version_is_parsed_once_not_on_every_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)
    (tmp_path / "versions" / V1 / "vectors.npy").write_bytes(b"garbage")
    loads = _count_loads(monkeypatch)
    active = ActiveIndex(tmp_path)

    # Act
    errors = []
    for _ in range(5):
        with pytest.raises(PriestIndexError) as caught:
            active.get()
        errors.append(str(caught.value))

    # Assert: one real attempt, the same message every time
    assert loads == [V1]
    assert len(set(errors)) == 1


def test_a_failed_load_is_retried_once_the_pointer_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    # Arrange
    build(tmp_path, V1)
    build(tmp_path, V2)
    activate(tmp_path, V1)
    (tmp_path / "versions" / V1 / "vectors.npy").write_bytes(b"garbage")
    active = ActiveIndex(tmp_path)
    with pytest.raises(PriestIndexError):
        active.get()

    # Act: an operator rolls back to the good version
    activate(tmp_path, V2)

    # Assert
    assert active.get().manifest.version == V2


def test_a_failed_load_is_retried_after_a_short_wait_so_a_repair_is_noticed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    # Arrange: the version is repaired in place, the pointer never moves
    build(tmp_path, V1)
    activate(tmp_path, V1)
    good = (tmp_path / "versions" / V1 / "vectors.npy").read_bytes()
    (tmp_path / "versions" / V1 / "vectors.npy").write_bytes(b"garbage")
    loads = _count_loads(monkeypatch)
    active = ActiveIndex(tmp_path)
    with pytest.raises(PriestIndexError):
        active.get()
    (tmp_path / "versions" / V1 / "vectors.npy").write_bytes(good)

    # Act
    with pytest.raises(PriestIndexError):
        active.get()  # still inside the window: no new attempt
    clock[0] += index_store._FAILURE_RETRY_SECONDS + 1
    repaired = active.get()

    # Assert
    assert loads == [V1, V1]
    assert repaired.manifest.version == V1


# ── Refusing to serve on a model mismatch ───────────────────────────────


def test_the_digest_check_passes_when_the_model_is_the_one_the_index_was_built_with(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)
    current = EmbedModelInfo(name="nomic-embed-text", digest=DIGEST, dim=DIM)

    # Act / Assert
    ActiveIndex(tmp_path).check_digest(current)


def test_a_different_model_digest_makes_the_loader_refuse_to_serve(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)
    current = EmbedModelInfo(name="nomic-embed-text", digest="sha256:other", dim=DIM)

    # Act
    with pytest.raises(PriestIndexError) as caught:
        ActiveIndex(tmp_path).check_digest(current)

    # Assert: the message is about the mismatch and leaks neither digest
    assert "digest" in str(caught.value)
    assert DIGEST not in str(caught.value) and "sha256:other" not in str(caught.value)


def test_a_different_dimension_also_makes_the_loader_refuse_to_serve(
    tmp_path: Path,
) -> None:
    # Arrange
    build(tmp_path, V1)
    activate(tmp_path, V1)
    current = EmbedModelInfo(name="nomic-embed-text", digest=DIGEST, dim=DIM * 2)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        ActiveIndex(tmp_path).check_digest(current)


def test_the_digest_check_raises_when_there_is_no_active_index(tmp_path: Path) -> None:
    # Arrange
    current = EmbedModelInfo(name="nomic-embed-text", digest=DIGEST, dim=DIM)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        ActiveIndex(tmp_path).check_digest(current)


# ── one loaded copy per index root (the admin view and the service share it) ─────


def test_the_shared_loader_is_one_object_per_index_root(tmp_path: Path) -> None:
    # Act
    first = index_store.shared_active_index(tmp_path)
    second = index_store.shared_active_index(tmp_path)
    other = index_store.shared_active_index(tmp_path / "elsewhere")

    # Assert — a second loader would hold a second full copy of the vectors in memory
    assert first is second
    assert first is not other


def test_the_service_and_the_admin_view_use_the_same_loader(
    tmp_path: Path, set_setting: Callable[[str, object], None]
) -> None:
    # Arrange
    from app.api.v1 import priest_admin
    from app.priest import priest_service

    set_setting("PRIEST_INDEX_DIR", str(tmp_path))
    priest_service.reset_priest_service()

    # Act
    service = priest_service.build_priest_service()
    priest_admin._active_manifest(tmp_path)

    # Assert
    assert service._retriever._active is index_store.shared_active_index(tmp_path)  # type: ignore[attr-defined]
