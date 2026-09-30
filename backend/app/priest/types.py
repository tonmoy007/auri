"""Plain data types shared by the knowledge pipeline and the answering service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteBlock:
    """A quotation found in a note, with where the note says it is from."""

    text: str
    attribution: str | None
    narrative: bool


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage of a note, with enough context to cite it."""

    chunk_id: str
    note_path: str
    note_title: str
    heading_path: tuple[str, ...]
    obsidian_anchor: str
    note_type: str
    traditions: tuple[str, ...]
    text: str
    quote_blocks: tuple[QuoteBlock, ...]
    char_len: int


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk with how it ranked. Scores are ``None`` when that side missed it."""

    chunk: Chunk
    rank: int
    dense_score: float | None
    bm25_score: float | None
    fused_score: float


@dataclass(frozen=True)
class RetrievalResult:
    """What retrieval found for one question, and whether the library covers it."""

    chunks: tuple[RetrievedChunk, ...]
    covered: bool
    best_dense: float
    best_bm25: float
    index_version: str
