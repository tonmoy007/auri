"""Tests for the retrieval evaluation script: metric maths, calibration and the CLI.

Metric functions are pure, so they are checked on toy data with hand-computed values.
The CLI is run end to end over a tiny synthetic index with a fake embedder: no test
reaches Ollama, the network or the real vault, and no test holds real vault text.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pytest
from app.exceptions import PriestIndexError
from app.priest.embedder import EmbedModelInfo
from app.priest.index_store import IndexManifest, activate, write_version
from app.priest.schemas import TraditionId
from app.priest.types import Chunk
from app.priest.vault_rules import is_denied_path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import eval_priest_retrieval as script
from eval_priest_retrieval import (
    Calibration,
    GoldError,
    ScoreRow,
    calibrate_floors,
    f1,
    load_gold,
    mrr,
    normalise_path,
    not_covered_scores,
    note_ranking,
    percentile,
    precision_recall_f1,
    recall_at_k,
    reciprocal_rank,
    reports_dir,
)

GOLD_FIXTURE = Path(__file__).parent / "fixtures" / "priest_retrieval_gold.json"
DIM = 64
DIGEST = "sha256:fake-digest"
VERSION = "20260930T000000Z-aaaa0001"
MODEL = "nomic-embed-text"


# ── recall, rank and f1 on toy data ──────────────────────────────────────


@pytest.mark.parametrize(
    ("k", "expected_recall"),
    [(1, 0.0), (2, 0.5), (3, 0.5), (4, 1.0), (10, 1.0)],
)
def test_recall_at_k_counts_expected_notes_found_in_the_top_k(
    k: int, expected_recall: float
) -> None:
    # Arrange
    ranked = ["a", "b", "c", "d"]
    expected = {"b", "d"}

    # Act
    result = recall_at_k(ranked, expected, k)

    # Assert
    assert result == expected_recall


def test_recall_at_k_does_not_count_a_repeated_note_twice() -> None:
    # Arrange
    ranked = ["b", "b", "d"]

    # Act
    result = recall_at_k(ranked, {"b", "d"}, 2)

    # Assert
    assert result == 0.5


def test_recall_at_k_needs_an_expected_note() -> None:
    # Arrange
    ranked = ["a"]

    # Act / Assert
    with pytest.raises(ValueError, match="expected"):
        recall_at_k(ranked, set(), 3)


def test_recall_at_k_needs_a_positive_k() -> None:
    # Arrange
    ranked = ["a"]

    # Act / Assert
    with pytest.raises(ValueError, match="k must"):
        recall_at_k(ranked, {"a"}, 0)


def test_reciprocal_rank_is_one_over_the_first_hit() -> None:
    # Arrange
    ranked = ["x", "y", "b", "d"]

    # Act
    result = reciprocal_rank(ranked, {"b", "d"})

    # Assert
    assert result == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_is_found() -> None:
    # Arrange
    ranked = ["x", "y"]

    # Act
    result = reciprocal_rank(ranked, {"b"})

    # Assert
    assert result == 0.0


def test_mrr_averages_the_reciprocal_ranks() -> None:
    # Arrange
    rankings = [["a", "z"], ["z", "a"], ["z", "y"]]
    expecteds = [{"a"}, {"a"}, {"a"}]

    # Act
    result = mrr(rankings, expecteds)

    # Assert
    assert result == pytest.approx(0.5)  # (1 + 1/2 + 0) / 3


def test_mrr_of_no_questions_is_zero() -> None:
    # Arrange
    rankings: list[list[str]] = []

    # Act
    result = mrr(rankings, [])

    # Assert
    assert result == 0.0


def test_mrr_rejects_mismatched_lengths() -> None:
    # Arrange
    rankings = [["a"], ["b"]]

    # Act / Assert
    with pytest.raises(ValueError, match="same length"):
        mrr(rankings, [{"a"}])


@pytest.mark.parametrize(
    ("precision", "recall", "expected"),
    [(0.5, 0.5, 0.5), (1.0, 0.5, 2 / 3), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
)
def test_f1_is_the_harmonic_mean(
    precision: float, recall: float, expected: float
) -> None:
    # Act
    result = f1(precision, recall)

    # Assert
    assert result == pytest.approx(expected)


def test_precision_recall_f1_from_counts() -> None:
    # Arrange: 3 true hits, 1 false alarm, 2 misses

    # Act
    p, r, score = precision_recall_f1(tp=3, fp=1, fn=2)

    # Assert
    assert p == pytest.approx(0.75)
    assert r == pytest.approx(0.6)
    assert score == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))


def test_precision_recall_f1_with_no_events_is_zero() -> None:
    # Act
    result = precision_recall_f1(tp=0, fp=0, fn=0)

    # Assert
    assert result == (0.0, 0.0, 0.0)


def test_not_covered_scores_treat_not_covered_as_the_positive_class() -> None:
    # Arrange
    predicted_covered = [True, False, False, True]
    expected_covered = [True, False, True, False]
    # predicted not covered: items 1 and 2; really not covered: items 1 and 3
    # so tp=1 (item 1), fp=1 (item 2), fn=1 (item 3)

    # Act
    scores = not_covered_scores(predicted_covered, expected_covered)

    # Assert
    assert (scores.tp, scores.fp, scores.fn) == (1, 1, 1)
    assert scores.precision == pytest.approx(0.5)
    assert scores.recall == pytest.approx(0.5)
    assert scores.f1 == pytest.approx(0.5)


def test_not_covered_scores_reject_mismatched_lengths() -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="same length"):
        not_covered_scores([True], [True, False])


def test_percentile_is_the_harness_function() -> None:
    # Arrange
    values = [1.0, 2.0, 3.0, 4.0]

    # Act
    result = percentile(values, 0.5)

    # Assert
    assert result == pytest.approx(2.5)


# ── ranking helpers ──────────────────────────────────────────────────────


def test_note_ranking_keeps_first_appearance_order_and_drops_repeats() -> None:
    # Arrange
    paths = ["a.md", "b.md", "a.md", "c.md", "b.md"]

    # Act
    ranked = note_ranking(paths, limit=10)

    # Assert
    assert ranked == ["a.md", "b.md", "c.md"]


def test_note_ranking_stops_at_the_limit() -> None:
    # Arrange
    paths = ["a.md", "b.md", "c.md", "d.md"]

    # Act
    ranked = note_ranking(paths, limit=2)

    # Assert
    assert ranked == ["a.md", "b.md"]


def test_paths_match_whether_the_filesystem_composed_the_accents_or_not() -> None:
    # Arrange
    composed = unicodedata.normalize("NFC", "concepts/Činvat Bridge.md")
    decomposed = unicodedata.normalize("NFD", composed)

    # Act
    same = normalise_path(composed) == normalise_path(decomposed)
    ranked = note_ranking([decomposed, composed], limit=5)

    # Assert
    assert composed != decomposed
    assert same
    assert ranked == [composed]


# ── floor calibration ────────────────────────────────────────────────────


def test_calibration_finds_the_floors_that_separate_a_toy_set() -> None:
    # Arrange: P3 is caught by BM25 only; N1 and N2 have some dense or BM25 score.
    rows = [
        ScoreRow(0.70, 0.0, True),
        ScoreRow(0.60, 0.0, True),
        ScoreRow(0.20, 8.0, True),
        ScoreRow(0.40, 1.0, False),
        ScoreRow(0.30, 2.0, False),
        ScoreRow(0.10, 0.0, False),
    ]

    # Act
    result = calibrate_floors(rows)

    # Assert: dense in (0.40, 0.60] and bm25 in (2.0, 8.0]; the midpoints are chosen.
    assert result == Calibration(
        dense_floor=0.5, bm25_floor=5.0, precision=1.0, recall=1.0, f1=1.0
    )


def test_calibration_switches_a_side_off_when_it_carries_no_signal() -> None:
    # Arrange: BM25 is 0 everywhere, so only a dense floor can help.
    rows = [
        ScoreRow(0.8, 0.0, True),
        ScoreRow(0.3, 0.0, True),
        ScoreRow(0.5, 0.0, False),
        ScoreRow(0.1, 0.0, False),
    ]
    # dense floor 0.65 covers only 0.8: not-covered tp=2, fp=1, fn=0, so F1 0.8.
    # 0.4 gives tp=1, fp=1, fn=1 (F1 0.5); 0.2 gives F1 2/3; "all not covered" 2/3.

    # Act
    result = calibrate_floors(rows)

    # Assert
    assert result.dense_floor == 0.65
    assert result.bm25_floor == script.BM25_DISABLED
    assert result.f1 == pytest.approx(0.8)
    assert result.precision == pytest.approx(2 / 3)
    assert result.recall == pytest.approx(1.0)


def test_calibration_breaks_ties_toward_the_lowest_floors() -> None:
    # Arrange: dense alone or BM25 alone separates the classes perfectly.
    rows = [ScoreRow(0.9, 3.0, True), ScoreRow(0.1, 1.0, False)]

    # Act
    result = calibrate_floors(rows)

    # Assert: the lowest pair that still scores F1 1.0 is chosen.
    assert (result.dense_floor, result.bm25_floor, result.f1) == (0.5, 2.0, 1.0)


def test_calibration_needs_both_kinds_of_question() -> None:
    # Arrange
    only_covered = [ScoreRow(0.9, 3.0, True), ScoreRow(0.8, 2.0, True)]

    # Act / Assert
    with pytest.raises(ValueError, match="both"):
        calibrate_floors(only_covered)


def test_calibrated_floors_fit_the_settings_ranges() -> None:
    # Arrange
    rows = [ScoreRow(0.7, 4.0, True), ScoreRow(0.2, 0.5, False)]

    # Act
    result = calibrate_floors(rows)

    # Assert
    assert 0.0 <= result.dense_floor <= 1.0
    assert 0.0 <= result.bm25_floor <= 1000.0


# ── the gold set file ────────────────────────────────────────────────────


def test_gold_fixture_has_forty_covered_and_ten_negatives() -> None:
    # Arrange / Act
    questions = load_gold(GOLD_FIXTURE)

    # Assert
    assert len(questions) == 50
    assert sum(q.expected_covered for q in questions) == 40
    assert sum(not q.expected_covered for q in questions) == 10
    assert len({q.id for q in questions}) == 50


def test_gold_fixture_expects_one_to_three_relative_markdown_notes() -> None:
    # Arrange
    questions = [q for q in load_gold(GOLD_FIXTURE) if q.expected_covered]

    # Act
    problems = [
        (q.id, path)
        for q in questions
        for path in q.expected_paths
        if is_denied_path(path) is not None or not path.endswith(".md")
    ]

    # Assert
    assert problems == []
    assert all(1 <= len(q.expected_paths) <= 3 for q in questions)
    assert all(len(set(q.expected_paths)) == len(q.expected_paths) for q in questions)


def test_gold_fixture_negatives_expect_no_notes() -> None:
    # Arrange
    negatives = [q for q in load_gold(GOLD_FIXTURE) if not q.expected_covered]

    # Act / Assert
    assert all(q.expected_paths == () for q in negatives)


def test_gold_fixture_mixes_the_three_kinds_of_in_scope_question() -> None:
    # Arrange
    kinds = {q.kind for q in load_gold(GOLD_FIXTURE)}

    # Act / Assert
    assert kinds == {"proper_noun", "feeling", "verse", "out_of_scope"}


def test_gold_fixture_holds_questions_and_paths_only() -> None:
    # Arrange
    body = json.loads(GOLD_FIXTURE.read_text("utf-8"))
    allowed_question_keys = {
        "id",
        "question",
        "kind",
        "tradition",
        "expected_covered",
        "expected",
    }

    # Act
    extra_keys = {key for q in body["questions"] for key in q} - allowed_question_keys
    longest_question = max(len(q["question"]) for q in body["questions"])
    expected_keys = {key for q in body["questions"] for e in q["expected"] for key in e}

    # Assert: no field could carry vault prose, and every question is short.
    assert extra_keys == set()
    assert expected_keys <= {"note_path", "headings"}
    assert longest_question <= 120


def test_gold_fixture_traditions_are_valid_ids_or_absent() -> None:
    # Arrange
    valid = {t.value for t in TraditionId}

    # Act
    used = {q.tradition for q in load_gold(GOLD_FIXTURE) if q.tradition}

    # Assert
    assert used <= valid


def _write_gold(tmp_path: Path, questions: list[dict[str, object]]) -> Path:
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"version": 1, "questions": questions}), "utf-8")
    return path


def _question(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "g1",
        "question": "What is zorvath?",
        "kind": "proper_noun",
        "tradition": None,
        "expected_covered": True,
        "expected": [{"note_path": "concepts/alpha.md"}],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad",
    [
        _question(expected=[]),
        _question(expected_covered=False),
        _question(expected=[{"note_path": f"concepts/n{i}.md"} for i in range(4)]),
        _question(expected=[{"note_path": "../secret.md"}]),
        _question(expected=[{"note_path": "concepts/alpha.txt"}]),
        _question(tradition="not-a-tradition"),
        _question(question="  "),
        _question(kind="mystery"),
        _question(id=""),
    ],
)
def test_load_gold_rejects_a_malformed_question(
    tmp_path: Path, bad: dict[str, object]
) -> None:
    # Arrange
    path = _write_gold(tmp_path, [bad])

    # Act / Assert
    with pytest.raises(GoldError):
        load_gold(path)


def test_load_gold_rejects_a_duplicate_id(tmp_path: Path) -> None:
    # Arrange
    path = _write_gold(tmp_path, [_question(), _question()])

    # Act / Assert
    with pytest.raises(GoldError, match="duplicate"):
        load_gold(path)


def test_load_gold_rejects_a_file_that_is_not_json(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "gold.json"
    path.write_text("{not json", "utf-8")

    # Act / Assert
    with pytest.raises(GoldError):
        load_gold(path)


# ── synthetic index and fake embedder ────────────────────────────────────


def axis(i: int) -> np.ndarray:
    """A one-hot unit vector."""
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def _chunk(i: int, path: str, title: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"c{i:03d}",
        note_path=path,
        note_title=title,
        heading_path=(title,),
        obsidian_anchor="",
        note_type="concept",
        traditions=("buddhism",),
        text=text,
        quote_blocks=(),
        char_len=len(text),
    )


CORPUS = [
    _chunk(0, "concepts/alpha.md", "Alpha Virtue", "zorvath is harmony of the plain"),
    _chunk(1, "stories/beta.md", "Beta Tale", "a quillon journey over the hills"),
    _chunk(
        2, "texts/gamma.md", "Gamma Verse", "commentary on verse 9:99 and its reception"
    ),
    _chunk(3, "notes/delta.md", "Delta Filler", "sowing reaping seasons"),
    _chunk(4, "notes/epsilon.md", "Epsilon Filler", "a ferry crosses the water"),
    _chunk(5, "notes/zeta.md", "Zeta Filler", "rules for sharing a meal"),
]
CORPUS_VECTORS = np.stack([axis(i) for i in range(len(CORPUS))]).astype(np.float32)

GOLD = [
    _question(
        id="g01",
        question="What is zorvath?",
        expected=[{"note_path": "concepts/alpha.md"}],
    ),
    _question(
        id="g02",
        question="feeling far from home",
        kind="feeling",
        expected=[{"note_path": "stories/beta.md"}],
    ),
    _question(
        id="g03",
        question="verse 9:99",
        kind="verse",
        expected=[{"note_path": "texts/gamma.md"}],
    ),
    _question(
        id="n01",
        question="How do I fix a tap?",
        kind="out_of_scope",
        expected_covered=False,
        expected=[],
    ),
    _question(
        id="n02",
        question="What is the capital of Peru?",
        kind="out_of_scope",
        expected_covered=False,
        expected=[],
    ),
]
QUERY_VECTORS = {
    "What is zorvath?": axis(0),
    "feeling far from home": axis(1),
    "verse 9:99": axis(2),
}


def build_index(root: Path, *, digest: str = DIGEST) -> None:
    """Write the synthetic corpus as the active index under *root*."""
    manifest = IndexManifest(
        version=VERSION,
        created_at="2026-09-30T00:00:00Z",
        embed_model=MODEL,
        embed_digest=digest,
        dim=DIM,
        chunk_count=len(CORPUS),
        note_count=len(CORPUS),
        note_hashes={},
        exclusions={},
        unresolved_links=0,
        cleaner_version="c1",
        chunker_version="k1",
        warnings=[],
        build_seconds=0.0,
        error_code=None,
    )
    write_version(root, CORPUS, CORPUS_VECTORS, manifest)
    activate(root, VERSION)


class FakeEmbedder:
    """Maps a known question to its vector; any other question to an unused axis."""

    def __init__(self, *, digest: str = DIGEST, unreachable: bool = False) -> None:
        self.digest = digest
        self.unreachable = unreachable

    async def embed_query(self, text: str) -> np.ndarray:
        return QUERY_VECTORS.get(text, axis(DIM - 1))

    async def model_info(self) -> EmbedModelInfo:
        if self.unreachable:
            raise PriestIndexError("embedding server is unreachable")
        return EmbedModelInfo(name=MODEL, digest=self.digest, dim=DIM)


class Factory:
    """An embedder factory that records how it was called."""

    def __init__(self, embedder: FakeEmbedder) -> None:
        self.embedder = embedder
        self.calls: list[tuple[str, str]] = []

    def __call__(self, base_url: str, model: str) -> FakeEmbedder:
        self.calls.append((base_url, model))
        return self.embedder


def _run(
    tmp_path: Path,
    factory: Factory,
    *,
    index: Path | None = None,
    gold: Path | None = None,
    extra: list[str] | None = None,
) -> int:
    index_dir = index if index is not None else tmp_path / "index"
    gold_path = gold if gold is not None else _write_gold(tmp_path, GOLD)
    argv = [
        "--index-dir",
        str(index_dir),
        "--gold",
        str(gold_path),
        "--report",
        str(tmp_path / "report.md"),
        "--ollama-url",
        "http://ollama.test",
        *(extra or []),
    ]
    return script.main(argv, embedder_factory=factory)


def _summary(tmp_path: Path) -> dict[str, object]:
    found = sorted((tmp_path / "index" / "reports").glob("retrieval_*.json"))
    assert len(found) == 1
    return json.loads(found[0].read_text("utf-8"))


# ── the CLI ──────────────────────────────────────────────────────────────


def test_cli_reports_each_mode_with_hand_checked_metrics(tmp_path: Path) -> None:
    # Arrange
    build_index(tmp_path / "index")
    factory = Factory(FakeEmbedder())

    # Act
    code = _run(tmp_path, factory)

    # Assert
    assert code == 0
    modes = _summary(tmp_path)["indexes"][0]["modes"]  # type: ignore[index]
    # BM25 finds g01 and g03 by their terms and nothing for the paraphrase g02.
    assert modes["bm25"]["recall@3"] == pytest.approx(2 / 3)
    assert modes["bm25"]["mrr"] == pytest.approx(2 / 3)
    # Dense finds every expected note at rank 1 through its query vector.
    assert modes["dense"]["recall@3"] == 1.0
    assert modes["dense"]["mrr"] == 1.0
    # Hybrid gets the best of both.
    assert modes["hybrid"]["recall@6"] == 1.0
    assert modes["hybrid"]["recall@10"] == 1.0
    assert modes["hybrid"]["latency_ms"]["count"] == 5


def test_cli_calibrates_and_scores_not_covered_detection(tmp_path: Path) -> None:
    # Arrange
    build_index(tmp_path / "index")

    # Act
    code = _run(tmp_path, Factory(FakeEmbedder()))

    # Assert
    assert code == 0
    index = _summary(tmp_path)["indexes"][0]  # type: ignore[index]
    calibrated = index["not_covered"]["calibrated"]
    assert calibrated["f1"] == 1.0
    assert 0.0 < calibrated["dense_floor"] <= 1.0
    configured = index["not_covered"]["configured"]
    assert configured["f1"] == 1.0
    assert index["hybrid_missed_ids"] == []


def test_cli_queries_with_the_model_named_in_the_index_manifest(tmp_path: Path) -> None:
    # Arrange
    build_index(tmp_path / "index")
    factory = Factory(FakeEmbedder())

    # Act
    _run(tmp_path, factory)

    # Assert
    assert factory.calls == [("http://ollama.test", MODEL)]


def test_cli_writes_the_markdown_report_and_prints_the_floors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")

    # Act
    code = _run(tmp_path, Factory(FakeEmbedder()))

    # Assert
    text = (tmp_path / "report.md").read_text("utf-8")
    out = capsys.readouterr().out
    assert code == 0
    assert "recall@6" in text
    assert "PRIEST_MIN_RELEVANCE_DENSE" in text
    assert "PRIEST_EMBED_MODEL" in text
    assert "PRIEST_MIN_RELEVANCE_DENSE=" in out
    assert "PRIEST_MIN_RELEVANCE_BM25=" in out


def test_cli_reports_hold_no_note_text_and_no_question_text(tmp_path: Path) -> None:
    # Arrange
    build_index(tmp_path / "index")

    # Act
    _run(tmp_path, Factory(FakeEmbedder()))

    # Assert
    written = (tmp_path / "report.md").read_text("utf-8") + json.dumps(
        _summary(tmp_path)
    )
    for chunk in CORPUS:
        assert chunk.text not in written
        assert chunk.note_title not in written
    for word in ("zorvath", "quillon", "fix a tap", "Peru", "far from home"):
        assert word not in written


def test_cli_names_the_summary_file_by_timestamp_inside_the_index_reports_dir(
    tmp_path: Path,
) -> None:
    # Arrange
    build_index(tmp_path / "index")

    # Act
    _run(tmp_path, Factory(FakeEmbedder()))

    # Assert
    (path,) = (tmp_path / "index" / "reports").iterdir()
    assert re.fullmatch(r"retrieval_\d{8}T\d{6}Z\.json", path.name)


def test_cli_refuses_a_reports_directory_that_points_outside_the_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "index" / "reports").symlink_to(outside, target_is_directory=True)

    # Act
    code = _run(tmp_path, Factory(FakeEmbedder()))

    # Assert
    assert code == 2
    assert list(outside.iterdir()) == []
    assert "reports" in capsys.readouterr().err


def test_reports_dir_sits_under_the_index_root(tmp_path: Path) -> None:
    # Arrange / Act
    directory = reports_dir(tmp_path)

    # Assert
    assert directory == tmp_path / "reports"


def test_cli_can_apply_the_tradition_filter(tmp_path: Path) -> None:
    # Arrange: every synthetic chunk is tagged buddhism, so a zoroastrian filter finds none.
    build_index(tmp_path / "index")
    gold = _write_gold(
        tmp_path,
        [
            _question(id="g01", tradition="zoroastrianism"),
            _question(
                id="n01", expected_covered=False, expected=[], kind="out_of_scope"
            ),
        ],
    )

    # Act
    code = _run(tmp_path, Factory(FakeEmbedder()), gold=gold, extra=["--use-tradition"])

    # Assert
    assert code == 0
    modes = _summary(tmp_path)["indexes"][0]["modes"]  # type: ignore[index]
    assert modes["hybrid"]["recall@10"] == 0.0


# ── skipping and errors ──────────────────────────────────────────────────


def test_cli_skips_with_exit_zero_when_there_is_no_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    factory = Factory(FakeEmbedder())

    # Act
    code = _run(tmp_path, factory, index=tmp_path / "missing-index")

    # Assert
    assert code == 0
    assert "SKIP" in capsys.readouterr().out
    assert factory.calls == []
    assert not (tmp_path / "missing-index").exists()
    assert not (tmp_path / "report.md").exists()


def test_cli_skips_with_exit_zero_when_ollama_is_not_reachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")
    factory = Factory(FakeEmbedder(unreachable=True))

    # Act
    code = _run(tmp_path, factory)

    # Assert
    out = capsys.readouterr().out
    assert code == 0
    assert "SKIP" in out
    assert "Ollama" in out
    assert not (tmp_path / "report.md").exists()
    assert not (tmp_path / "index" / "reports").exists()


def test_cli_skips_when_the_index_model_is_not_a_known_embedder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")

    def refuse(base_url: str, model: str) -> FakeEmbedder:
        raise PriestIndexError("embedding model is not in the registry")

    # Act
    code = script.main(
        [
            "--index-dir",
            str(tmp_path / "index"),
            "--gold",
            str(_write_gold(tmp_path, GOLD)),
            "--report",
            str(tmp_path / "report.md"),
        ],
        embedder_factory=refuse,
    )

    # Assert
    assert code == 0
    assert "SKIP" in capsys.readouterr().out


def test_cli_fails_when_the_index_was_built_with_other_weights(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")
    factory = Factory(FakeEmbedder(digest="sha256:some-other-weights"))

    # Act
    code = _run(tmp_path, factory)

    # Assert
    assert code == 2
    assert "digest" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()


def test_cli_fails_when_the_gold_file_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    build_index(tmp_path / "index")

    # Act
    code = _run(tmp_path, Factory(FakeEmbedder()), gold=tmp_path / "nope.json")

    # Assert
    assert code == 2
    assert "gold" in capsys.readouterr().err


def test_cli_evaluates_the_other_index_when_one_is_missing(tmp_path: Path) -> None:
    # Arrange
    build_index(tmp_path / "index")
    factory = Factory(FakeEmbedder())
    argv = [
        "--index-dir",
        str(tmp_path / "absent"),
        "--index-dir",
        str(tmp_path / "index"),
        "--gold",
        str(_write_gold(tmp_path, GOLD)),
        "--report",
        str(tmp_path / "report.md"),
    ]

    # Act
    code = script.main(argv, embedder_factory=factory)

    # Assert
    assert code == 0
    assert len(_summary(tmp_path)["indexes"]) == 1  # type: ignore[arg-type]
    assert "SKIP" in (tmp_path / "report.md").read_text("utf-8")
