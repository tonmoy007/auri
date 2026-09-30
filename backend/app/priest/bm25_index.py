"""In-house BM25 over the study chunks, tuned for names and verse references.

Dense vectors handle paraphrase but are weak on proper nouns and transliterations
(Kisā Gotamī / Kisa Gotami, Aša / Asha) and on references such as ``2:255``. BM25
over diacritic-folded, casefolded tokens covers exactly those. Postings live in
memory; the index is built once per loaded index version.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
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


def fold(text: str) -> str:
    """Remove diacritics and case, so ``Kisā Gotamī`` and ``kisa gotami`` are equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


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
        terms = dict.fromkeys(t for t in tokenize(query) if t not in _STOPWORDS)
        if k <= 0 or not terms:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in terms:
            for row, tf in self._postings.get(term, ()):
                if allowed is None or row in allowed:
                    scores[row] += self._idf[term] * self._term_weight(tf, row)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:k]

    def _term_weight(self, tf: int, row: int) -> float:
        """The saturating, length-normalised part of BM25 for one term in one chunk."""
        norm = 1.0 - self._b + self._b * self._lengths[row] / self._avg_len
        return tf * (self._k1 + 1.0) / (tf + self._k1 * norm)
