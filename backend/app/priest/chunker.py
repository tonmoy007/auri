"""Split a clean note into retrievable chunks that each carry their own context.

Rules (plan section 4.4): split at H2, then H3; every chunk starts with a breadcrumb
``Note title › Heading``; the target is 250-380 tokens and 450 is a hard ceiling,
breadcrumb included, with tokens estimated as characters / 4. A section under 60
tokens merges with its next sibling under the same H2 (a small last one merges back).
Long text splits at paragraph, then sentence, boundaries with a one-sentence overlap.
A chunk never crosses an H2, never splits a table row (a table that spans chunks
repeats its header) and never splits a quote block.

One rule gives way to the ceiling: a quote block or table row that alone exceeds it
cannot be kept whole, so a quote is demoted to plain text (and is then not reported in
``quote_blocks``, so it cannot be offered as a verified quotation) and a row is cut.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from itertools import takewhile
from typing import Final, Literal

from app.priest.types import Chunk, QuoteBlock
from app.priest.vault_cleaner import Block, CleanNote, CleanSection, TableBlock

CHUNKER_VERSION: Final = "1"
CHARS_PER_TOKEN: Final = 4
MIN_TARGET_TOKENS: Final = 250
MAX_TARGET_TOKENS: Final = 380
HARD_MAX_TOKENS: Final = 450
MERGE_BELOW_TOKENS: Final = 60

_TARGET_CHARS: Final = MAX_TARGET_TOKENS * CHARS_PER_TOKEN
_HARD_CHARS: Final = HARD_MAX_TOKENS * CHARS_PER_TOKEN
_MIN_CHARS: Final = MIN_TARGET_TOKENS * CHARS_PER_TOKEN
_CRUMB_SEP: Final = " › "
_CRUMB_PART_MAX: Final = 120
_MAX_HEADER_CHARS: Final = 300
_MAX_OVERLAP_CHARS: Final = 300
_SENTENCE: Final = re.compile(r"(?<=[.!?…])\s+")
_ANCHOR_STRIP: Final = str.maketrans("", "", "#|^:[]")

Kind = Literal["para", "quote", "row", "head"]


def estimate_tokens(text: str) -> int:
    """Estimate tokens as characters / 4, rounded up; no tokenizer dependency."""
    return -(-len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class _Atom:
    """Text the packer never divides: a paragraph piece, a quote, a row, a header."""

    text: str
    kind: Kind
    sep: str = "\n\n"
    quote: QuoteBlock | None = None
    header: str | None = None


@dataclass
class _Unit:
    """Blocks that share a heading path, before they are packed."""

    path: tuple[str, ...]
    blocks: list[Block] = field(default_factory=list)


@dataclass
class _Body:
    """The chunk being filled."""

    parts: list[str] = field(default_factory=list)
    quotes: list[QuoteBlock] = field(default_factory=list)
    atoms: list[_Atom] = field(default_factory=list)
    length: int = 0

    def add(self, atom: _Atom) -> None:
        """Append an atom, with its separator unless it is the first."""
        self.parts.append(atom.text if not self.atoms else atom.sep + atom.text)
        self.length += len(self.parts[-1])
        self.atoms.append(atom)
        if atom.quote is not None:
            self.quotes.append(atom.quote)

    @property
    def header(self) -> str | None:
        """The header of the table the last atom belongs to, if any."""
        last = self.atoms[-1] if self.atoms else None
        return last.header if last is not None else None


def _clip(text: str) -> str:
    """Shorten one breadcrumb part so a silly title cannot eat the chunk budget."""
    return text if len(text) <= _CRUMB_PART_MAX else text[: _CRUMB_PART_MAX - 1] + "…"


def _breadcrumb(title: str, path: tuple[str, ...]) -> str:
    """``Title › H2 › H3``, which prefixes every chunk."""
    return _CRUMB_SEP.join(_clip(part) for part in (title, *path))


def _anchor(note_path: str, path: tuple[str, ...]) -> str:
    """An Obsidian-style link target: the note path plus ``#heading`` parts."""
    names = [" ".join(part.translate(_ANCHOR_STRIP).split()) for part in path]
    return "#".join([note_path.removesuffix(".md"), *(n for n in names if n)])


def _header_line(table: TableBlock) -> str | None:
    """The line repeated above table rows, or ``None`` when there is no useful header."""
    names = [name for name in table.header if name]
    line = f"Table: {' | '.join(names)}"
    return line if names and len(line) <= _MAX_HEADER_CHARS else None


def _quote_text(quote: QuoteBlock) -> str:
    """A quote as it reads in a chunk: the words in quotation marks, then the source."""
    source = f" — {quote.attribution}" if quote.attribution else ""
    return f'"{quote.text}"{source}'


def _render(block: Block) -> str:
    """A block as plain text, used to measure it."""
    if isinstance(block, TableBlock):
        return "\n".join([_header_line(block) or "", *block.rows])
    return _quote_text(block) if isinstance(block, QuoteBlock) else block


def _cut(text: str, budget: int) -> list[str]:
    """Cut *text* into parts of at most *budget* characters, at spaces where possible."""
    parts: list[str] = []
    budget = max(budget, 1)
    while len(text) > budget:
        at = text.rfind(" ", 0, budget + 1)
        at = at if at > 0 else budget
        parts.append(text[:at].rstrip())
        text = text[at:].lstrip()
    return [*parts, text] if text else parts


def _parts(line: str, budget: int) -> list[str]:
    """The sentences of one line, each cut further if it alone outgrows *budget*."""
    sentences = _SENTENCE.split(line)
    return [part for sentence in sentences for part in _cut(sentence, budget) if part]


def _para_atoms(text: str, budget: int) -> list[_Atom]:
    """One atom per paragraph, or per sentence when the paragraph outgrows *budget*."""
    if len(text) <= budget:
        return [_Atom(text, "para")]
    atoms: list[_Atom] = []
    for n, line in enumerate(text.split("\n")):
        for k, part in enumerate(_parts(line, budget)):
            atoms.append(_Atom(part, "para", sep=" " if k else ("\n" if n else "\n\n")))
    return atoms


def _table_atoms(table: TableBlock, budget: int) -> list[_Atom]:
    """One atom per row, each remembering the header to repeat above it."""
    header = _header_line(table)
    room = budget - len(header or "") - 4
    atoms: list[_Atom] = []
    for row in table.rows:
        pieces = _cut(row, room) if len(row) > room else [row]
        atoms.extend(_Atom(p, "row", sep="\n", header=header) for p in pieces)
    return atoms


def _atoms(blocks: list[Block], target: int, hard: int) -> list[_Atom]:
    """Turn blocks into atoms sized for a chunk whose breadcrumb leaves these budgets."""
    atoms: list[_Atom] = []
    for block in blocks:
        if isinstance(block, TableBlock):
            atoms.extend(_table_atoms(block, hard))
        elif isinstance(block, QuoteBlock):
            text = _quote_text(block)
            kept = len(text) <= hard
            atoms.extend(
                [_Atom(text, "quote", quote=block)]
                if kept
                else _para_atoms(text, target)
            )
        elif block:
            atoms.extend(_para_atoms(block, target))
    return atoms


def _last_sentence(atom: _Atom) -> str:
    """The final sentence of a paragraph atom, the overlap for the next chunk."""
    return _SENTENCE.split(atom.text.strip())[-1]


class _Packer:
    """Fills chunk bodies atom by atom, flushing at the target size."""

    def __init__(self, target: int, hard: int, minimum: int) -> None:
        self.target, self.hard, self.minimum = target, hard, minimum
        self.done: list[_Body] = []
        self.cur = _Body()

    def feed(self, atom: _Atom) -> None:
        """Place *atom* in the current chunk, or start a new one when it does not fit."""
        cost = self._cost(atom)
        total = self.cur.length + cost
        fits_target = not self.cur.atoms or total <= self.target
        topping_up = self.cur.length < self.minimum and total <= self.hard
        if fits_target or topping_up:
            self._place(atom)
        else:
            self._next_chunk(atom)

    def finish(self) -> list[_Body]:
        """Close the last chunk and return every body."""
        return [*self.done, self.cur] if self.cur.atoms else self.done

    def _needs_header(self, atom: _Atom) -> str | None:
        """The header line to put above *atom* when it opens a table piece."""
        if atom.kind != "row" or atom.header is None:
            return None
        return None if self.cur.header == atom.header else atom.header

    def _cost(self, atom: _Atom) -> int:
        """Characters *atom* adds, counting a header line it would pull in."""
        header = self._needs_header(atom)
        return len(atom.sep) + len(atom.text) + (len(header) + 2 if header else 0)

    def _place(self, atom: _Atom) -> None:
        """Append *atom*, preceded by its table header when it starts a table piece."""
        header = self._needs_header(atom)
        if header:
            self.cur.add(_Atom(header, "head", header=header))
        self.cur.add(atom)

    def _next_chunk(self, atom: _Atom) -> None:
        """Flush the current chunk and open the next, repeating one sentence of overlap."""
        previous = self.cur
        self.done.append(previous)
        self.cur = _Body()
        overlap = self._overlap(previous, atom)
        if overlap:
            self.cur.add(overlap)
        self._place(atom)

    def _overlap(self, previous: _Body, atom: _Atom) -> _Atom | None:
        """The previous chunk's last sentence, when repeating it is useful and fits."""
        last = previous.atoms[-1]
        if len(previous.atoms) < 2 or last.kind != "para" or atom.kind != "para":
            return None
        sentence = _last_sentence(last)
        fits = len(sentence) + 2 + len(atom.sep) + len(atom.text) <= self.target
        return (
            _Atom(sentence, "para")
            if sentence and len(sentence) <= _MAX_OVERLAP_CHARS and fits
            else None
        )


def _common(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """The shared leading headings of two paths."""
    return tuple(
        part for part, _ in takewhile(lambda p: p[0] == p[1], zip(a, b, strict=False))
    )


def _join(first: _Unit, second: _Unit) -> _Unit:
    """Merge two units under their common heading, keeping the deeper headings as text."""
    common = _common(first.path, second.path)
    blocks: list[Block] = []
    for unit in (first, second):
        if unit.path != common:
            blocks.append(_CRUMB_SEP.join(unit.path[len(common) :]))
        blocks.extend(unit.blocks)
    return _Unit(common, blocks)


def _size(unit: _Unit) -> int:
    """Tokens in a unit's text, headings excluded."""
    return estimate_tokens("\n\n".join(_render(b) for b in unit.blocks))


def _merge_small(units: list[_Unit]) -> list[_Unit]:
    """Merge a unit under the merge threshold into the next; a small last one into the previous."""
    merged: list[_Unit] = []
    carry: _Unit | None = None
    for n, unit in enumerate(units):
        current = _join(carry, unit) if carry else unit
        carry = (
            current
            if _size(current) < MERGE_BELOW_TOKENS and n < len(units) - 1
            else None
        )
        if carry is None:
            merged.append(current)
    if len(merged) > 1 and _size(merged[-1]) < MERGE_BELOW_TOKENS:
        merged[-2:] = [_join(merged[-2], merged[-1])]
    return merged


def _groups(sections: tuple[CleanSection, ...]) -> list[list[CleanSection]]:
    """Consecutive sections under the same H2, so nothing is ever merged across one."""
    groups: list[list[CleanSection]] = []
    for section in sections:
        if groups and groups[-1][0].heading_path[:1] == section.heading_path[:1]:
            groups[-1].append(section)
        else:
            groups.append([section])
    return groups


def _bodies(unit: _Unit, crumb: str) -> list[_Body]:
    """Pack one unit's blocks into chunk bodies within the budgets its breadcrumb leaves."""
    target = _TARGET_CHARS - len(crumb) - 2
    hard = _HARD_CHARS - len(crumb) - 2
    packer = _Packer(target, hard, _MIN_CHARS - len(crumb) - 2)
    for atom in _atoms(unit.blocks, target, hard):
        packer.feed(atom)
    return packer.finish()


def _chunk_group(note: CleanNote, group: list[CleanSection], first: int) -> list[Chunk]:
    """Chunk the sections under one H2; *first* is the ordinal of the first chunk."""
    chunks: list[Chunk] = []
    units = [_Unit(s.heading_path, list(s.blocks)) for s in group if s.blocks]
    for unit in _merge_small(units):
        crumb = _breadcrumb(note.title, unit.path)
        for body in _bodies(unit, crumb):
            chunks.append(_build(note, unit.path, crumb, body, first + len(chunks)))
    return chunks


def chunk_note(note: CleanNote) -> list[Chunk]:
    """Chunk a cleaned note; the result is ordered and each id depends on its text."""
    chunks: list[Chunk] = []
    for group in _groups(note.sections):
        chunks.extend(_chunk_group(note, group, len(chunks)))
    return chunks


def _build(
    note: CleanNote, path: tuple[str, ...], crumb: str, body: _Body, ordinal: int
) -> Chunk:
    """Assemble a chunk and its stable id."""
    text = f"{crumb}\n\n{''.join(body.parts)}"
    key = f"{note.path}|{'/'.join(path)}|{ordinal}|{text}"
    return Chunk(
        chunk_id=hashlib.sha1(key.encode("utf-8")).hexdigest()[:16],
        note_path=note.path,
        note_title=note.title,
        heading_path=path,
        obsidian_anchor=_anchor(note.path, path),
        note_type=note.note_type,
        traditions=note.traditions,
        text=text,
        quote_blocks=tuple(body.quotes),
        char_len=len(text),
    )
