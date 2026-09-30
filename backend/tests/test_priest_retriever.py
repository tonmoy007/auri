"""Tests for the in-house BM25 index and the hybrid retriever.

The corpus is a small synthetic library with hand-made vectors, so every ranking can
be reasoned about without Ollama or the real vault. The embedder is a fake that maps
each question to a chosen vector. Notes and questions are invented; nothing is copied
from the real study vault.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
from pathlib import Path

import numpy as np
import pytest
from app.exceptions import PriestIndexError
from app.priest import retriever as retriever_module
from app.priest.bm25_index import Bm25Index, fold, tokenize
from app.priest.embedder import EmbedModelInfo
from app.priest.index_store import (
    ActiveIndex,
    IndexManifest,
    LoadedIndex,
    activate,
    write_version,
)
from app.priest.retriever import Retriever, rrf_fuse
from app.priest.types import Chunk

DIM = 64
DIGEST = "sha256:fake-digest"
V1, V2 = "20260930T000000Z-aaaa0001", "20260930T000001Z-aaaa0002"
KISA = "Kis\u0101 Gotam\u012b"
DENSE_FLOOR = 0.5
BM25_FLOOR = 2.0


# ── Synthetic corpus helpers ────────────────────────────────────────────


def axis(i: int) -> np.ndarray:
    """A one-hot unit vector."""
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def mix(*parts: tuple[int, float]) -> np.ndarray:
    """A unit vector with the given ``(axis, weight)`` components."""
    v = np.zeros(DIM, dtype=np.float32)
    for index, weight in parts:
        v[index] = weight
    return (v / np.linalg.norm(v)).astype(np.float32)


def chunk(
    i: int,
    title: str,
    text: str,
    *,
    path: str | None = None,
    traditions: tuple[str, ...] = ("buddhism",),
    headings: tuple[str, ...] = (),
) -> Chunk:
    """A synthetic chunk."""
    slug = re.sub(r"\W+", "-", title.casefold()).strip("-")
    return Chunk(
        chunk_id=f"c{i:03d}",
        note_path=path or f"notes/{slug}.md",
        note_title=title,
        heading_path=(title, *headings),
        obsidian_anchor="",
        note_type="story",
        traditions=traditions,
        text=text,
        quote_blocks=(),
        char_len=len(text),
    )


Row = tuple[Chunk, np.ndarray]


def base_corpus() -> list[Row]:
    """Twelve chunks: a three-part story, a verse note, a near-duplicate pair, filler."""
    return [
        (
            chunk(
                0,
                "Harvest Calendar",
                "sowing reaping seasons of the plain",
                traditions=("zoroastrianism",),
            ),
            axis(40),
        ),
        (
            chunk(
                1,
                "River Crossing",
                "a ferry carries travellers over the water",
                traditions=("daoism",),
            ),
            axis(41),
        ),
        (
            chunk(
                2,
                KISA,
                "A young mother carried her child from house to house asking for medicine.",
                headings=("Part one",),
            ),
            mix((52, 1.0), (10, 0.3)),
        ),
        (
            chunk(
                3,
                KISA,
                "Each household had known a death; none could give the mustard seed.",
                headings=("Part two",),
            ),
            mix((52, 1.0), (11, 0.3)),
        ),
        (
            chunk(
                4,
                KISA,
                "She laid the child down and asked to join the community.",
                headings=("Part three",),
            ),
            mix((52, 1.0), (12, 0.3)),
        ),
        (
            chunk(
                5,
                "Throne Verse",
                "A commentary on the verse numbered 2:255 and its reception.",
                traditions=("islam",),
            ),
            axis(54),
        ),
        (
            chunk(
                6,
                "Aša",
                "Order and truth as a cosmic principle in the old hymns.",
                traditions=("zoroastrianism",),
                path="concepts/asa.md",
            ),
            axis(56),
        ),
        (
            chunk(
                7,
                "Asha",
                "Order and truth as a cosmic principle in the old hymns.",
                traditions=("zoroastrianism",),
                path="concepts/asha.md",
            ),
            mix((56, 1.0), (57, 0.1)),
        ),
        (
            chunk(
                8,
                "Mountain Hermit",
                "a hermit keeps to the quiet peaks",
                traditions=("daoism",),
            ),
            axis(42),
        ),
        (
            chunk(
                9,
                "Dinner Etiquette",
                "rules for sharing a meal with elders",
                traditions=("confucianism",),
            ),
            axis(43),
        ),
        (
            chunk(
                10,
                "Desert Fast",
                "a month of fasting from dawn to dusk",
                traditions=("islam",),
            ),
            axis(44),
        ),
        (
            chunk(
                11,
                "Bridge Story",
                "a bridge that narrows for the unjust",
                traditions=("zoroastrianism",),
            ),
            axis(45),
        ),
    ]


def build_active(
    tmp_path: Path, rows: list[Row], *, digest: str = DIGEST, version: str = V1
) -> ActiveIndex:
    """Write *rows* as a real index version, activate it and return the loader."""
    chunks = [c for c, _ in rows]
    vectors = np.stack([v for _, v in rows]).astype(np.float32)
    manifest = IndexManifest(
        version=version,
        created_at="2026-09-30T00:00:00Z",
        embed_model="nomic-embed-text",
        embed_digest=digest,
        dim=DIM,
        chunk_count=len(chunks),
        note_count=len({c.note_path for c in chunks}),
        note_hashes={},
        exclusions={},
        unresolved_links=0,
        cleaner_version="c1",
        chunker_version="k1",
        warnings=[],
        build_seconds=0.0,
        error_code=None,
    )
    write_version(tmp_path, chunks, vectors, manifest)
    activate(tmp_path, version)
    return ActiveIndex(tmp_path)


class FakeEmbedder:
    """Maps a question to a chosen vector; anything else gets an unused axis."""

    def __init__(
        self, table: dict[str, np.ndarray] | None = None, *, digest: str = DIGEST
    ) -> None:
        self.table = table or {}
        self.digest = digest
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> np.ndarray:
        self.queries.append(text)
        return self.table.get(text, axis(DIM - 1))

    async def model_info(self) -> EmbedModelInfo:
        return EmbedModelInfo(name="nomic-embed-text", digest=self.digest, dim=DIM)


def make_retriever(
    active: ActiveIndex,
    embedder: FakeEmbedder | None = None,
    *,
    top_k: int = 6,
    dense_floor: float = DENSE_FLOOR,
    bm25_floor: float = BM25_FLOOR,
) -> Retriever:
    return Retriever(
        active,
        embedder or FakeEmbedder(),
        dense_floor=dense_floor,
        bm25_floor=bm25_floor,
        top_k=top_k,
    )


def titles(result: object) -> list[str]:
    return [r.chunk.note_title for r in result.chunks]  # type: ignore[attr-defined]


# ── Folding and tokenising ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Kisā Gotamī", "kisa gotami"),
        ("Aša", "asha"),
        ("Činvat", "chinvat"),
        ("Ṛgveda", "rigveda"),
        ("Śiva", "shiva"),
        ("CAFÉ", "cafe"),
        ("Straße", "strasse"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_fold_strips_marks_and_case(raw: str, folded: str) -> None:
    # Act / Assert
    assert fold(raw) == folded


@pytest.mark.parametrize(
    ("written", "indexed"),
    [
        ("Qur'an", "Quran"),
        ("Qur\u02beān", "Quran"),
        ("Qur\u2019an", "Qur'an"),
        ("\u02bf\u0100\u02bfisha", "Aisha"),
        ("Aša", "Asha"),
        ("Činvat", "Chinvat"),
        ("Ṛgveda", "Rigveda"),
        ("Śiva", "Shiva"),
        ("Zarathustra's", "Zarathustra"),
    ],
)
def test_bm25_matches_transliteration_variants_either_way(
    written: str, indexed: str
) -> None:
    # Arrange
    for in_note, in_question in ((written, indexed), (indexed, written)):
        index = Bm25Index(_chunks(f"the story of {in_note} in brief", "other words"))

        # Act
        hits = index.search(f"tell me about {in_question}", k=3)

        # Assert
        assert [row for row, _ in hits] == [0], (in_note, in_question)


def test_fold_is_the_same_for_composed_and_decomposed_digraph_letters() -> None:
    # Arrange: š as one code point, and as s plus a combining caron
    assert fold("A\u0161a") == fold("As\u030ca") == "asha"


def test_fold_handles_text_already_in_decomposed_form() -> None:
    # Arrange
    decomposed = "Kisā Gotamī"

    # Act / Assert
    assert fold(decomposed) == "kisa gotami"


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("Kisā Gotamī, the mother!", ["kisa", "gotami", "the", "mother"]),
        ("See 2:255 today", ["see", "2:255", "today"]),
        ("chapter 30.3 says", ["chapter", "30.3", "says"]),
        ("Genesis 1:1-3", ["genesis", "1:1", "3"]),
        ("in 2023. Then", ["in", "2023", "then"]),
        ("snake_case and o'clock", ["snake", "case", "and", "oclock"]),
        ("Qur'an, Qur\u2019an and Qur\u02beān", ["quran", "quran", "and", "quran"]),
        (
            "Zarathustra's hymns, Aisha\u2019s story",
            ["zarathustra", "hymns", "aisha", "story"],
        ),
        ("\u02bf\u0100\u02bfisha", ["aisha"]),
        ("", []),
        ("...", []),
    ],
)
def test_tokenize_keeps_verse_references_whole(text: str, tokens: list[str]) -> None:
    # Act / Assert
    assert tokenize(text) == tokens


# ── BM25 ────────────────────────────────────────────────────────────────


def _chunks(*texts: str, title: str = "T") -> list[Chunk]:
    return [chunk(i, f"{title}{i}", t) for i, t in enumerate(texts)]


def test_bm25_ranks_the_chunk_with_the_term_first() -> None:
    # Arrange
    index = Bm25Index(_chunks("apples and pears", "oranges only", "pears pears pears"))

    # Act
    hits = index.search("pears", k=5)

    # Assert
    assert [row for row, _ in hits] == [2, 0]
    assert all(score > 0 for _, score in hits)


def test_bm25_matches_through_diacritics_and_case() -> None:
    # Arrange
    index = Bm25Index(_chunks("the story of Kisā Gotamī", "something else"))

    # Act
    hits = index.search("KISA gotami", k=3)

    # Assert
    assert [row for row, _ in hits] == [0]


def test_bm25_matches_a_verse_reference_as_one_token() -> None:
    # Arrange
    index = Bm25Index(
        _chunks("see 2:255 here", "see 2 and 255 separately", "30.3 is elsewhere")
    )

    # Act / Assert
    assert [row for row, _ in index.search("2:255", k=3)] == [0]
    assert [row for row, _ in index.search("30.3", k=3)] == [2]


def test_bm25_finds_a_chunk_through_its_note_title_and_headings() -> None:
    # Arrange
    rows = [
        chunk(0, "Mustard Seed", "a quiet passage", headings=("The Journey",)),
        chunk(1, "Other", "another passage"),
    ]
    index = Bm25Index(rows)

    # Act / Assert
    assert [r for r, _ in index.search("mustard", k=3)] == [0]
    assert [r for r, _ in index.search("journey", k=3)] == [0]


def test_a_rare_term_outweighs_a_common_one() -> None:
    # Arrange
    index = Bm25Index(
        _chunks(
            "the common word", "the common word", "the common word rare", "the common"
        )
    )

    # Act
    rare = index.search("rare", k=1)[0][1]
    common = index.search("common", k=1)[0][1]

    # Assert
    assert rare > common


def test_a_shorter_chunk_scores_higher_for_the_same_term_count() -> None:
    # Arrange
    filler = " ".join(["padding"] * 40)
    index = Bm25Index(_chunks(f"target {filler}", "target short", "unrelated words"))

    # Act
    hits = index.search("target", k=2)

    # Assert
    assert [row for row, _ in hits] == [1, 0]


def test_bm25_respects_k_and_the_allowed_rows() -> None:
    # Arrange
    index = Bm25Index(_chunks("apple", "apple apple", "apple apple apple", "pear"))

    # Act
    top_one = index.search("apple", k=1)
    restricted = index.search("apple", k=5, allowed=frozenset({0, 3}))

    # Assert
    assert [row for row, _ in top_one] == [2]
    assert [row for row, _ in restricted] == [0]


def test_bm25_ties_are_broken_by_row_order() -> None:
    # Arrange
    index = Bm25Index(_chunks("same words", "same words", "same words"))

    # Act
    hits = index.search("words", k=3)

    # Assert
    assert [row for row, _ in hits] == [0, 1, 2]
    assert len({score for _, score in hits}) == 1


def test_bm25_ignores_stop_words_in_the_question() -> None:
    # Arrange
    index = Bm25Index(_chunks("how to fix a tap", "a story about grief"))

    # Act
    only_stop_words = index.search("how do I a", k=3)
    with_content = index.search("how do I grieve over grief", k=3)

    # Assert
    assert only_stop_words == []
    assert [row for row, _ in with_content] == [1]


def test_bm25_over_nothing_finds_nothing() -> None:
    # Act / Assert
    assert Bm25Index([]).search("anything", k=3) == []
    assert Bm25Index(_chunks("a")).search("", k=3) == []
    assert Bm25Index(_chunks("apple")).search("apple", k=0) == []


def test_coverage_score_sums_only_the_best_matching_terms() -> None:
    # Arrange
    index = Bm25Index(
        _chunks("alpha beta gamma delta epsilon zeta", "unrelated words here")
    )
    query = "alpha beta gamma delta epsilon zeta"

    # Act
    everything = index.search(query, k=1)[0][1]
    capped = index.coverage_score(query, [0, 1], max_terms=2)
    uncapped = index.coverage_score(query, [0, 1], max_terms=10)

    # Assert
    assert uncapped == pytest.approx(everything)
    assert 0 < capped < uncapped


def test_coverage_score_only_looks_at_the_given_rows() -> None:
    # Arrange
    index = Bm25Index(_chunks("apple pear", "apple", "pear"))

    # Act / Assert
    assert index.coverage_score("apple", [2], max_terms=4) == 0.0
    assert index.coverage_score("apple", [1], max_terms=4) > 0.0
    assert index.coverage_score("apple", [], max_terms=4) == 0.0
    assert index.coverage_score("how do I", [0], max_terms=4) == 0.0


# ── Reciprocal rank fusion ──────────────────────────────────────────────


def test_fusion_scores_follow_the_rrf_formula() -> None:
    # Arrange
    dense = [(1, 0.9), (2, 0.8)]
    bm25 = [(2, 5.0), (3, 4.0)]

    # Act
    fused = dict(rrf_fuse(dense, bm25))

    # Assert
    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 62)


def test_fusion_orders_ties_by_row_and_does_not_depend_on_input_order() -> None:
    # Arrange: row 5 is only dense rank 1, row 2 is only BM25 rank 1, so they tie
    dense = [(5, 0.9), (7, 0.1)]
    bm25 = [(2, 3.0), (9, 1.0)]

    # Act
    first = rrf_fuse(dense, bm25)
    second = rrf_fuse(list(dense), list(bm25))

    # Assert
    assert [row for row, _ in first] == [2, 5, 7, 9]
    assert first == second


# ── Retrieval ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_name_with_no_diacritics_finds_the_note_that_has_them(
    tmp_path: Path,
) -> None:
    # Arrange: the dense side knows nothing about this question
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    result = await retriever.retrieve("Kisa Gotami", None)

    # Assert
    assert result.chunks[0].chunk.note_title == KISA
    assert result.chunks[0].bm25_score is not None and result.chunks[0].bm25_score > 0
    assert result.best_dense == pytest.approx(0.0)
    assert result.covered is True


@pytest.mark.asyncio
async def test_a_verse_reference_matches_its_chunk(tmp_path: Path) -> None:
    # Arrange
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    result = await retriever.retrieve("2:255", None)

    # Assert
    assert result.chunks[0].chunk.note_title == "Throne Verse"
    assert result.chunks[0].bm25_score is not None


@pytest.mark.asyncio
async def test_a_paraphrased_question_is_found_by_meaning(tmp_path: Path) -> None:
    # Arrange
    question = "accepting that everyone eventually loses someone"
    embedder = FakeEmbedder({question: axis(52)})
    retriever = make_retriever(build_active(tmp_path, base_corpus()), embedder)

    # Act
    result = await retriever.retrieve(question, None)

    # Assert
    assert result.chunks[0].chunk.note_title == KISA
    assert result.chunks[0].bm25_score is None
    assert result.best_dense > 0.9 and result.covered is True


@pytest.mark.asyncio
async def test_at_most_two_chunks_of_one_note_are_returned(tmp_path: Path) -> None:
    # Arrange
    question = "grief"
    embedder = FakeEmbedder({question: axis(52)})
    retriever = make_retriever(build_active(tmp_path, base_corpus()), embedder, top_k=8)

    # Act
    result = await retriever.retrieve(question, None)

    # Assert: three chunks of the story are near the question, two survive
    story = [r for r in result.chunks if r.chunk.note_title == KISA]
    assert len(story) == 2


@pytest.mark.asyncio
async def test_a_near_duplicate_pair_collapses_to_one(tmp_path: Path) -> None:
    # Arrange: Aša and Asha share text and have cosine 0.995
    question = "what is order and truth"
    embedder = FakeEmbedder({question: axis(56)})
    retriever = make_retriever(build_active(tmp_path, base_corpus()), embedder, top_k=6)

    # Act
    result = await retriever.retrieve(question, None)

    # Assert
    found = [t for t in titles(result) if t in {"Aša", "Asha"}]
    assert len(found) == 1
    assert len(result.chunks) == 6


@pytest.mark.asyncio
async def test_two_notes_with_the_same_folded_title_in_one_folder_collapse(
    tmp_path: Path,
) -> None:
    # Arrange: different files, orthogonal vectors, titles equal once folded
    rows = [
        (chunk(0, KISA, "first telling", path="stories/a.md"), axis(1)),
        (chunk(1, "Kisa Gotami", "second telling", path="stories/b.md"), axis(2)),
        (chunk(2, "Unrelated", "nothing to see", path="stories/c.md"), axis(3)),
    ]
    retriever = make_retriever(build_active(tmp_path, rows))

    # Act
    result = await retriever.retrieve("telling", None)

    # Assert
    assert len([t for t in titles(result) if t.startswith("Kis")]) == 1


@pytest.mark.asyncio
async def test_a_transliterated_title_collapses_through_the_shared_fold(
    tmp_path: Path,
) -> None:
    # Arrange: Aša and Asha are the same title once folded, with unrelated vectors
    rows = [
        (chunk(0, "Aša", "first telling", path="concepts/a.md"), axis(1)),
        (chunk(1, "Asha", "second telling", path="concepts/b.md"), axis(2)),
        (chunk(2, "Unrelated", "nothing to see", path="concepts/c.md"), axis(3)),
    ]
    retriever = make_retriever(build_active(tmp_path, rows))

    # Act
    result = await retriever.retrieve("telling", None)

    # Assert
    assert len([t for t in titles(result) if t in {"Aša", "Asha"}]) == 1


@pytest.mark.asyncio
async def test_same_titled_notes_in_different_folders_are_both_kept(
    tmp_path: Path,
) -> None:
    # Arrange: a figure note and a story note that share a name, unrelated vectors
    rows = [
        (chunk(0, "Overview", "the person", path="figures/overview.md"), axis(1)),
        (chunk(1, "Overview", "the tale", path="stories/overview.md"), axis(2)),
    ]
    retriever = make_retriever(build_active(tmp_path, rows))

    # Act
    result = await retriever.retrieve("person tale", None)

    # Assert
    assert sorted(r.chunk.note_path for r in result.chunks) == [
        "figures/overview.md",
        "stories/overview.md",
    ]


@pytest.mark.asyncio
async def test_same_titled_notes_in_different_folders_collapse_when_also_similar(
    tmp_path: Path,
) -> None:
    # Arrange: same title, different folders, cosine 0.9 (above the title-twin bar)
    rows = [
        (chunk(0, "Overview", "the person", path="figures/overview.md"), axis(1)),
        (
            chunk(1, "Overview", "the tale", path="stories/overview.md"),
            mix((1, 1.0), (2, 0.4)),
        ),
    ]
    retriever = make_retriever(build_active(tmp_path, rows))

    # Act
    result = await retriever.retrieve("person tale", None)

    # Assert
    assert len(result.chunks) == 1


@pytest.mark.asyncio
async def test_the_tradition_filter_excludes_other_traditions(tmp_path: Path) -> None:
    # Arrange
    retriever = make_retriever(build_active(tmp_path, base_corpus()), top_k=12)

    # Act
    result = await retriever.retrieve("Kisa Gotami", frozenset({"islam"}))

    # Assert: the Buddhist story is excluded and nothing in the filtered set matches
    assert all("islam" in r.chunk.traditions for r in result.chunks)
    assert KISA not in titles(result)
    assert result.covered is False and result.best_bm25 == 0.0


@pytest.mark.asyncio
async def test_the_filter_is_applied_before_fusion_not_after(tmp_path: Path) -> None:
    # Arrange: 40 Buddhist chunks fill the dense top 30; two Islamic chunks sit far below
    question = "grief"
    rows: list[Row] = [
        (
            chunk(i, f"Buddhist Note {i}", f"teaching number {i}"),
            mix((0, 1.0), (1 + i, 1.5)),
        )
        for i in range(40)
    ]
    rows.append((chunk(40, "Fast One", "a fast", traditions=("islam",)), axis(50)))
    rows.append(
        (chunk(41, "Fast Two", "another fast", traditions=("islam",)), axis(51))
    )
    retriever = make_retriever(
        build_active(tmp_path, rows), FakeEmbedder({question: axis(0)}), top_k=6
    )

    # Act
    unfiltered = await retriever.retrieve(question, None)
    filtered = await retriever.retrieve(question, frozenset({"islam"}))

    # Assert
    assert set(titles(unfiltered)).isdisjoint({"Fast One", "Fast Two"})
    assert set(titles(filtered)) == {"Fast One", "Fast Two"}


@pytest.mark.asyncio
async def test_a_chunk_with_no_tradition_tag_is_left_out_when_a_filter_is_set(
    tmp_path: Path,
) -> None:
    # Arrange
    rows = [
        (chunk(0, "Untagged", "common ground", traditions=()), axis(1)),
        (chunk(1, "Tagged", "common ground", traditions=("islam",)), axis(2)),
    ]
    retriever = make_retriever(build_active(tmp_path, rows))

    # Act
    everything = await retriever.retrieve("common ground", None)
    narrowed = await retriever.retrieve("common ground", frozenset({"islam"}))

    # Assert
    assert set(titles(everything)) == {"Untagged", "Tagged"}
    assert titles(narrowed) == ["Tagged"]


@pytest.mark.asyncio
async def test_an_empty_filter_allows_nothing(tmp_path: Path) -> None:
    # Arrange
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    result = await retriever.retrieve("Kisa Gotami", frozenset())

    # Assert
    assert result.chunks == () and result.covered is False


@pytest.mark.asyncio
async def test_an_off_topic_question_is_not_covered(tmp_path: Path) -> None:
    # Arrange: orthogonal to every note, and none of its words is in the library
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    result = await retriever.retrieve("How do I fix a leaking tap?", None)

    # Assert
    assert result.covered is False
    assert result.best_dense == pytest.approx(0.0)
    assert result.best_bm25 == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dense_floor", "bm25_floor", "covered"),
    [(0.5, 99.0, True), (1.1, 0.5, True), (1.1, 99.0, False)],
)
async def test_covered_is_the_best_dense_or_the_best_bm25_reaching_its_floor(
    tmp_path: Path, dense_floor: float, bm25_floor: float, covered: bool
) -> None:
    # Arrange: dense cosine 1.0 for one chunk, and BM25 hits on "hermit"
    question = "hermit"
    embedder = FakeEmbedder({question: axis(42)})
    retriever = make_retriever(
        build_active(tmp_path, base_corpus()),
        embedder,
        dense_floor=dense_floor,
        bm25_floor=bm25_floor,
    )

    # Act
    result = await retriever.retrieve(question, None)

    # Assert
    assert result.best_dense == pytest.approx(1.0)
    assert result.best_bm25 > 0.5
    assert result.covered is covered


@pytest.mark.asyncio
async def test_coverage_is_judged_before_the_caps_drop_chunks(tmp_path: Path) -> None:
    # Arrange: row 1 matches the words and row 0 matches the meaning; they are
    # near-duplicates, so only one survives, but the best dense score is still reported
    question = "zzqx"
    rows = [
        (
            chunk(0, "Meaning Match", "plain words here", path="a.md"),
            mix((5, 1.0), (6, 0.05)),
        ),
        (chunk(1, "Word Match", "zzqx zzqx", path="b.md"), axis(5)),
    ]
    embedder = FakeEmbedder({question: mix((5, 1.0), (6, 0.05))})
    retriever = make_retriever(
        build_active(tmp_path, rows),
        embedder,
        top_k=2,
        dense_floor=0.99,
        bm25_floor=99.0,
    )

    # Act
    result = await retriever.retrieve(question, None)

    # Assert
    assert len(result.chunks) == 1
    assert result.best_dense == pytest.approx(1.0, abs=1e-5)
    assert result.covered is True


@pytest.mark.asyncio
async def test_ranks_start_at_one_and_the_result_is_limited_to_top_k(
    tmp_path: Path,
) -> None:
    # Arrange
    retriever = make_retriever(build_active(tmp_path, base_corpus()), top_k=3)

    # Act
    result = await retriever.retrieve("Kisa Gotami", None)

    # Assert
    assert [r.rank for r in result.chunks] == [1, 2, 3]
    assert all(r.fused_score > 0 for r in result.chunks)
    assert [r.fused_score for r in result.chunks] == sorted(
        (r.fused_score for r in result.chunks), reverse=True
    )
    assert result.index_version == V1


@pytest.mark.asyncio
async def test_a_side_that_missed_a_chunk_reports_none_for_its_score(
    tmp_path: Path,
) -> None:
    # Arrange: 40 chunks, so the dense top 30 leaves out the one BM25 picks
    rows: list[Row] = [
        (chunk(i, f"Plain Note {i}", f"ordinary text {i}"), mix((0, 1.0), (1 + i, 1.5)))
        for i in range(40)
    ]
    rows.append(
        (chunk(40, "Rare Name", "xylophonist", traditions=("daoism",)), axis(60))
    )
    retriever = make_retriever(
        build_active(tmp_path, rows), FakeEmbedder({"xylophonist": axis(0)}), top_k=12
    )

    # Act
    result = await retriever.retrieve("xylophonist", None)

    # Assert
    hit = next(r for r in result.chunks if r.chunk.note_title == "Rare Name")
    assert hit.dense_score is None and hit.bm25_score is not None


@pytest.mark.asyncio
async def test_the_same_question_gives_the_same_answer_every_time(
    tmp_path: Path,
) -> None:
    # Arrange
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    first = await retriever.retrieve("Kisa Gotami", None)
    second = await retriever.retrieve("Kisa Gotami", None)

    # Assert
    assert first == second


# ── Coverage decision ───────────────────────────────────────────────────


def _filler_rows(count: int, *, start: int = 20) -> list[Row]:
    """Chunks that share no word with the coverage tests' questions."""
    return [
        (chunk(start + i, f"Filler {i}", f"plain filler text number {i}"), axis(i))
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_a_long_rambling_question_does_not_cover_itself_by_adding_up_words(
    tmp_path: Path,
) -> None:
    # Arrange: one long chunk holds six moderately common words the question
    # happens to use; no single one is a real match
    words = ["river", "stone", "garden", "window", "lantern", "meadow"]
    rows = [
        (chunk(0, "Long Chunk", " ".join(words) + " and more about the day"), axis(1)),
        *[
            (
                chunk(1 + i, f"Other {i}", f"{words[i % 6]} {words[(i + 1) % 6]} text"),
                axis(2 + i),
            )
            for i in range(12)
        ],
        *_filler_rows(20),
    ]
    question = (
        "I walked by the river and a stone near the garden past a window "
        "with a lantern across the meadow, so what should I do about my boss"
    )
    raw_best = Bm25Index([c for c, _ in rows]).search(question, 1)[0][1]
    floor = raw_best * 0.9
    retriever = make_retriever(
        build_active(tmp_path, rows), bm25_floor=floor, dense_floor=1.1
    )

    # Act
    result = await retriever.retrieve(question, None)

    # Assert: the raw sum clears the floor, the length-robust score does not
    assert raw_best >= floor
    assert result.best_bm25 < floor
    assert result.covered is False


@pytest.mark.asyncio
async def test_a_short_question_with_a_rare_name_is_still_covered(
    tmp_path: Path,
) -> None:
    # Arrange: two rare words matching one chunk clear a floor a single common word
    # would not
    rows = [
        (chunk(0, "Story", "zzqx and qqvw meet"), axis(1)),
        *_filler_rows(20),
    ]
    raw_best = Bm25Index([c for c, _ in rows]).search("zzqx qqvw", 1)[0][1]
    retriever = make_retriever(
        build_active(tmp_path, rows), bm25_floor=raw_best * 0.95, dense_floor=1.1
    )

    # Act
    result = await retriever.retrieve("zzqx qqvw", None)

    # Assert
    assert result.covered is True
    assert result.best_bm25 == pytest.approx(raw_best)


@pytest.mark.asyncio
async def test_a_chunk_that_drops_out_of_the_top_k_cannot_cover_the_question(
    tmp_path: Path,
) -> None:
    # Arrange: row 0 alone holds the rare word, so BM25 ranks it first, but nine rows
    # that sit in both ranked lists outrank it once the lists are fused
    both = [
        (
            chunk(1 + i, f"Shared {i}", "river bank", path=f"s/{i}.md"),
            mix((10 + i, 1.0), (60, 0.2)),
        )
        for i in range(9)
    ]
    rows = [
        (chunk(0, "Rare Holder", "zzqx", path="s/rare.md"), axis(5)),
        *both,
        *_filler_rows(30),
    ]
    question = "zzqx river"
    # row 0 is also pushed out of the dense top 30, so only BM25 knows it
    embedder = FakeEmbedder({question: mix((60, 1.0), (5, -0.3))})
    raw_best = Bm25Index([c for c, _ in rows]).search(question, 1)[0][1]
    retriever = make_retriever(
        build_active(tmp_path, rows),
        embedder,
        top_k=6,
        bm25_floor=raw_best * 0.9,
        dense_floor=1.1,
    )

    # Act
    result = await retriever.retrieve(question, None)

    # Assert: row 0 set the raw best BM25 but is not among the six returned chunks
    assert "Rare Holder" not in titles(result)
    assert len(result.chunks) == 6
    assert result.best_bm25 < raw_best * 0.9
    assert result.covered is False


@pytest.mark.asyncio
async def test_a_chunk_that_makes_the_top_k_can_cover_the_question(
    tmp_path: Path,
) -> None:
    # Arrange: the same rare word, but the chunk is also the dense favourite
    rows = [
        (chunk(0, "Rare Holder", "zzqx", path="s/rare.md"), mix((60, 1.0))),
        *_filler_rows(10),
    ]
    question = "zzqx"
    embedder = FakeEmbedder({question: mix((60, 1.0))})
    raw_best = Bm25Index([c for c, _ in rows]).search(question, 1)[0][1]
    retriever = make_retriever(
        build_active(tmp_path, rows),
        embedder,
        bm25_floor=raw_best * 0.9,
        dense_floor=1.1,
    )

    # Act
    result = await retriever.retrieve(question, None)

    # Assert
    assert titles(result)[0] == "Rare Holder"
    assert result.covered is True


# ── Failures ────────────────────────────────────────────────────────────


class _EmptyActive:
    """Stands in for an active index whose version somehow holds no chunks."""

    def get(self) -> LoadedIndex:
        manifest = IndexManifest(
            version=V1,
            created_at="x",
            embed_model="m",
            embed_digest=DIGEST,
            dim=DIM,
            chunk_count=0,
            note_count=0,
            note_hashes={},
            exclusions={},
            unresolved_links=0,
            cleaner_version="c",
            chunker_version="k",
            warnings=[],
            build_seconds=0.0,
            error_code=None,
        )
        return LoadedIndex(
            chunks=(), vectors=np.zeros((0, DIM), dtype=np.float32), manifest=manifest
        )

    def check_digest(self, current: EmbedModelInfo) -> None:
        return None


@pytest.mark.asyncio
async def test_an_empty_index_raises(tmp_path: Path) -> None:
    # Arrange
    retriever = Retriever(
        _EmptyActive(), FakeEmbedder(), dense_floor=0.5, bm25_floor=2.0, top_k=6
    )

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await retriever.retrieve("anything", None)


@pytest.mark.asyncio
async def test_no_active_index_raises(tmp_path: Path) -> None:
    # Arrange
    retriever = make_retriever(ActiveIndex(tmp_path))

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await retriever.retrieve("anything", None)


@pytest.mark.asyncio
async def test_a_changed_model_digest_refuses_to_serve_and_embeds_nothing(
    tmp_path: Path,
) -> None:
    # Arrange
    embedder = FakeEmbedder(digest="sha256:a-different-model")
    retriever = make_retriever(build_active(tmp_path, base_corpus()), embedder)

    # Act
    with pytest.raises(PriestIndexError):
        await retriever.retrieve("Kisa Gotami", None)

    # Assert: the question never left the retriever
    assert embedder.queries == []


@pytest.mark.asyncio
async def test_a_query_vector_of_the_wrong_size_raises(tmp_path: Path) -> None:
    # Arrange
    embedder = FakeEmbedder({"short": np.ones(3, dtype=np.float32)})
    retriever = make_retriever(build_active(tmp_path, base_corpus()), embedder)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await retriever.retrieve("short", None)


def test_top_k_below_one_is_refused(tmp_path: Path) -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="top_k"):
        make_retriever(ActiveIndex(tmp_path), top_k=0)


# ── Index versions ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bm25_is_built_once_per_index_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    builds: list[int] = []
    real = retriever_module.Bm25Index

    def counting(chunks: object, *args: object, **kwargs: object) -> Bm25Index:
        builds.append(1)
        return real(chunks, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(retriever_module, "Bm25Index", counting)
    active = build_active(tmp_path, base_corpus())
    retriever = make_retriever(active)

    # Act
    await retriever.retrieve("Kisa Gotami", None)
    await retriever.retrieve("2:255", None)
    cached = len(builds)
    write_version(
        tmp_path,
        [c for c, _ in base_corpus()],
        np.stack([v for _, v in base_corpus()]).astype(np.float32),
        dataclasses.replace(active.get().manifest, version=V2),
    )
    activate(tmp_path, V2)
    result = await retriever.retrieve("Kisa Gotami", None)

    # Assert
    assert cached == 1
    assert len(builds) == 2
    assert result.index_version == V2


# ── One load per request ────────────────────────────────────────────────


class _SpyActive:
    """Wraps a real ``ActiveIndex``; records calls and which thread made them."""

    def __init__(self, inner: ActiveIndex) -> None:
        self.inner = inner
        self.gets = 0
        self.digest_checks = 0
        self.threads: list[int] = []

    def get(self) -> LoadedIndex:
        self.gets += 1
        self.threads.append(threading.get_ident())
        return self.inner.get()

    def check_digest(self, current: EmbedModelInfo) -> None:
        self.digest_checks += 1
        self.inner.check_digest(current)


@pytest.mark.asyncio
async def test_a_request_loads_the_index_once_and_checks_that_same_load(
    tmp_path: Path,
) -> None:
    # Arrange
    spy = _SpyActive(build_active(tmp_path, base_corpus()))
    retriever = Retriever(spy, FakeEmbedder(), dense_floor=0.5, bm25_floor=2.0, top_k=6)

    # Act
    await retriever.retrieve("Kisa Gotami", None)

    # Assert: one get(), and no second get() hidden inside a separate digest check
    assert spy.gets == 1
    assert spy.digest_checks == 0


@pytest.mark.asyncio
async def test_the_digest_is_checked_against_the_index_that_was_loaded(
    tmp_path: Path,
) -> None:
    # Arrange: a source whose own digest check would wrongly say yes
    inner = build_active(tmp_path, base_corpus())

    class Lying(_SpyActive):
        def check_digest(self, current: EmbedModelInfo) -> None:
            return None

    embedder = FakeEmbedder(digest="sha256:a-different-model")
    retriever = Retriever(
        Lying(inner), embedder, dense_floor=0.5, bm25_floor=2.0, top_k=6
    )

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await retriever.retrieve("Kisa Gotami", None)
    assert embedder.queries == []


@pytest.mark.asyncio
async def test_the_blocking_load_runs_off_the_event_loop(tmp_path: Path) -> None:
    # Arrange
    spy = _SpyActive(build_active(tmp_path, base_corpus()))
    retriever = Retriever(spy, FakeEmbedder(), dense_floor=0.5, bm25_floor=2.0, top_k=6)

    # Act
    await retriever.retrieve("Kisa Gotami", None)

    # Assert
    assert spy.threads and spy.threads[0] != threading.get_ident()


# ── Privacy ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_question_is_not_logged_or_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    secret = "my-manager-humiliated-me-yesterday"
    retriever = make_retriever(build_active(tmp_path, base_corpus()))

    # Act
    with caplog.at_level(logging.DEBUG):
        await retriever.retrieve(secret, None)

    # Assert
    assert secret not in caplog.text
    assert secret not in repr(vars(retriever))
