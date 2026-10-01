"""In-house BM25 over the study chunks, tuned for names and verse references.

Dense vectors handle paraphrase but are weak on proper nouns and transliterations
(Kisā Gotamī / Kisa Gotami, Aša / Asha) and on references such as ``2:255``. BM25
over folded, casefolded tokens covers exactly those: diacritics, modifier letters and
apostrophes (Qur'an, Qurʾān, Quran) and a few digraphs (Aša, Asha) all fold to one
form. Postings live in memory and are built from the chunks when an index version is
loaded, so a change to the folding needs no index rebuild and no version bump.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Final

from app.priest.types import Chunk

# Verse references stay whole; everything else is a run of letters or digits.
_TOKEN: Final = re.compile(r"\d+[:.]\d+|[^\W_]+")
# Dropped from questions only. Without this, a question made of "how", "do", "a"
# scores on filler words and looks covered.
_STOPWORDS: Final = frozenset(
    """a about after all also am an and any are as at be been but by can could did do
    does for from had has have how i if in into is it its me more most my no not of
    on or our should so some than that the their them then there these they this to
    up was we were what when where which who why will with would you your""".split()  # noqa: SIM905
)


# Letters whose usual English spelling is a digraph. They are replaced before the
# accents are stripped, since stripping would turn all of them into a bare letter.
_DIGRAPHS: Final = str.maketrans(
    {"š": "sh", "ś": "sh", "č": "ch", "ž": "zh", "ṣ": "s", "ṛ": "ri"}
)
# A possessive ('s) and an apostrophe between letters are dropped: Qur'an is Quran.
_POSSESSIVE: Final = re.compile(r"(?<=\w)['\u2018\u2019`]s\b")
_INNER_APOSTROPHE: Final = re.compile(r"(?<=\w)['\u2018\u2019`](?=\w)")
_DROPPED_CATEGORIES: Final = frozenset({"Mn", "Lm"})


def fold(text: str) -> str:
    """Fold *text* so the spellings of one name compare equal.

    Case, combining marks, modifier letters (the ``ʿ`` and ``ʾ`` of Arabic
    transliteration) and apostrophes inside words are removed, and ``š``, ``č``,
    ``ṣ`` and ``ṛ`` become ``sh``, ``ch``, ``s`` and ``ri``. So ``Kisā Gotamī`` and
    ``kisa gotami``, ``Qurʾān`` and ``Qur'an`` and ``Quran``, ``Aša`` and ``Asha``
    are equal.
    """
    composed = unicodedata.normalize("NFC", text).casefold().translate(_DIGRAPHS)
    decomposed = unicodedata.normalize("NFKD", composed)
    stripped = "".join(
        ch for ch in decomposed if unicodedata.category(ch) not in _DROPPED_CATEGORIES
    )
    return _INNER_APOSTROPHE.sub("", _POSSESSIVE.sub("", stripped))


def tokenize(text: str) -> list[str]:
    """Split *text* into folded tokens, keeping ``2:255`` and ``30.3`` whole."""
    return _TOKEN.findall(fold(text))


def _indexed_text(chunk: Chunk) -> str:
    """What BM25 sees: the title and headings as well as the body, so names match."""
    return f"{chunk.note_title} {' '.join(chunk.heading_path)} {chunk.text}"


class Bm25Index:
    """Okapi BM25 over a fixed list of chunks, addressed by row index."""

    def __init__(
        self, chunks: Sequence[Chunk], k1: float = 1.2, b: float = 0.75
    ) -> None:
        """Index *chunks*; search results refer to positions in this sequence."""
        self._k1 = k1
        self._b = b
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._lengths: list[int] = []
        for row, chunk in enumerate(chunks):
            tokens = tokenize(_indexed_text(chunk))
            self._lengths.append(len(tokens))
            counts: dict[str, int] = defaultdict(int)
            for token in tokens:
                counts[token] += 1
            for token, tf in counts.items():
                postings[token].append((row, tf))
        self._postings = dict(postings)
        total = len(self._lengths)
        self._avg_len = (sum(self._lengths) / total) if total else 0.0
        # The "+1" inside the log keeps idf positive even for terms in most chunks.
        self._idf = {
            term: math.log(1.0 + (total - len(rows) + 0.5) / (len(rows) + 0.5))
            for term, rows in self._postings.items()
        }

    def search(
        self, query: str, k: int, allowed: frozenset[int] | None = None
    ) -> list[tuple[int, float]]:
        """Rank chunks for *query*.

        Args:
            query: The question. Stop words are ignored.
            k: How many hits to return at most.
            allowed: If given, only these rows can be returned.

        Returns:
            ``(row, score)`` pairs, best first, ties broken by row. Chunks that share
            no term with the query are never returned.
        """
        terms = self._query_terms(query)
        if k <= 0 or not terms:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in terms:
            for row, tf in self._postings.get(term, ()):
                if allowed is None or row in allowed:
                    scores[row] += self._idf[term] * self._term_weight(tf, row)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:k]

    def coverage_score(
        self, query: str, rows: Collection[int], max_terms: int
    ) -> float:
        """The best score any of *rows* gets from its *max_terms* best-matching terms.

        The plain score adds up every matching term, so a long rambling question can
        reach a high total through a handful of ordinary words. Counting only the few
        best terms keeps the number about names and rare words, whatever the length of
        the question; a question of *max_terms* or fewer terms scores as in ``search``.

        Args:
            query: The question. Stop words are ignored.
            rows: The chunks to score; others are never looked at.
            max_terms: How many matching terms count at most.

        Returns:
            The highest capped score, or 0.0 when no row shares a term with the query.
        """
        wanted = frozenset(rows)
        contributions: dict[int, list[float]] = defaultdict(list)
        for term in self._query_terms(query):
            for row, tf in self._postings.get(term, ()):
                if row in wanted:
                    contributions[row].append(
                        self._idf[term] * self._term_weight(tf, row)
                    )
        best = (
            sum(sorted(values, reverse=True)[:max_terms])
            for values in contributions.values()
        )
        return max(best, default=0.0)

    @staticmethod
    def _query_terms(query: str) -> dict[str, None]:
        """The distinct non-stop-word tokens of *query*, in order."""
        return dict.fromkeys(t for t in tokenize(query) if t not in _STOPWORDS)

    def _term_weight(self, tf: int, row: int) -> float:
        """The saturating, length-normalised part of BM25 for one term in one chunk."""
        norm = 1.0 - self._b + self._b * self._lengths[row] / self._avg_len
        return tf * (self._k1 + 1.0) / (tf + self._k1 * norm)
