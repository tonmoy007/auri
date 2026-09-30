"""Build the priest-mode index from the study vault: ``python -m app.priest.index_builder``.

Pipeline: take the build lock, read the vault through the shared rules, clean and chunk
every kept note, embed the chunks, check the result, write it as a new immutable
version, and only then point ``ACTIVE`` at it. Every step before that last one can fail
without touching what is being served.

The build is incremental. The active manifest's note hashes say which notes are
unchanged; their chunks keep their old vectors. Changed chunks go through the
embedder, whose content-hash cache (``embed_cache``) spares any text it has seen.

Privacy: a status, a manifest, a log line and the command's output carry counts and
fixed error codes only. An exception is logged by class name, never by message,
because a parser error can quote the note it choked on.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import shutil
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol, TypeVar

import numpy as np

from app.config import settings
from app.exceptions import PriestError, PriestIndexError
from app.priest import build_status, index_store, priest_config
from app.priest.build_status import BuildState, BuildStatus
from app.priest.chunker import CHUNKER_VERSION, chunk_note
from app.priest.embedder import EmbedModelInfo, OllamaEmbedder, Vectors
from app.priest.index_store import ActiveIndex, IndexManifest, LoadedIndex
from app.priest.types import Chunk
from app.priest.vault_cleaner import CLEANER_VERSION, clean_note, link_targets
from app.priest.vault_rules import VaultNote, iter_vault_notes

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, int], None]
_T = TypeVar("_T")

KEEP_VERSIONS: Final = 3
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_RUNNING: Final = 3
_EMBED_SLICE: Final = 256
_VERSIONS_DIR: Final = (
    "versions"  # the layout is index_store's; the builder only sweeps it
)
_STALE_PREFIXES: Final = (".tmp-", ".ACTIVE.")
_NO_CONTENT: Final = "no_content"
_UNPROCESSABLE: Final = "unprocessable"
_ALREADY_RUNNING: Final = "already_running"
# Anything a pipeline bug could plausibly raise; reported as "unexpected", by class only.
_UNEXPECTED: Final = (
    OSError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    ArithmeticError,
    RuntimeError,
    PriestError,
)


class IndexBuildError(PriestError):
    """A build step failed; ``code`` is the fixed error code recorded in the status."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BuildEmbedder(Protocol):
    """The part of ``OllamaEmbedder`` a build needs."""

    async def model_info(self) -> EmbedModelInfo:
        """Name, digest and dimension of the embedding model."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> Vectors:
        """Embed library passages as unit vectors, in order."""
        ...

    async def embed_query(self, text: str) -> Vectors:
        """Embed a query as a unit vector."""
        ...


@dataclass(frozen=True)
class _Corpus:
    """Every chunk to index, with the hash of the note each came from."""

    chunks: list[Chunk]
    note_hashes: dict[str, str]
    unresolved_links: int
    unclosed_fences: int = 0


def _sweep_stale(index_dir: Path) -> None:
    """Remove staging leftovers of a crashed build; only called while holding the lock."""
    for parent in (index_dir, index_dir / _VERSIONS_DIR):
        if not parent.is_dir():
            continue
        for entry in parent.iterdir():
            if not entry.name.startswith(_STALE_PREFIXES):
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def _previous_index(index_dir: Path) -> LoadedIndex | None:
    """The active index, or ``None`` if there is none or it cannot be loaded."""
    try:
        return ActiveIndex(index_dir).get()
    except PriestIndexError:
        return None


def _reusable_rows(
    previous: LoadedIndex | None, note_hashes: dict[str, str], info: EmbedModelInfo
) -> dict[str, int]:
    """Map chunk id to its row in the old vectors, for chunks of unchanged notes.

    Nothing is reusable when the model, its dimension, or the cleaner or chunker
    version differ: the old vectors then describe different text or a different space.
    """
    if previous is None:
        return {}
    old = previous.manifest
    same_setup = (old.embed_digest, old.dim, old.cleaner_version, old.chunker_version)
    if same_setup != (info.digest, info.dim, CLEANER_VERSION, CHUNKER_VERSION):
        return {}
    unchanged = {p for p, h in note_hashes.items() if old.note_hashes.get(p) == h}
    return {
        c.chunk_id: row
        for row, c in enumerate(previous.chunks)
        if c.note_path in unchanged
    }


def _version_name(started: datetime, note_hashes: dict[str, str], digest: str) -> str:
    """``YYYYMMDDTHHMMSSZ-<sha8>``; the suffix changes with notes, tools and model."""
    parts = [*sorted(note_hashes.values()), CLEANER_VERSION, CHUNKER_VERSION, digest]
    sha8 = hashlib.sha256("\n".join(parts).encode()).hexdigest()[:8]
    return f"{started.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{sha8}"


class _Build:
    """One build run: its counters, and the steps in order."""

    def __init__(
        self,
        vault_dir: Path,
        index_dir: Path,
        embedder: BuildEmbedder,
        now: Callable[[], datetime],
        progress: Progress | None,
    ) -> None:
        self._vault = vault_dir
        self._index = index_dir
        self._embedder = embedder
        self._now = now
        self._progress = progress
        self._started = now()
        self._total = 0
        self._exclusions: Counter[str] = Counter()
        self._unclosed_fences = 0

    def _report(self, stage: str, done: int, total: int) -> None:
        if self._progress is not None:
            self._progress(stage, done, total)

    def _status(self, state: BuildState, **fields: object) -> BuildStatus:
        """A status for *state*, with the counts known so far and any overrides."""
        base = BuildStatus(
            state=state,
            started_at=self._started.isoformat(),
            pid=os.getpid(),
            notes_total=self._total,
            exclusions=dict(self._exclusions),
        )
        return replace(base, **fields)  # type: ignore[arg-type]

    def _fail(self, code: str) -> BuildStatus:
        logger.warning("index build failed (%s)", code)
        status = self._status(
            BuildState.failed, finished_at=self._now().isoformat(), error_code=code
        )
        build_status.write(self._index, status)
        return status

    async def run(self) -> BuildStatus:
        """Run the build and return its final status, written to disk as well."""
        build_status.write(self._index, self._status(BuildState.running))
        _sweep_stale(self._index)
        try:
            return await self._execute()
        except IndexBuildError as exc:
            return self._fail(exc.code)
        except _UNEXPECTED as exc:
            logger.warning("index build hit %s", type(exc).__name__)
            return self._fail("unexpected")

    async def _execute(self) -> BuildStatus:
        if not self._vault.is_dir():
            raise IndexBuildError("vault_missing")
        corpus = self._read_corpus()
        if not corpus.chunks:
            raise IndexBuildError("no_chunks")
        info = await self._embedder_call(self._embedder.model_info())
        previous = _previous_index(self._index)
        reuse = _reusable_rows(previous, corpus.note_hashes, info)
        vectors = await self._embed(corpus.chunks, previous, reuse, info)
        manifest = self._manifest(corpus, info)
        await self._validate(corpus.chunks, vectors, info)
        self._publish(manifest, corpus.chunks, vectors)
        status = self._status(
            BuildState.succeeded,
            finished_at=self._now().isoformat(),
            notes_indexed=len(corpus.note_hashes),
            chunks=len(corpus.chunks),
        )
        build_status.write(self._index, status)
        logger.info(
            "index build finished: %d chunks, %s", status.chunks, manifest.version
        )
        return status

    def _read_corpus(self) -> _Corpus:
        """Read, clean and chunk the vault; count what is left out, per reason."""
        notes = list(iter_vault_notes(self._vault))
        self._total = len(notes)
        targets = link_targets(n.rel_path for n in notes)
        chunks: list[Chunk] = []
        hashes: dict[str, str] = {}
        unresolved = 0
        for note in notes:
            found, links = self._chunks_of(note, targets)
            unresolved += links
            if found:
                chunks.extend(found)
                hashes[note.rel_path] = note.sha256
        build_status.write(self._index, self._status(BuildState.running))
        self._report("notes", len(hashes), self._total)
        return _Corpus(chunks, hashes, unresolved, self._unclosed_fences)

    def _chunks_of(
        self, note: VaultNote, targets: frozenset[str]
    ) -> tuple[list[Chunk], int]:
        """The chunks of one note and its unresolved-link count; an excluded note has none."""
        if note.exclusion_reason:
            self._exclusions[note.exclusion_reason] += 1
            return [], 0
        try:
            cleaned = clean_note(note.rel_path, note.text, targets)
            chunks = chunk_note(cleaned)
        except (RecursionError, MemoryError):
            # One hostile note must not take the whole build down with it.
            self._exclusions[_UNPROCESSABLE] += 1
            return [], 0
        self._unclosed_fences += cleaned.unclosed_fence
        if not chunks:
            self._exclusions[_NO_CONTENT] += 1
        return chunks, cleaned.unresolved_links

    async def _embedder_call(self, awaitable: Awaitable[_T]) -> _T:
        """Await an embedder call, turning its failure into the fixed error code."""
        try:
            return await awaitable
        except PriestIndexError:
            raise IndexBuildError("embedder_unreachable") from None

    async def _embed(
        self,
        chunks: list[Chunk],
        previous: LoadedIndex | None,
        reuse: dict[str, int],
        info: EmbedModelInfo,
    ) -> Vectors:
        """Vectors for *chunks*: old ones for unchanged text, the embedder for the rest."""
        vectors = np.empty((len(chunks), info.dim), dtype=np.float32)
        missing: list[int] = []
        for row, chunk in enumerate(chunks):
            old = reuse.get(chunk.chunk_id)
            if (
                previous is not None
                and old is not None
                and previous.chunks[old].text == chunk.text
            ):
                vectors[row] = previous.vectors[old]
            else:
                missing.append(row)
        done = len(chunks) - len(missing)
        self._report("embedding", done, len(chunks))
        for start in range(0, len(missing), _EMBED_SLICE):
            rows = missing[start : start + _EMBED_SLICE]
            fresh = await self._embedder_call(
                self._embedder.embed_documents([chunks[r].text for r in rows])
            )
            vectors[rows] = fresh
            done += len(rows)
            self._report("embedding", done, len(chunks))
        logger.info(
            "index build embedded %d chunks, reused %d",
            len(missing),
            len(chunks) - len(missing),
        )
        return vectors

    def _manifest(self, corpus: _Corpus, info: EmbedModelInfo) -> IndexManifest:
        stamp = self._now()
        warnings = [
            f"{name}={count}"
            for name, count in (
                ("unresolved_links", corpus.unresolved_links),
                ("unclosed_fences", corpus.unclosed_fences),
            )
            if count
        ]
        return IndexManifest(
            version=_version_name(self._started, corpus.note_hashes, info.digest),
            created_at=stamp.isoformat(),
            embed_model=info.name,
            embed_digest=info.digest,
            dim=info.dim,
            chunk_count=len(corpus.chunks),
            note_count=len(corpus.note_hashes),
            note_hashes=dict(corpus.note_hashes),
            exclusions=dict(self._exclusions),
            unresolved_links=corpus.unresolved_links,
            cleaner_version=CLEANER_VERSION,
            chunker_version=CHUNKER_VERSION,
            warnings=warnings,
            build_seconds=(stamp - self._started).total_seconds(),
            error_code=None,
        )

    async def _validate(
        self, chunks: list[Chunk], vectors: Vectors, info: EmbedModelInfo
    ) -> None:
        """At least one chunk, one consistent dimension, and a query that finds something.

        The probe is the first note's title, a library string and not a user's words.
        """
        self._report("validating", 0, 1)
        if vectors.shape != (len(chunks), info.dim) or not np.isfinite(vectors).all():
            raise IndexBuildError("validation_failed")
        query = await self._embedder_call(
            self._embedder.embed_query(chunks[0].note_title)
        )
        if query.shape != (info.dim,):
            raise IndexBuildError("validation_failed")
        scores = vectors @ query
        if not np.isfinite(scores).all() or float(scores.max()) <= 0.0:
            raise IndexBuildError("validation_failed")

    def _publish(
        self, manifest: IndexManifest, chunks: list[Chunk], vectors: Vectors
    ) -> None:
        """Write the version, activate it, and prune; undo the write if activation fails."""
        self._report("activating", 0, 1)
        wrote = manifest.version not in index_store.list_versions(self._index)
        if wrote:
            try:
                index_store.write_version(self._index, chunks, vectors, manifest)
            except PriestIndexError:
                raise IndexBuildError("unexpected") from None
        try:
            index_store.activate(self._index, manifest.version)
        except PriestIndexError:
            if wrote:
                shutil.rmtree(
                    self._index / _VERSIONS_DIR / manifest.version, ignore_errors=True
                )
            raise IndexBuildError("validation_failed") from None
        try:
            index_store.prune(self._index, KEEP_VERSIONS)
        except PriestIndexError:
            logger.warning("index build could not prune old versions")


async def abuild_index(
    vault_dir: Path,
    index_dir: Path,
    embedder: BuildEmbedder,
    *,
    now: Callable[[], datetime],
    progress: Progress | None = None,
) -> BuildStatus:
    """Build and activate a new index version; the async core of ``build_index``.

    Args:
        vault_dir: The ``religion-study`` folder; read-only.
        index_dir: The index root; holds the lock, the status file and the versions.
        embedder: Embeds the chunks; its document cache makes reruns cheap.
        now: The clock; read at the start, for the manifest and at the end.
        progress: Called as ``(stage, done, total)`` with counts only.

    Returns:
        The final status. A failed build has a fixed ``error_code`` and leaves the
        active index as it was. ``already_running`` is returned without writing the
        status file, which belongs to the build that holds the lock.
    """
    if not build_status.acquire_lock(index_dir, pid=os.getpid()):
        logger.warning("index build refused: another build holds the lock")
        return BuildStatus(state=BuildState.failed, error_code=_ALREADY_RUNNING)
    try:
        return await _Build(vault_dir, index_dir, embedder, now, progress).run()
    finally:
        build_status.release_lock(index_dir, pid=os.getpid())


def build_index(
    vault_dir: Path,
    index_dir: Path,
    embedder: BuildEmbedder,
    *,
    now: Callable[[], datetime],
    progress: Progress | None = None,
) -> BuildStatus:
    """Synchronous wrapper around ``abuild_index`` for scripts; do not call in a loop."""
    return asyncio.run(
        abuild_index(vault_dir, index_dir, embedder, now=now, progress=progress)
    )


# ── the command line ─────────────────────────────────────────────────────


class ClosableEmbedder(BuildEmbedder, Protocol):
    """A build embedder that owns a connection to close."""

    async def aclose(self) -> None:
        """Release the connection."""
        ...


EmbedderFactory = Callable[[Path], ClosableEmbedder]


def _default_embedder(cache_dir: Path) -> OllamaEmbedder:
    """The configured Ollama embedder, caching document vectors under *cache_dir*."""
    return OllamaEmbedder(
        settings.OLLAMA_BASE_URL, priest_config.embed_model(), cache_dir=cache_dir
    )


def _summary(status: BuildStatus) -> str:
    """One line of counts and the error code; never a title, path or text."""
    exclusions = ",".join(f"{k}:{v}" for k, v in sorted(status.exclusions.items()))
    return (
        f"state={status.state.value} notes_total={status.notes_total} "
        f"notes_indexed={status.notes_indexed} chunks={status.chunks} "
        f"exclusions={exclusions or 'none'} error_code={status.error_code or 'none'}"
    )


def _refused_embedder(index_dir: Path, now: Callable[[], datetime]) -> BuildStatus:
    """Record a build that could not start because the embedder could not be made."""
    stamp = now().isoformat()
    status = BuildStatus(
        state=BuildState.failed,
        started_at=stamp,
        finished_at=stamp,
        pid=os.getpid(),
        error_code="embedder_unreachable",
    )
    if build_status.acquire_lock(index_dir, pid=os.getpid()):
        try:
            build_status.write(index_dir, status)
        finally:
            build_status.release_lock(index_dir, pid=os.getpid())
    return status


async def _run_cli(
    vault: Path, index: Path, factory: EmbedderFactory, now: Callable[[], datetime]
) -> BuildStatus:
    try:
        embedder = factory(index / "embed_cache")
    except PriestIndexError:
        return _refused_embedder(index, now)
    try:
        return await abuild_index(vault, index, embedder, now=now)
    finally:
        await embedder.aclose()


def main(
    argv: Sequence[str] | None = None,
    *,
    embedder_factory: EmbedderFactory = _default_embedder,
) -> int:
    """Build the index from ``PRIEST_VAULT_DIR`` into ``PRIEST_INDEX_DIR``.

    Returns:
        0 on success, 1 on a failed build, 3 when another build holds the lock. The
        one line printed holds counts and the error code only.
    """
    argparse.ArgumentParser(description=(main.__doc__ or "").split("\n")[0]).parse_args(
        argv
    )
    status = asyncio.run(
        _run_cli(
            Path(settings.PRIEST_VAULT_DIR),
            Path(settings.PRIEST_INDEX_DIR),
            embedder_factory,
            lambda: datetime.now(timezone.utc),
        )
    )
    sys.stdout.write(_summary(status) + "\n")
    if status.error_code == _ALREADY_RUNNING:
        return EXIT_RUNNING
    return EXIT_OK if status.state is BuildState.succeeded else EXIT_FAILED


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    raise SystemExit(main())
