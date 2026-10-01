"""Hybrid retrieval over the active index: dense cosine plus BM25, fused with RRF.

Order of operations, and why:

1. The tradition filter runs first, on both sides. Filtering after fusion would let
   chunks from other traditions crowd the top 30 and leave a filtered question with
   nothing.
2. Each side contributes its top 30; Reciprocal Rank Fusion (k=60) merges them.
3. A cap of two chunks per note, and a collapse of near-duplicates, keep the final
   ``top_k`` varied.
4. "Covered" is the best cosine, or the best BM25 score among the chunks that are
   returned. A chunk the model never sees cannot be the reason the library is said to
   cover a question, and only a few matching terms count towards the BM25 score, so
   the length of a question does not decide it.

The question is only ever held in local variables: it is not logged or stored.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final, Protocol

import numpy as np
import numpy.typing as npt

from app.exceptions import PriestIndexError
from app.priest.bm25_index import Bm25Index, fold
from app.priest.embedder import EmbedModelInfo
from app.priest.index_store import LoadedIndex, check_manifest_digest
from app.priest.types import Chunk, RetrievalResult, RetrievedChunk

_SIDE_DEPTH: Final = 30
_RRF_K: Final = 60
_MAX_PER_NOTE: Final = 2
_DUPLICATE_COSINE: Final = 0.95
# Two same-titled notes in different folders are told apart unless they also read alike.
_TITLE_TWIN_COSINE: Final = 0.85
# How many matching terms count towards the BM25 half of the coverage decision.
_COVER_TERMS: Final = 4

Hits = Sequence[tuple[int, float]]


class QueryEmbedder(Protocol):
    """The part of ``OllamaEmbedder`` that retrieval needs."""

    async def embed_query(self, text: str) -> npt.NDArray[np.float32]:
        """Embed a question as a unit vector."""
        ...

    async def model_info(self) -> EmbedModelInfo:
        """Name, digest and dimension of the embedding model in use."""
        ...


class IndexSource(Protocol):
    """The part of ``ActiveIndex`` that retrieval needs."""

    def get(self) -> LoadedIndex:
        """The currently active index; may block while a new version loads."""
        ...


def rrf_fuse(dense: Hits, bm25: Hits) -> list[tuple[int, float]]:
    """Fuse two ranked lists of ``(row, score)`` by Reciprocal Rank Fusion.

    A row's score is the sum of ``1 / (60 + rank)`` over the lists it appears in,
    with ranks starting at 1. Ties are ordered by row, so the result never depends on
    hash or input order.
    """
    fused: dict[int, float] = {}
    for hits in (dense, bm25):
        for rank, (row, _) in enumerate(hits, start=1):
            fused[row] = fused.get(row, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(fused.items(), key=lambda item: (-item[1], item[0]))


def _allowed_rows(
    chunks: tuple[Chunk, ...], traditions: frozenset[str] | None
) -> np.ndarray:
    """Rows whose chunk is tagged with any wanted tradition; every row for ``None``."""
    if traditions is None:
        return np.arange(len(chunks), dtype=np.int64)
    keep = [i for i, c in enumerate(chunks) if traditions.intersection(c.traditions)]
    return np.array(keep, dtype=np.int64)


def _dense_hits(
    vectors: np.ndarray, query: np.ndarray, rows: np.ndarray
) -> list[tuple[int, float]]:
    """Top chunks by cosine among *rows*; ties go to the lower row."""
    if rows.size == 0:
        return []
    scores = vectors[rows] @ query
    order = np.argsort(-scores, kind="stable")[:_SIDE_DEPTH]
    return [(int(rows[i]), float(scores[i])) for i in order]


def _folder(note_path: str) -> str:
    """The directory part of a note's relative path; empty for a top-level note."""
    return note_path.rpartition("/")[0]


def _is_title_twin(
    row: int, other: int, chunks: tuple[Chunk, ...], cosine: float
) -> bool:
    """Whether two different notes are one note under two spellings of its title.

    The titles must fold to the same text (the fold the BM25 index uses). Notes in one
    folder are then twins outright; notes in different folders, such as a figure and a
    story that share a name, are twins only when their passages also read alike.
    """
    a, b = chunks[row], chunks[other]
    if a.note_path == b.note_path or fold(a.note_title) != fold(b.note_title):
        return False
    return _folder(a.note_path) == _folder(b.note_path) or cosine >= _TITLE_TWIN_COSINE


def _is_duplicate(
    row: int, kept: list[int], chunks: tuple[Chunk, ...], vectors: np.ndarray
) -> bool:
    """Whether *row* repeats a kept chunk: near-identical vector, or a title twin."""
    for other in kept:
        cosine = float(vectors[row] @ vectors[other])
        if cosine >= _DUPLICATE_COSINE or _is_title_twin(row, other, chunks, cosine):
            return True
    return False


def _select(
    fused: Hits, chunks: tuple[Chunk, ...], vectors: np.ndarray, top_k: int
) -> list[tuple[int, float]]:
    """Walk the fused order, applying the per-note cap and duplicate collapse."""
    kept: list[tuple[int, float]] = []
    per_note: dict[str, int] = {}
    for row, score in fused:
        note = chunks[row].note_path
        if per_note.get(note, 0) >= _MAX_PER_NOTE:
            continue
        if _is_duplicate(row, [r for r, _ in kept], chunks, vectors):
            continue
        kept.append((row, score))
        per_note[note] = per_note.get(note, 0) + 1
        if len(kept) == top_k:
            break
    return kept


class Retriever:
    """Finds the library passages that best answer a question."""

    def __init__(
        self,
        active: IndexSource,
        embedder: QueryEmbedder,
        *,
        dense_floor: float,
        bm25_floor: float,
        top_k: int,
    ) -> None:
        """Create a retriever.

        Args:
            active: The loader for the active index (``ActiveIndex``).
            embedder: Embeds questions; must be the model the index was built with.
            dense_floor: Best cosine at or above which the library covers a question.
            bm25_floor: Best BM25 score at or above which it does.
            top_k: How many passages to return at most.

        Raises:
            ValueError: If *top_k* is below one.
        """
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        self._active = active
        self._embedder = embedder
        self._dense_floor = dense_floor
        self._bm25_floor = bm25_floor
        self._top_k = top_k
        self._bm25: tuple[LoadedIndex, Bm25Index] | None = None
        self._bm25_lock = asyncio.Lock()

    async def retrieve(
        self, question: str, traditions: frozenset[str] | None
    ) -> RetrievalResult:
        """Retrieve passages for *question*.

        Args:
            question: The user's words. Sent only to the embedder.
            traditions: Restrict to chunks tagged with any of these; ``None`` for all.

        Raises:
            PriestIndexError: If there is no usable index, it is empty, the embedding
                model is not the one it was built with, or the query vector has the
                wrong size.
        """
        info = await self._embedder.model_info()
        # One load per request: the digest is checked against the very index that is
        # then queried, and a reload (a JSONL parse, np.load) stays off the event loop.
        loaded = await asyncio.to_thread(self._active.get)
        check_manifest_digest(loaded.manifest, info)
        if not loaded.chunks:
            raise PriestIndexError("the active index is empty")
        query = await self._embedder.embed_query(question)
        if query.shape != (loaded.vectors.shape[1],):
            raise PriestIndexError(
                "the query vector does not match the index dimension"
            )
        rows = _allowed_rows(loaded.chunks, traditions)
        bm25 = await self._bm25_for(loaded)
        allowed = None if traditions is None else frozenset(int(r) for r in rows)
        dense_hits = _dense_hits(loaded.vectors, query, rows)
        bm25_hits = bm25.search(question, _SIDE_DEPTH, allowed)
        return self._result(loaded, bm25, question, dense_hits, bm25_hits)

    def _result(
        self,
        loaded: LoadedIndex,
        bm25: Bm25Index,
        question: str,
        dense_hits: Hits,
        bm25_hits: Hits,
    ) -> RetrievalResult:
        """Fuse, cap and label the hits, and decide coverage.

        The best cosine is the best of the dense side, before the caps. The best BM25
        score is taken over the returned chunks only, on their best few terms.
        """
        best_dense = dense_hits[0][1] if dense_hits else 0.0
        dense_by_row, bm25_by_row = dict(dense_hits), dict(bm25_hits)
        chosen = _select(
            rrf_fuse(dense_hits, bm25_hits), loaded.chunks, loaded.vectors, self._top_k
        )
        chunks = tuple(
            RetrievedChunk(
                chunk=loaded.chunks[row],
                rank=rank,
                dense_score=dense_by_row.get(row),
                bm25_score=bm25_by_row.get(row),
                fused_score=fused,
            )
            for rank, (row, fused) in enumerate(chosen, start=1)
        )
        best_bm25 = bm25.coverage_score(
            question, [row for row, _ in chosen], _COVER_TERMS
        )
        return RetrievalResult(
            chunks=chunks,
            covered=best_dense >= self._dense_floor or best_bm25 >= self._bm25_floor,
            best_dense=best_dense,
            best_bm25=best_bm25,
            index_version=loaded.manifest.version,
        )

    async def _bm25_for(self, loaded: LoadedIndex) -> Bm25Index:
        """The BM25 index for *loaded*, built once per loaded version."""
        cached = self._bm25
        if cached is not None and cached[0] is loaded:
            return cached[1]
        async with self._bm25_lock:
            cached = self._bm25
            if cached is not None and cached[0] is loaded:
                return cached[1]
            built = await asyncio.to_thread(Bm25Index, loaded.chunks)
            self._bm25 = (loaded, built)
            return built
