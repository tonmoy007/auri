"""Versioned, immutable index artifacts and a loader that follows the ``ACTIVE`` pointer.

Layout under the index root::

    ACTIVE                        one line: the version being served
    versions/<version>/chunks.jsonl    metadata and text, one chunk per line
    versions/<version>/vectors.npy     float32, row-aligned with chunks.jsonl
    versions/<version>/manifest.json   counts and hashes only, never note text

A version is written into a hidden temp directory and renamed into place, so a crash
never leaves a half-written version that looks real. ``ACTIVE`` is replaced with
``os.replace`` only after the named version has been loaded and validated, so it never
points at something that cannot be served. Rolling back is activating an older version.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from app.exceptions import PriestIndexError
from app.priest.embedder import EmbedModelInfo
from app.priest.types import Chunk, QuoteBlock

logger = logging.getLogger(__name__)

_VERSIONS_DIR: Final = "versions"
_POINTER: Final = "ACTIVE"
_CHUNKS: Final = "chunks.jsonl"
_VECTORS: Final = "vectors.npy"
_MANIFEST: Final = "manifest.json"
# Version names become directory names, so nothing that could leave the directory.
_VERSION_RE: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
_DEFAULT_KEEP: Final = 3


@dataclass(frozen=True)
class IndexManifest:
    """Facts about one index build. Counts and hashes only: no note text."""

    version: str
    created_at: str
    embed_model: str
    embed_digest: str
    dim: int
    chunk_count: int
    note_count: int
    note_hashes: dict[str, str]
    exclusions: dict[str, int]
    unresolved_links: int
    cleaner_version: str
    chunker_version: str
    warnings: list[str]
    build_seconds: float
    error_code: str | None


@dataclass(frozen=True)
class LoadedIndex:
    """An index version in memory: chunks row-aligned with unit-length vectors."""

    chunks: tuple[Chunk, ...]
    vectors: npt.NDArray[np.float32]
    manifest: IndexManifest


# field name to the types a manifest value may have when read back from disk
_MANIFEST_TYPES: Final[dict[str, tuple[type, ...]]] = {
    "version": (str,),
    "created_at": (str,),
    "embed_model": (str,),
    "embed_digest": (str,),
    "dim": (int,),
    "chunk_count": (int,),
    "note_count": (int,),
    "note_hashes": (dict,),
    "exclusions": (dict,),
    "unresolved_links": (int,),
    "cleaner_version": (str,),
    "chunker_version": (str,),
    "warnings": (list,),
    "build_seconds": (int, float),
    "error_code": (str, type(None)),
}


def _check_name(version: str) -> None:
    if not _VERSION_RE.fullmatch(version):
        raise PriestIndexError("index version name is not valid")


def _check_shapes(
    count: int, vectors: object, manifest: IndexManifest
) -> npt.NDArray[np.float32]:
    """Confirm *count* chunks, *vectors* and *manifest* agree; return the vectors."""
    if not isinstance(vectors, np.ndarray) or vectors.ndim != 2:
        raise PriestIndexError("index vectors must be a two-dimensional array")
    if vectors.dtype != np.float32:
        raise PriestIndexError("index vectors must be float32")
    if count == 0:
        raise PriestIndexError("an index needs at least one chunk")
    if vectors.shape[0] != count:
        raise PriestIndexError("index chunks and vectors are not row-aligned")
    if vectors.shape[1] != manifest.dim:
        raise PriestIndexError("index vector dimension differs from the manifest")
    if manifest.chunk_count != count:
        raise PriestIndexError("manifest chunk count differs from the chunks")
    if not np.isfinite(vectors).all():
        raise PriestIndexError("index vectors contain a non-finite value")
    return vectors


def _chunk_to_row(chunk: Chunk) -> dict[str, Any]:
    return dataclasses.asdict(chunk)


def _as(kind: type, value: object) -> Any:
    if not isinstance(value, kind):
        raise TypeError("unexpected type")
    return value


def _chunk_from_row(row: object) -> Chunk:
    """Rebuild a chunk from its JSON row; raises on a missing or mistyped field."""
    data: dict[str, Any] = _as(dict, row)
    return Chunk(
        chunk_id=_as(str, data["chunk_id"]),
        note_path=_as(str, data["note_path"]),
        note_title=_as(str, data["note_title"]),
        heading_path=tuple(_as(str, h) for h in _as(list, data["heading_path"])),
        obsidian_anchor=_as(str, data["obsidian_anchor"]),
        note_type=_as(str, data["note_type"]),
        traditions=tuple(_as(str, t) for t in _as(list, data["traditions"])),
        text=_as(str, data["text"]),
        quote_blocks=tuple(_quote_from_row(q) for q in _as(list, data["quote_blocks"])),
        char_len=_as(int, data["char_len"]),
    )


def _quote_from_row(row: object) -> QuoteBlock:
    data: dict[str, Any] = _as(dict, row)
    attribution = data["attribution"]
    if attribution is not None:
        _as(str, attribution)
    return QuoteBlock(
        text=_as(str, data["text"]),
        attribution=attribution,
        narrative=_as(bool, data["narrative"]),
    )


def _read_chunks(path: Path) -> tuple[Chunk, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise PriestIndexError("index chunk file cannot be read") from exc
    chunks = []
    for number, line in enumerate(lines, start=1):
        try:
            chunks.append(_chunk_from_row(json.loads(line)))
        except (ValueError, KeyError, TypeError) as exc:
            raise PriestIndexError(f"index chunk row {number} is malformed") from exc
    return tuple(chunks)


def _read_vectors(path: Path) -> object:
    try:
        return np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError) as exc:
        raise PriestIndexError("index vector file cannot be read") from exc


def _read_manifest(path: Path) -> IndexManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PriestIndexError("index manifest cannot be read") from exc
    if not isinstance(data, dict) or set(data) != set(_MANIFEST_TYPES):
        raise PriestIndexError("index manifest has the wrong fields")
    for key, kinds in _MANIFEST_TYPES.items():
        if not isinstance(data[key], kinds):
            raise PriestIndexError("index manifest has a field of the wrong type")
    return IndexManifest(**data)


def load_version(root: Path, version: str) -> LoadedIndex:
    """Load and fully validate one version.

    Raises:
        PriestIndexError: If the version is missing, partly written, corrupt, or its
            files disagree with each other.
    """
    _check_name(version)
    directory = root / _VERSIONS_DIR / version
    if not directory.is_dir():
        raise PriestIndexError("index version does not exist")
    manifest = _read_manifest(directory / _MANIFEST)
    if manifest.version != version:
        raise PriestIndexError("index manifest names a different version")
    chunks = _read_chunks(directory / _CHUNKS)
    vectors = _check_shapes(len(chunks), _read_vectors(directory / _VECTORS), manifest)
    return LoadedIndex(chunks=chunks, vectors=vectors, manifest=manifest)


def _write_file(path: Path, write: Any) -> None:
    with path.open("wb") as handle:
        write(handle)
        handle.flush()
        os.fsync(handle.fileno())


def _make_staging(target: Path) -> Path:
    """A fresh hidden directory beside *target*; refuses if *target* already exists."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise PriestIndexError(
                "index version already exists; versions are immutable"
            )
        return Path(tempfile.mkdtemp(prefix=".tmp-", dir=target.parent))
    except OSError as exc:
        raise PriestIndexError("index directory cannot be written") from exc


def _write_files(
    staging: Path,
    chunks: Sequence[Chunk],
    vectors: npt.NDArray[np.float32],
    manifest: IndexManifest,
) -> None:
    """Write the three files of a version into *staging*, flushed to disk."""
    rows = "".join(
        json.dumps(_chunk_to_row(c), ensure_ascii=False) + "\n" for c in chunks
    )
    body = json.dumps(dataclasses.asdict(manifest), ensure_ascii=False, indent=2)
    _write_file(staging / _CHUNKS, lambda h: h.write(rows.encode("utf-8")))
    _write_file(staging / _VECTORS, lambda h: np.save(h, vectors, allow_pickle=False))
    _write_file(staging / _MANIFEST, lambda h: h.write(body.encode("utf-8")))


def write_version(
    root: Path,
    chunks: Sequence[Chunk],
    vectors: npt.NDArray[np.float32],
    manifest: IndexManifest,
) -> Path:
    """Write a new immutable version and return its directory.

    Args:
        root: The index root.
        chunks: Chunks in row order.
        vectors: A float32 ``(len(chunks), manifest.dim)`` matrix of unit vectors.
        manifest: The build facts; ``manifest.version`` names the directory.

    Raises:
        PriestIndexError: If the inputs disagree, the version already exists, or the
            files cannot be written. Nothing is left behind in any of those cases.
    """
    _check_name(manifest.version)
    _check_shapes(len(chunks), vectors, manifest)
    target = root / _VERSIONS_DIR / manifest.version
    staging = _make_staging(target)
    try:
        _write_files(staging, chunks, vectors, manifest)
        os.replace(staging, target)
    except OSError as exc:
        raise PriestIndexError("index version could not be written") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _read_pointer(root: Path) -> str:
    """The version ``ACTIVE`` names; raises if there is no pointer or it is nonsense."""
    try:
        version = (root / _POINTER).read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise PriestIndexError("there is no active index") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise PriestIndexError("the active index pointer cannot be read") from exc
    _check_name(version)
    return version


def activate(root: Path, version: str) -> None:
    """Point ``ACTIVE`` at *version*, but only once it loads cleanly.

    Raises:
        PriestIndexError: If the version does not load. ``ACTIVE`` is left as it was.
    """
    load_version(root, version)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=root, prefix=".ACTIVE.", delete=False
        ) as handle:
            handle.write(version + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, root / _POINTER)
    except OSError as exc:
        raise PriestIndexError("the active index pointer could not be written") from exc


def list_versions(root: Path) -> list[str]:
    """Version names under *root*, oldest first (names start with a timestamp)."""
    versions = root / _VERSIONS_DIR
    if not versions.is_dir():
        return []
    return sorted(
        p.name
        for p in versions.iterdir()
        if p.is_dir() and _VERSION_RE.fullmatch(p.name)
    )


def prune(root: Path, keep: int = _DEFAULT_KEEP) -> list[str]:
    """Delete all but the newest *keep* versions, never the active one.

    Returns:
        The versions removed.

    Raises:
        ValueError: If *keep* is below one.
        PriestIndexError: If ``ACTIVE`` exists but is unreadable, since then it is
            unknown what must be kept.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    versions = list_versions(root)
    protected = set(versions[-keep:])
    if (root / _POINTER).exists():
        protected.add(_read_pointer(root))
    removed = []
    for version in versions:
        if version in protected:
            continue
        try:
            shutil.rmtree(root / _VERSIONS_DIR / version)
        except OSError:
            logger.warning("could not remove an old index version")
            continue
        removed.append(version)
    return removed


_Signature = tuple[int, int, int, int]


class ActiveIndex:
    """Serves whichever version ``ACTIVE`` names, reloading when it changes.

    ``get`` stats the pointer on every call. The signature includes ctime and inode,
    so a pointer rewritten in place with the same mtime is still noticed.
    """

    def __init__(self, root: Path) -> None:
        """Create a loader over the index at *root*; nothing is read until ``get``."""
        self._root = root
        self._lock = threading.Lock()
        self._state: tuple[_Signature, LoadedIndex] | None = None

    def _signature(self) -> _Signature:
        try:
            st = os.stat(self._root / _POINTER)
        except FileNotFoundError as exc:
            raise PriestIndexError("there is no active index") from exc
        except OSError as exc:
            raise PriestIndexError("the active index pointer cannot be read") from exc
        return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)

    def get(self) -> LoadedIndex:
        """The active index, loaded or reloaded as needed.

        Raises:
            PriestIndexError: If there is no ``ACTIVE``, or the version it names is
                missing or corrupt. A failed reload keeps failing; it never falls
                back to the previous version.
        """
        signature = self._signature()
        state = self._state
        if state is not None and state[0] == signature:
            return state[1]
        with self._lock:
            state = self._state
            if state is not None and state[0] == signature:
                return state[1]
            version = _read_pointer(self._root)
            if state is not None and state[1].manifest.version == version:
                loaded = state[1]
            else:
                loaded = load_version(self._root, version)
            self._state = (signature, loaded)
            return loaded

    def check_digest(self, current: EmbedModelInfo) -> None:
        """Refuse to serve an index built with a different embedding model.

        Raises:
            PriestIndexError: If the manifest's digest or dimension differs from
                *current*, or there is no usable active index.
        """
        manifest = self.get().manifest
        if manifest.embed_digest != current.digest:
            raise PriestIndexError(
                "embedding model digest differs from the one the index was built with"
            )
        if manifest.dim != current.dim:
            raise PriestIndexError(
                "embedding model dimension differs from the one the index was built with"
            )
