"""Evaluate priest-mode retrieval against the gold set: BM25, dense and hybrid.

Usage::

    python backend/scripts/eval_priest_retrieval.py [--index-dir DIR ...]
        [--gold FILE] [--report FILE] [--ollama-url URL] [--use-tradition]
        [--top-k N]

For each index it reports recall@3/6/10, MRR and latency p50/p95 for the three modes,
scores the "is this covered?" decision (precision, recall and F1 of *not covered*), and
calibrates the two relevance floors on the out-of-scope negatives. Run it once per
embedding model's index (repeat ``--index-dir``) to compare embedders.

Definitions. A chunk ranking becomes a note ranking: notes in order of their best chunk,
each note once. Recall@k is the share of a question's expected notes among the first k
notes; MRR is the mean of 1 / (rank of the first expected note), over the in-scope
questions. Latency is the cost of one retrieval in that mode: BM25 alone, query
embedding plus cosine for dense, and the production ``Retriever`` for hybrid.

Hybrid is scored at what the app returns: ``--top-k`` chunks (default
``PRIEST_TOP_K``), at most two per note, so a handful of distinct notes. A second,
deeper hybrid figure (30 chunks) is reported beside it, labelled, for comparison with
earlier reports; it flatters the app, which never shows that many. The gate is judged
on the configured floors. The calibrated floors are fitted on the same questions they
are scored on, so their F1 is in-sample: a description of the data, not a forecast.

It skips, exit code 0, when an index is missing or unusable or Ollama cannot be
reached, so it is safe in a scheduled job on a machine without either. Real problems
(a bad gold file, an index built with other weights, an unsafe reports directory) exit 2.
Output holds metrics and question ids only: no note text, no question text, no path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import unicodedata
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Final, TypeVar

import numpy as np

# `app` lives one level up, and `eval` is the sibling package next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings
from app.exceptions import PriestIndexError
from app.priest.bm25_index import Bm25Index
from app.priest.embedder import OllamaEmbedder
from app.priest.index_store import ActiveIndex, LoadedIndex
from app.priest.retriever import QueryEmbedder, Retriever
from app.priest.schemas import MAX_QUESTION_CHARS, MIN_QUESTION_CHARS, TraditionId
from app.priest.vault_rules import is_denied_path
from eval.harness import (
    LatencyStats,
    Scalar,
    latency_stats,
    markdown_table,
    percentile,
    reports_dir,
    safe_report_path,
    timestamp_slug,
)

__all__ = [
    "Calibration",
    "GoldError",
    "GoldQuestion",
    "ScoreRow",
    "calibrate_floors",
    "f1",
    "load_gold",
    "main",
    "mrr",
    "normalise_path",
    "not_covered_scores",
    "note_ranking",
    "percentile",
    "precision_recall_f1",
    "recall_at_k",
    "reciprocal_rank",
    "reports_dir",
]

MODES: Final = ("bm25", "dense", "hybrid")
RECALL_KS: Final = (3, 6, 10)
RANK_DEPTH: Final = max(RECALL_KS)
# The retriever takes this many hits from each side before fusing (its _SIDE_DEPTH).
SIDE_DEPTH: Final = 30
# The deep hybrid figure asks for this many chunks. The app returns its ``top_k`` (6 by
# default) chunks, at most two per note, which is 3 to 6 distinct notes; the deep run is
# only a comparison with the numbers of earlier reports, not what a user is shown.
HYBRID_DEEP_TOP_K: Final = SIDE_DEPTH
DEEP_MODE: Final = "hybrid_deep"
# The largest values priest_config accepts; a floor this high switches its side off.
DENSE_DISABLED: Final = 1.0
BM25_DISABLED: Final = 1000.0
_DENSE_DIGITS: Final = 3
_BM25_DIGITS: Final = 2
# What the app returns when PRIEST_TOP_K is not set.
APP_TOP_K: Final = 6
GATE_RECALL_AT_6: Final = 0.85
GATE_NOT_COVERED_F1: Final = 0.85
SCHEMA_VERSION: Final = 1
GOLD_KINDS: Final = frozenset({"proper_noun", "feeling", "verse", "out_of_scope"})
MAX_EXPECTED_NOTES: Final = 3
EXIT_ERROR: Final = 2
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_GOLD: Final = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "priest_retrieval_gold.json"
)
DEFAULT_REPORT: Final = _REPO_ROOT / "docs" / "priest-retrieval-report.md"
_KNOWN_TRADITIONS: Final = frozenset(t.value for t in TraditionId)
_F1_DIGITS: Final = 9

T = TypeVar("T")


# ── pure metrics ─────────────────────────────────────────────────────────


def recall_at_k(ranked: Sequence[str], expected: Collection[str], k: int) -> float:
    """The share of *expected* notes found among the first *k* of *ranked*.

    Raises:
        ValueError: If *expected* is empty or *k* is below one.
    """
    if not expected:
        raise ValueError("recall needs at least one expected note")
    if k < 1:
        raise ValueError("k must be at least 1")
    return len(set(ranked[:k]) & set(expected)) / len(set(expected))


def reciprocal_rank(ranked: Sequence[str], expected: Collection[str]) -> float:
    """``1 / rank`` of the first expected note in *ranked*; 0.0 if none is there."""
    for rank, note in enumerate(ranked, start=1):
        if note in expected:
            return 1.0 / rank
    return 0.0


def mrr(
    rankings: Sequence[Sequence[str]], expecteds: Sequence[Collection[str]]
) -> float:
    """Mean reciprocal rank over questions; 0.0 when there are none.

    Raises:
        ValueError: If the two sequences are not the same length.
    """
    if len(rankings) != len(expecteds):
        raise ValueError("rankings and expecteds must be the same length")
    if not rankings:
        return 0.0
    return sum(
        reciprocal_rank(r, e) for r, e in zip(rankings, expecteds, strict=True)
    ) / len(rankings)


def f1(precision: float, recall: float) -> float:
    """The harmonic mean of precision and recall; 0.0 when both are 0."""
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def precision_recall_f1(*, tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Precision, recall and F1 from counts; a zero denominator gives 0.0."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return precision, recall, f1(precision, recall)


@dataclass(frozen=True)
class NotCoveredScores:
    """How well "not covered" was detected; *not covered* is the positive class."""

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


def not_covered_scores(
    predicted_covered: Sequence[bool], expected_covered: Sequence[bool]
) -> NotCoveredScores:
    """Score the covered / not-covered decision against the gold labels.

    Raises:
        ValueError: If the two sequences are not the same length.
    """
    if len(predicted_covered) != len(expected_covered):
        raise ValueError("predicted and expected must be the same length")
    pairs = list(zip(predicted_covered, expected_covered, strict=True))
    tp = sum(1 for p, e in pairs if not p and not e)
    fp = sum(1 for p, e in pairs if not p and e)
    fn = sum(1 for p, e in pairs if p and not e)
    precision, recall, score = precision_recall_f1(tp=tp, fp=fp, fn=fn)
    return NotCoveredScores(tp, fp, fn, precision, recall, score)


def normalise_path(path: str) -> str:
    """NFC-normalise a note path: macOS and Linux may store the same accent differently."""
    return unicodedata.normalize("NFC", path)


def note_ranking(note_paths: Sequence[str], limit: int) -> list[str]:
    """Collapse chunk-ordered note paths to distinct notes, first appearance first."""
    seen: dict[str, None] = {}
    for path in note_paths:
        seen.setdefault(normalise_path(path), None)
        if len(seen) == limit:
            break
    return list(seen)


# ── floor calibration ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreRow:
    """The best raw scores retrieval gave one question, and whether it is in scope."""

    best_dense: float
    best_bm25: float
    expected_covered: bool


@dataclass(frozen=True)
class Calibration:
    """The two floors that best separate in-scope questions from the negatives."""

    dense_floor: float
    bm25_floor: float
    precision: float
    recall: float
    f1: float


def _candidates(values: Sequence[float], disabled: float, digits: int) -> list[float]:
    """Floors worth trying: always-covered, midpoints between seen scores, switched-off."""
    ordered = sorted(set(values))
    midpoints = (round((a + b) / 2.0, digits) for a, b in pairwise(ordered))
    return sorted({0.0, disabled, *(min(m, disabled) for m in midpoints)})


def _scores_at(rows: Sequence[ScoreRow], dense: float, bm25: float) -> NotCoveredScores:
    """Not-covered scores if covered means ``best_dense >= dense or best_bm25 >= bm25``."""
    return not_covered_scores(
        [covered_at(row, dense, bm25) for row in rows],
        [row.expected_covered for row in rows],
    )


def covered_at(row: ScoreRow, dense_floor: float, bm25_floor: float) -> bool:
    """The app's rule: covered when either best score reaches its floor."""
    return row.best_dense >= dense_floor or row.best_bm25 >= bm25_floor


def calibrate_floors(rows: Sequence[ScoreRow]) -> Calibration:
    """Pick the floors that maximise the F1 of detecting *not covered*.

    Every pair of candidate floors is tried, in ascending order; the first pair with
    the best F1 wins, so ties go to the lowest floors, which favour answering from the
    library (the answer validators are the second net).

    Raises:
        ValueError: If *rows* lacks in-scope questions or negatives.
    """
    if not any(r.expected_covered for r in rows) or all(
        r.expected_covered for r in rows
    ):
        raise ValueError("calibration needs both in-scope and out-of-scope questions")
    dense_options = _candidates(
        [r.best_dense for r in rows], DENSE_DISABLED, _DENSE_DIGITS
    )
    bm25_options = _candidates([r.best_bm25 for r in rows], BM25_DISABLED, _BM25_DIGITS)
    trials = [
        (dense, bm25, _scores_at(rows, dense, bm25))
        for dense in dense_options
        for bm25 in bm25_options
    ]
    # max() keeps the first of equal scores; rounding stops a last-bit float difference
    # between equal F1 values from deciding the winner.
    dense, bm25, scores = max(trials, key=lambda trial: round(trial[2].f1, _F1_DIGITS))
    return Calibration(dense, bm25, scores.precision, scores.recall, scores.f1)


# ── the gold set ─────────────────────────────────────────────────────────


class GoldError(ValueError):
    """The gold file is unreadable or does not follow the gold-set format."""


@dataclass(frozen=True)
class GoldQuestion:
    """One gold question. Paths are relative to ``religion-study/``; there is no text."""

    id: str
    question: str
    kind: str
    tradition: str | None
    expected_covered: bool
    expected_paths: tuple[str, ...]


def _expected_paths(raw: object, covered: bool, label: str) -> tuple[str, ...]:
    """Validate an ``expected`` list: 1-3 safe ``.md`` paths for in-scope, none otherwise."""
    if not isinstance(raw, list):
        raise GoldError(f"{label}: expected must be a list")
    paths: list[str] = []
    for entry in raw:
        path = entry.get("note_path") if isinstance(entry, dict) else None
        headings = entry.get("headings", []) if isinstance(entry, dict) else None
        if not isinstance(path, str) or not isinstance(headings, list):
            raise GoldError(f"{label}: each expected entry needs a note_path")
        if is_denied_path(path) is not None or not path.endswith(".md"):
            raise GoldError(f"{label}: note_path must be a relative .md path")
        paths.append(normalise_path(path))
    if len(set(paths)) != len(paths):
        raise GoldError(f"{label}: duplicate expected note")
    if covered and not 1 <= len(paths) <= MAX_EXPECTED_NOTES:
        raise GoldError(f"{label}: an in-scope question needs 1-3 expected notes")
    if not covered and paths:
        raise GoldError(f"{label}: a negative must expect no notes")
    return tuple(paths)


def _gold_question(raw: object, index: int) -> GoldQuestion:
    """Validate one gold entry; the label in errors is its position, never its text."""
    label = f"question {index}"
    if not isinstance(raw, dict):
        raise GoldError(f"{label}: must be an object")
    ident, text, kind = raw.get("id"), raw.get("question"), raw.get("kind")
    tradition, covered = raw.get("tradition"), raw.get("expected_covered")
    if not isinstance(ident, str) or not ident.strip():
        raise GoldError(f"{label}: id is required")
    if not isinstance(text, str) or not (
        MIN_QUESTION_CHARS <= len(text.strip()) <= MAX_QUESTION_CHARS
    ):
        raise GoldError(f"{label}: question text is missing or out of bounds")
    if kind not in GOLD_KINDS:
        raise GoldError(f"{label}: kind must be one of {sorted(GOLD_KINDS)}")
    if tradition is not None and tradition not in _KNOWN_TRADITIONS:
        raise GoldError(f"{label}: unknown tradition")
    if not isinstance(covered, bool) or (kind == "out_of_scope") == covered:
        raise GoldError(f"{label}: out_of_scope must match expected_covered is false")
    paths = _expected_paths(raw.get("expected"), covered, label)
    return GoldQuestion(ident, text.strip(), str(kind), tradition, covered, paths)


def load_gold(path: Path) -> list[GoldQuestion]:
    """Read and validate the gold set.

    Raises:
        GoldError: If the file cannot be read or parsed, a question is malformed, or
            two questions share an id.
    """
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoldError("the gold file cannot be read as JSON") from exc
    entries = body.get("questions") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise GoldError("the gold file needs a questions list")
    if not entries:
        raise GoldError("the gold file has no questions")
    questions = [_gold_question(raw, n) for n, raw in enumerate(entries, start=1)]
    if len({q.id for q in questions}) != len(questions):
        raise GoldError("duplicate question id in the gold file")
    return questions


# ── running the three modes ──────────────────────────────────────────────


@dataclass(frozen=True)
class ModeMetrics:
    """Recall at each k, MRR and latency for one mode."""

    recall: dict[int, float]
    mrr: float
    latency: LatencyStats


@dataclass(frozen=True)
class IndexEvaluation:
    """Everything measured for one index.

    ``modes`` holds BM25, dense and hybrid at the app's ``top_k``; ``hybrid_deep`` is
    hybrid at 30 chunks. ``calibration`` is fitted on the same questions it is scored
    on (in-sample); ``configured`` is what the floors in the settings actually do.
    """

    version: str
    embed_model: str
    chunk_count: int
    top_k: int
    modes: dict[str, ModeMetrics]
    hybrid_deep: ModeMetrics
    configured_floors: tuple[float, float]
    configured: NotCoveredScores
    calibration: Calibration
    hybrid_missed_ids: tuple[str, ...]


@dataclass(frozen=True)
class Skipped:
    """An index that was not evaluated, and why. The reason is a fixed message."""

    reason: str


class ScriptError(Exception):
    """A problem that stops the run with exit code 2; the message is safe to print."""


def _allowed_rows(
    loaded: LoadedIndex, question: GoldQuestion, use_tradition: bool
) -> np.ndarray | None:
    """Rows tagged with the question's tradition, or ``None`` for no filter."""
    if not use_tradition or question.tradition is None:
        return None
    keep = [
        row
        for row, chunk in enumerate(loaded.chunks)
        if question.tradition in chunk.traditions
    ]
    return np.array(keep, dtype=np.int64)


def _dense_notes(
    loaded: LoadedIndex, query: np.ndarray, rows: np.ndarray | None
) -> list[str]:
    """Notes ranked by cosine to *query* over *rows* (every row when ``None``)."""
    if query.shape != (loaded.vectors.shape[1],):
        raise PriestIndexError("the query vector does not match the index dimension")
    candidates = np.arange(len(loaded.chunks)) if rows is None else rows
    if candidates.size == 0:
        return []
    scores = loaded.vectors[candidates] @ query
    order = np.argsort(-scores, kind="stable")[:SIDE_DEPTH]
    return note_ranking(
        [loaded.chunks[int(candidates[i])].note_path for i in order], RANK_DEPTH
    )


def _bm25_notes(
    loaded: LoadedIndex, bm25: Bm25Index, text: str, rows: np.ndarray | None
) -> list[str]:
    """Notes ranked by BM25 for *text*; a question sharing no term gives no notes."""
    allowed = None if rows is None else frozenset(int(r) for r in rows)
    hits = bm25.search(text, SIDE_DEPTH, allowed)
    return note_ranking([loaded.chunks[row].note_path for row, _ in hits], RANK_DEPTH)


async def _timed(clock: Callable[[], float], work: Awaitable[T]) -> tuple[T, float]:
    """Await *work*; return its value and how long it took in milliseconds."""
    started = clock()
    value = await work
    return value, (clock() - started) * 1000.0


def _timed_sync(clock: Callable[[], float], work: Callable[[], T]) -> tuple[T, float]:
    """Call *work*; return its value and how long it took in milliseconds."""
    started = clock()
    value = work()
    return value, (clock() - started) * 1000.0


def _mode_metrics(
    rankings: Sequence[Sequence[str]],
    latencies_ms: Sequence[float],
    gold: Sequence[GoldQuestion],
) -> ModeMetrics:
    """Recall and MRR over the in-scope questions; latency over all of them."""
    scored = [
        (ranking, set(q.expected_paths))
        for ranking, q in zip(rankings, gold, strict=True)
        if q.expected_covered
    ]
    recall = {
        k: sum(recall_at_k(r, e, k) for r, e in scored) / len(scored) if scored else 0.0
        for k in RECALL_KS
    }
    return ModeMetrics(
        recall=recall,
        mrr=mrr([r for r, _ in scored], [e for _, e in scored]),
        latency=latency_stats(latencies_ms),
    )


async def evaluate_index(
    active: ActiveIndex,
    embedder: QueryEmbedder,
    gold: Sequence[GoldQuestion],
    *,
    dense_floor: float,
    bm25_floor: float,
    top_k: int = APP_TOP_K,
    use_tradition: bool = False,
    clock: Callable[[], float] = time.perf_counter,
) -> IndexEvaluation:
    """Run the three modes over *gold* against the active index.

    Hybrid runs at *top_k*, as the app does, and once more at 30 chunks for the
    labelled deep figure.

    Raises:
        PriestIndexError: If retrieval fails (an embedding error, a wrong dimension).
        ValueError: If *gold* has no in-scope question or no negative.
    """
    loaded = active.get()
    bm25 = Bm25Index(loaded.chunks)
    retriever = Retriever(
        active, embedder, dense_floor=dense_floor, bm25_floor=bm25_floor, top_k=top_k
    )
    deep = Retriever(
        active,
        embedder,
        dense_floor=dense_floor,
        bm25_floor=bm25_floor,
        top_k=HYBRID_DEEP_TOP_K,
    )
    await retriever.retrieve(gold[0].question, None)  # warm-up: BM25 build, model info
    rankings: dict[str, list[list[str]]] = {mode: [] for mode in (*MODES, DEEP_MODE)}
    latencies: dict[str, list[float]] = {mode: [] for mode in (*MODES, DEEP_MODE)}
    score_rows: list[ScoreRow] = []
    predicted: list[bool] = []
    for question in gold:
        rows = _allowed_rows(loaded, question, use_tradition)
        traditions = (
            frozenset({question.tradition})
            if use_tradition and question.tradition
            else None
        )
        notes, ms = _timed_sync(
            clock, partial(_bm25_notes, loaded, bm25, question.question, rows)
        )
        rankings["bm25"].append(notes)
        latencies["bm25"].append(ms)
        notes, ms = await _timed(
            clock, _dense_query(loaded, embedder, question.question, rows)
        )
        rankings["dense"].append(notes)
        latencies["dense"].append(ms)
        result, ms = await _timed(
            clock, retriever.retrieve(question.question, traditions)
        )
        rankings["hybrid"].append(
            note_ranking([rc.chunk.note_path for rc in result.chunks], RANK_DEPTH)
        )
        latencies["hybrid"].append(ms)
        found_deep, ms = await _timed(
            clock, deep.retrieve(question.question, traditions)
        )
        rankings[DEEP_MODE].append(
            note_ranking([rc.chunk.note_path for rc in found_deep.chunks], RANK_DEPTH)
        )
        latencies[DEEP_MODE].append(ms)
        score_rows.append(
            ScoreRow(result.best_dense, result.best_bm25, question.expected_covered)
        )
        predicted.append(result.covered)
    modes = {m: _mode_metrics(rankings[m], latencies[m], gold) for m in MODES}
    deep_metrics = _mode_metrics(rankings[DEEP_MODE], latencies[DEEP_MODE], gold)
    missed = tuple(
        q.id
        for q, ranking in zip(gold, rankings["hybrid"], strict=True)
        if q.expected_covered and recall_at_k(ranking, set(q.expected_paths), 6) == 0.0
    )
    return IndexEvaluation(
        version=loaded.manifest.version,
        embed_model=loaded.manifest.embed_model,
        chunk_count=len(loaded.chunks),
        top_k=top_k,
        modes=modes,
        hybrid_deep=deep_metrics,
        configured_floors=(dense_floor, bm25_floor),
        configured=not_covered_scores(predicted, [q.expected_covered for q in gold]),
        calibration=calibrate_floors(score_rows),
        hybrid_missed_ids=missed,
    )


async def _dense_query(
    loaded: LoadedIndex, embedder: QueryEmbedder, text: str, rows: np.ndarray | None
) -> list[str]:
    """Embed *text* and rank notes by cosine; this is the dense mode's whole cost."""
    return _dense_notes(loaded, await embedder.embed_query(text), rows)


# ── reports ──────────────────────────────────────────────────────────────


def _scores_json(
    scores: NotCoveredScores, dense: float, bm25: float
) -> dict[str, object]:
    return {
        "dense_floor": dense,
        "bm25_floor": bm25,
        "precision": scores.precision,
        "recall": scores.recall,
        "f1": scores.f1,
    }


def _calibrated_scores(evaluation: IndexEvaluation) -> dict[str, object]:
    c = evaluation.calibration
    return {
        # Fitted on the questions it is scored on; never a held-out figure.
        "in_sample": True,
        "dense_floor": c.dense_floor,
        "bm25_floor": c.bm25_floor,
        "precision": c.precision,
        "recall": c.recall,
        "f1": c.f1,
    }


def _latency_json(stats: LatencyStats) -> dict[str, float | int]:
    return {
        "count": stats.count,
        "p50": stats.p50,
        "p95": stats.p95,
        "mean": stats.mean,
        "max": stats.max,
    }


def _mode_json(m: ModeMetrics) -> dict[str, object]:
    return {
        **{f"recall@{k}": m.recall[k] for k in RECALL_KS},
        "mrr": m.mrr,
        "latency_ms": _latency_json(m.latency),
    }


def evaluation_json(evaluation: IndexEvaluation) -> dict[str, object]:
    """One index's results as JSON-ready data: metrics and question ids only."""
    modes = {mode: _mode_json(m) for mode, m in evaluation.modes.items()}
    modes[DEEP_MODE] = _mode_json(evaluation.hybrid_deep)
    dense, bm25 = evaluation.configured_floors
    return {
        "index_version": evaluation.version,
        "embed_model": evaluation.embed_model,
        "chunk_count": evaluation.chunk_count,
        "top_k": evaluation.top_k,
        "modes": modes,
        "not_covered": {
            "configured": _scores_json(evaluation.configured, dense, bm25),
            "calibrated": _calibrated_scores(evaluation),
        },
        "hybrid_missed_ids": list(evaluation.hybrid_missed_ids),
    }


def summary_json(
    evaluation: IndexEvaluation,
    *,
    generated_at: str,
    gold_size: int,
    use_tradition: bool,
) -> dict[str, object]:
    """The summary written to ``<index>/reports/retrieval_<timestamp>.json``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": "priest_retrieval",
        "generated_at": generated_at,
        "gold_questions": gold_size,
        "use_tradition": use_tradition,
        "indexes": [evaluation_json(evaluation)],
    }


def _gate(evaluation: IndexEvaluation) -> bool:
    """Hybrid recall@6 at the app's top_k, and F1 of the configured floors."""
    return (
        evaluation.modes["hybrid"].recall[6] >= GATE_RECALL_AT_6
        and evaluation.configured.f1 >= GATE_NOT_COVERED_F1
    )


def _index_section(number: int, e: IndexEvaluation) -> list[str]:
    lines = [
        f"## Index {number}: {e.embed_model}",
        "",
        f"Version {e.version}, {e.chunk_count} chunks.",
        "",
    ]
    rows = [_mode_row(_mode_label(mode, e.top_k), m) for mode, m in e.modes.items()]
    rows.append(_mode_row(f"hybrid deep ({HYBRID_DEEP_TOP_K} chunks)", e.hybrid_deep))
    headers = ["mode", *(f"recall@{k}" for k in RECALL_KS), "MRR", "p50 ms", "p95 ms"]
    lines += markdown_table(headers, rows)
    c = e.calibration
    dense, bm25 = e.configured_floors
    scores = [
        _score_row("configured", dense, bm25, e.configured),
        _score_row(
            "calibrated (in-sample)", c.dense_floor, c.bm25_floor, e.calibration
        ),
    ]
    lines += [
        "",
        (
            f"Hybrid is scored on the {e.top_k} chunks the app returns (at most two per "
            f"note); the deep row asks for {HYBRID_DEEP_TOP_K} and is for comparison "
            "with earlier reports only."
        ),
        "",
        "Not-covered detection (precision, recall and F1 of *not covered*):",
        "",
    ]
    lines += markdown_table(
        ["floors", "dense floor", "BM25 floor", "precision", "recall", "F1"], scores
    )
    verdict = "PASS" if _gate(e) else "FAIL"
    lines += [
        "",
        (
            f"Gate [A] (hybrid recall@6 >= {GATE_RECALL_AT_6}, configured F1 >= "
            f"{GATE_NOT_COVERED_F1}): {verdict}."
        ),
        "",
        (
            "The calibrated row is fitted on the same questions it is scored on, so "
            "its F1 is in-sample and optimistic; it is not part of the gate."
        ),
        "",
        "Floors that fit these questions (in-sample; check them on new questions):",
        "",
        f"    PRIEST_EMBED_MODEL={e.embed_model}",
        f"    PRIEST_MIN_RELEVANCE_DENSE={c.dense_floor}",
        f"    PRIEST_MIN_RELEVANCE_BM25={c.bm25_floor}",
        "",
    ]
    missed = ", ".join(e.hybrid_missed_ids) or "none"
    lines += [f"In-scope questions the hybrid missed at recall@6 (ids): {missed}.", ""]
    return lines


def _mode_label(mode: str, top_k: int) -> str:
    """The table label of a mode; hybrid says how many chunks it was scored on."""
    return f"hybrid (top {top_k} chunks)" if mode == "hybrid" else mode


def _mode_row(label: str, m: ModeMetrics) -> list[Scalar]:
    """One row of the per-mode table: recall at each k, MRR and latency."""
    return [
        label,
        *(round(m.recall[k], 3) for k in RECALL_KS),
        round(m.mrr, 3),
        round(m.latency.p50, 1),
        round(m.latency.p95, 1),
    ]


def _score_row(
    label: str, dense: float, bm25: float, scores: NotCoveredScores | Calibration
) -> list[Scalar]:
    """One row of the not-covered table."""
    return [
        label,
        dense,
        bm25,
        round(scores.precision, 3),
        round(scores.recall, 3),
        round(scores.f1, 3),
    ]


def _comparison_row(number: int, e: IndexEvaluation) -> list[Scalar]:
    """One row of the embedder comparison table."""
    hybrid = e.modes["hybrid"]
    return [
        number,
        e.embed_model,
        round(hybrid.recall[6], 3),
        round(hybrid.mrr, 3),
        round(e.configured.f1, 3),
        round(hybrid.latency.p95, 1),
    ]


def render_report(
    evaluations: Sequence[IndexEvaluation],
    skipped: Sequence[Skipped],
    *,
    generated_at: str,
    gold_size: int,
    use_tradition: bool,
) -> str:
    """The Markdown report for every evaluated index, and a note for each skipped one."""
    lines = [
        "# Priest retrieval report",
        "",
        (
            f"Generated {generated_at} from {gold_size} gold questions"
            f"{' with the tradition filter' if use_tradition else ''}. "
            "Recall is over in-scope notes; BM25, dense and hybrid are compared."
        ),
        "",
    ]
    for note in skipped:
        lines += [f"SKIPPED: {note.reason}", ""]
    if len(evaluations) > 1:
        rows = [_comparison_row(n, e) for n, e in enumerate(evaluations, start=1)]
        lines += ["## Embedder comparison", ""]
        lines += markdown_table(
            [
                "index",
                "embed model",
                "hybrid recall@6",
                "hybrid MRR",
                "configured F1",
                "hybrid p95 ms",
            ],
            rows,
        )
        lines.append("")
    for number, evaluation in enumerate(evaluations, start=1):
        lines += _index_section(number, evaluation)
    return "\n".join(lines)


# ── command line ─────────────────────────────────────────────────────────

EmbedderFactory = Callable[[str, str], QueryEmbedder]


def _default_factory(base_url: str, model: str) -> QueryEmbedder:
    return OllamaEmbedder(base_url, model)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate priest-mode retrieval.")
    parser.add_argument(
        "--index-dir",
        action="append",
        type=Path,
        help="an index root; repeat once per embedding model (default: PRIEST_INDEX_DIR)",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ollama-url", default=settings.OLLAMA_BASE_URL)
    parser.add_argument("--use-tradition", action="store_true")
    parser.add_argument(
        "--top-k",
        type=int,
        default=settings.PRIEST_TOP_K,
        help="chunks the hybrid run returns, as the app does (default: PRIEST_TOP_K)",
    )
    parser.add_argument(
        "--dense-floor", type=float, default=settings.PRIEST_MIN_RELEVANCE_DENSE
    )
    parser.add_argument(
        "--bm25-floor", type=float, default=settings.PRIEST_MIN_RELEVANCE_BM25
    )
    return parser.parse_args(argv)


async def _evaluate_one(
    root: Path,
    gold: Sequence[GoldQuestion],
    args: argparse.Namespace,
    factory: EmbedderFactory,
) -> IndexEvaluation | Skipped:
    """Evaluate one index root, or say why it was skipped."""
    if not root.is_dir():
        return Skipped("no index directory")
    active = ActiveIndex(root)
    try:
        manifest = active.get().manifest
        embedder = factory(args.ollama_url, manifest.embed_model)
    except PriestIndexError as exc:
        return Skipped(f"no usable index or embedder: {exc}")
    try:
        try:
            info = await embedder.model_info()
        except PriestIndexError:
            return Skipped(f"Ollama is not reachable or lacks {manifest.embed_model}")
        try:
            active.check_digest(info)
        except PriestIndexError as exc:
            raise ScriptError(f"{root}: {exc}") from exc
        try:
            return await evaluate_index(
                active,
                embedder,
                gold,
                dense_floor=args.dense_floor,
                bm25_floor=args.bm25_floor,
                top_k=args.top_k,
                use_tradition=args.use_tradition,
            )
        except (PriestIndexError, ValueError) as exc:
            raise ScriptError(f"{root}: {exc}") from exc
    finally:
        if isinstance(embedder, OllamaEmbedder):
            await embedder.aclose()


def _write_outputs(
    roots: Sequence[Path],
    evaluations: Sequence[IndexEvaluation],
    skipped: Sequence[Skipped],
    args: argparse.Namespace,
    gold_size: int,
) -> list[Path]:
    """Write one summary JSON per evaluated index and the combined Markdown report."""
    now = datetime.now(timezone.utc)
    stamp = timestamp_slug(now)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    directories = [reports_dir(root) for root in roots]  # refuse any unsafe one first
    written: list[Path] = []
    for directory, evaluation in zip(directories, evaluations, strict=True):
        target = safe_report_path(directory, f"retrieval_{stamp}", ".json")
        directory.mkdir(parents=True, exist_ok=True)
        body = summary_json(
            evaluation,
            generated_at=generated_at,
            gold_size=gold_size,
            use_tradition=args.use_tradition,
        )
        target.write_text(
            json.dumps(body, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        written.append(target)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_report(
            evaluations,
            skipped,
            generated_at=generated_at,
            gold_size=gold_size,
            use_tradition=args.use_tradition,
        ),
        encoding="utf-8",
    )
    return [*written, args.report]


async def _run(args: argparse.Namespace, factory: EmbedderFactory) -> int:
    gold = load_gold(args.gold)
    roots = args.index_dir or [Path(settings.PRIEST_INDEX_DIR)]
    evaluations: list[IndexEvaluation] = []
    evaluated_roots: list[Path] = []
    skipped: list[Skipped] = []
    for root in roots:
        outcome = await _evaluate_one(root, gold, args, factory)
        if isinstance(outcome, Skipped):
            skipped.append(outcome)
            sys.stdout.write(f"SKIP: {outcome.reason} [{root}]\n")
        else:
            evaluations.append(outcome)
            evaluated_roots.append(root)
    if not evaluations:
        return 0
    written = _write_outputs(evaluated_roots, evaluations, skipped, args, len(gold))
    for evaluation in evaluations:
        c = evaluation.calibration
        sys.stdout.write(
            f"{evaluation.embed_model}: hybrid recall@6 "
            f"{evaluation.modes['hybrid'].recall[6]:.3f} (top {evaluation.top_k} chunks), "
            f"configured not-covered F1 {evaluation.configured.f1:.3f}, "
            f"calibrated F1 {c.f1:.3f} (in-sample)\n"
            f"PRIEST_MIN_RELEVANCE_DENSE={c.dense_floor} PRIEST_MIN_RELEVANCE_BM25={c.bm25_floor}\n"
        )
    sys.stdout.write("wrote " + ", ".join(str(p) for p in written) + "\n")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    embedder_factory: EmbedderFactory | None = None,
) -> int:
    """Run the evaluation; return 0 on success or skip, 2 on a real problem."""
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args, embedder_factory or _default_factory))
    except GoldError as exc:
        sys.stderr.write(f"gold set error: {exc}\n")
    except ScriptError as exc:
        sys.stderr.write(f"error: {exc}\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
