"""Turn one raw Obsidian note into plain blocks under clean headings.

Only the prose, the quotations and the table facts survive. Wikilinks keep their
visible text, images and embeds are dropped and counted, callouts and tables are
flattened, and emoji are removed from headings. Every emitted string has runs of
``<<<`` and ``>>>`` replaced by a space (``app.llm.fencing``), so note text can never
close a prompt fence; the words around such a run are kept.

Behaviour worth knowing, because the plan leaves it open:

* ``unresolved_links`` needs the set of note names: pass ``known_targets`` (see
  ``link_targets``). Without it the cleaner cannot tell, and reports 0.
* ``dropped_embeds`` counts ``![[embed]]`` and ``![alt](image)`` alike.
* A quote callout's title is its attribution only when no ``— source`` follows the
  quote, or the source that follows is not a short first-line source; a plain
  blockquote in a ``story`` note is narrative and carries none.
* A code fence follows the CommonMark rule (a fence closes on the same character, at
  least as many times). One that never closes hides the rest of the note, and is
  reported in ``CleanNote.unclosed_fence`` so the build can warn about it.
* The inline patterns have bounded bodies, so a garbled or hostile note costs time in
  proportion to its size, never the square of it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath
from re import Match
from typing import Final, TypeAlias

from app.llm.fencing import strip_fence_runs
from app.priest.types import QuoteBlock
from app.priest.vault_rules import (
    scalar_text,
    split_frontmatter,
    tags_of,
    traditions_for,
)

CLEANER_VERSION: Final = "2"
DEFAULT_NOTE_TYPE: Final = "note"
_STORY: Final = "story"
_MAX_TRAILING_SOURCE: Final = 100
# The longest wikilink, link text or emphasised span the inline patterns look for. A
# pattern that scans to the end of the line for a closer that is not there would cost
# the square of the line length; a bound makes it cost the length times the bound.
_SPAN: Final = 600

_COMMENT_MARKS: Final = (("%%", "%%"), ("<!--", "-->"))
_FENCE_OPEN: Final = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING: Final = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_RULE: Final = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_IMAGE_LINE: Final = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_CAPTION: Final = re.compile(r"^\s*([*_])(?![*_\s]).+\1\s*$")
_CALLOUT: Final = re.compile(r"^\s*>\s*\[!([\w-]+)\][+-]?[ \t]*(.*)$")
_QUOTE_PREFIX: Final = re.compile(r"^\s*(?:>[ \t]?)+")
# Each piece is followed by its own whitespace and nothing is optional twice in a row,
# so a long run of spaces cannot be split between two patterns in many ways.
_TABLE_SEP: Final = re.compile(r"^\s*(?:\|\s*)?:?-+:?\s*(?:\|\s*:?-+:?\s*)*(?:\|\s*)?$")
_WIKILINK_SPAN: Final = re.compile(rf"\[\[[^\[\]]{{0,{_SPAN}}}\]\]")
_PIPE: Final = "\ue000"  # stands in for a pipe inside a wikilink while a row is split
_CELL_SPLIT: Final = re.compile(r"(?<!\\)\|")
_LIST_ITEM: Final = re.compile(r"^\s*[*+-][ \t]+")

_EMBED: Final = re.compile(rf"!\[\[[^\[\]]{{0,{_SPAN}}}\]\]")
_IMAGE: Final = re.compile(rf"!\[[^\[\]]{{0,{_SPAN}}}\]\([^)]{{0,{_SPAN}}}\)")
_WIKILINK: Final = re.compile(
    rf"\[\[([^\[\]|#]{{0,{_SPAN}}})(?:#([^\[\]|]{{0,{_SPAN}}}))?"
    rf"(?:\\?\|([^\[\]]{{0,{_SPAN}}}))?\]\]"
)
_MD_LINK: Final = re.compile(rf"\[([^\[\]]{{0,{_SPAN}}})\]\([^)]{{0,{_SPAN}}}\)")
_URL: Final = re.compile(r"https?://\S+")
_HTML_TAG: Final = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_HIGHLIGHT: Final = re.compile(rf"==(?=\S)(.{{1,{_SPAN}}}?)(?<=\S)==")
_BOLD: Final = re.compile(rf"(\*\*|__)(?=\S)(.{{1,{_SPAN}}}?)(?<=\S)\1")
_ITALIC: Final = re.compile(
    rf"(?<![\w*_])([*_])(?=[^\s*_])(.{{1,{_SPAN}}}?)(?<=[^\s*_])\1(?![\w*_])"
)
_BLOCK_ID: Final = re.compile(r"[ \t]\^[\w-]+[ \t]*$", re.MULTILINE)
_SPACES: Final = re.compile(r"[ \t ]+")

_OPEN_QUOTE: Final = re.compile(r"^[\"“‘«]")
_CLOSE_THEN_DASH: Final = re.compile(r"([\"”’»])\s+[—–]{1,2}\s+")
_DASH_LINE: Final = re.compile(r"^(?:[—–]{1,2}|--)\s*(\S.*)$")
_TRAILING_SOURCE: Final = re.compile(r"\s[—–][ \t]+([^—–\n]+)$")
_WRAPPERS: Final = {'"': '"', "“": "”", "‘": "’", "«": "»"}
_DECORATIVE: Final = frozenset({"So", "Sk"})
_INVISIBLE: Final = frozenset({0x200D, 0xFE0E, 0xFE0F, 0x20E3})


@dataclass(frozen=True)
class TableBlock:
    """A table: its column names and each row already rendered, never to be split."""

    header: tuple[str, ...]
    rows: tuple[str, ...]


Block: TypeAlias = str | TableBlock | QuoteBlock


@dataclass(frozen=True)
class CleanSection:
    """The blocks under one heading. ``()`` is the text before the first H2."""

    heading_path: tuple[str, ...]
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class CleanNote:
    """A note reduced to metadata plus clean sections."""

    path: str
    title: str
    note_type: str
    tags: tuple[str, ...]
    traditions: tuple[str, ...]
    sources: tuple[str, ...]
    sections: tuple[CleanSection, ...]
    unresolved_links: int
    dropped_embeds: int
    unclosed_fence: bool = False


@dataclass
class _Stats:
    """Running counters, and the note names links are checked against."""

    known: AbstractSet[str] | None = None
    unresolved: int = 0
    embeds: int = 0

    def drop_embed(self) -> str:
        """Count a dropped embed and return the empty replacement."""
        self.embeds += 1
        return ""

    def check_link(self, target: str) -> None:
        """Count *target* as unresolved when the note names are known and it is absent."""
        if self.known is None:
            return
        key = target.strip().lower().removesuffix(".md")
        if key not in self.known and key.rsplit("/", 1)[-1] not in self.known:
            self.unresolved += 1


def link_targets(rel_paths: Iterable[str]) -> frozenset[str]:
    """Names a wikilink can resolve to: each note's path and its bare name, lower-case."""
    names: set[str] = set()
    for rel_path in rel_paths:
        key = rel_path.lower().removesuffix(".md")
        names.update((key, key.rsplit("/", 1)[-1]))
    return frozenset(names)


def _link_text(match: Match[str], stats: _Stats) -> str:
    """The visible text of a wikilink: alias, else last path segment, else heading."""
    target = match.group(1).strip()
    heading = (match.group(2) or "").strip()
    alias = (match.group(3) or "").strip()
    if target:
        stats.check_link(target)
    if alias:
        return alias
    if target:
        return target.rsplit("/", 1)[-1].removesuffix(".md")
    return heading


def _strip_comments(text: str) -> str:
    """Remove ``%%...%%`` and ``<!--...-->`` comments; a marker never closed stays.

    Done with ``str.find`` rather than a lazy pattern: a pattern re-scans to the end of
    the text for every unclosed marker, which costs the square of the text length.
    """
    parts: list[str] = []
    done = 0
    exhausted: set[str] = set()
    while True:
        starts = [
            (text.find(opener, done), opener, closer)
            for opener, closer in _COMMENT_MARKS
            if opener not in exhausted
        ]
        starts = [entry for entry in starts if entry[0] != -1]
        if not starts:
            break
        start, opener, closer = min(starts)
        end = text.find(closer, start + len(opener))
        if end == -1:
            exhausted.add(opener)
            continue
        parts.append(text[done:start])
        done = end + len(closer)
    parts.append(text[done:])
    return "".join(parts)


def _clean_text(text: str) -> str:
    """Remove fence runs and tidy the spaces; the last step for any emitted string."""
    return _SPACES.sub(" ", strip_fence_runs(text)).strip()


def _inline(text: str, stats: _Stats) -> str:
    """Reduce inline Markdown and Obsidian syntax on one line to its plain text."""
    text = _EMBED.sub(lambda _m: stats.drop_embed(), text)
    text = _IMAGE.sub(lambda _m: stats.drop_embed(), text)
    text = _WIKILINK.sub(lambda m: _link_text(m, stats), text)
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", _URL.sub("", text))
    text = _ITALIC.sub(r"\2", _BOLD.sub(r"\2", _HIGHLIGHT.sub(r"\1", text)))
    text = _BLOCK_ID.sub("", text.replace("`", ""))
    return _clean_text(text)


def _strip_symbols(text: str) -> str:
    """Remove emoji and decorative symbols (Unicode So and Sk) and their joiners."""
    kept = (
        ch
        for ch in text
        if unicodedata.category(ch) not in _DECORATIVE and ord(ch) not in _INVISIBLE
    )
    return _SPACES.sub(" ", "".join(kept)).strip()


def _unwrap_quotes(text: str) -> str:
    """Drop one pair of quotation marks that wraps the whole text and nothing more."""
    text = text.strip()
    closer = _WRAPPERS.get(text[:1])
    if closer is None or len(text) < 2 or text[-1] != closer:
        return text
    inner = text[1:-1]
    if any(mark in inner for mark in (text[0], closer)):
        return text
    return inner.strip()


def _split_attribution(lines: list[str]) -> tuple[str, str | None]:
    """Split quote lines into the quoted text and the source the note names for it.

    A source is the rest of one line, at most ``_MAX_TRAILING_SOURCE`` characters. A
    longer one, or text that runs on over several lines, is commentary and not a
    source; the caller then falls back to the callout title.
    """
    dash = _DASH_LINE.match(lines[-1]) if len(lines) > 1 else None
    if dash and len(dash.group(1).strip()) <= _MAX_TRAILING_SOURCE:
        return "\n".join(lines[:-1]), dash.group(1).strip()
    text = "\n".join(lines)
    closers = list(_CLOSE_THEN_DASH.finditer(text))
    if closers:
        split = _after_closing_quote(text, closers[-1])
        if split is not None:
            return split
    trailing = _TRAILING_SOURCE.search(text)
    if trailing and len(trailing.group(1)) <= _MAX_TRAILING_SOURCE:
        return text[: trailing.start()], trailing.group(1).strip()
    return text, None


def _after_closing_quote(text: str, closer: Match[str]) -> tuple[str, str] | None:
    """Split at a closing quote and dash: the quote, and the first line after the dash.

    Any lines after that first one stay with the quote, so nothing is lost. ``None``
    when the first line is too long to be a source.
    """
    first, _, rest = text[closer.end() :].partition("\n")
    source = first.strip()
    if not source or len(source) > _MAX_TRAILING_SOURCE:
        return None
    quote = text[: closer.end(1)]
    return (f"{quote}\n{rest.strip()}" if rest.strip() else quote), source


def _quote_block(
    lines: list[str], label: str | None, narrative: bool
) -> QuoteBlock | None:
    """Build a quote block, or ``None`` when nothing is left to quote."""
    lines = [line for line in lines if line]
    if narrative:
        text, attribution = "\n".join(lines), None
    else:
        text, attribution = _split_attribution(lines)
        attribution = attribution or label or None
    text = _unwrap_quotes(text)
    return QuoteBlock(text, attribution, narrative) if text else None


def _starts_table(lines: list[str], i: int) -> bool:
    """Whether a Markdown table begins at line *i*: a pipe row, then its separator."""
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    return "|" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1]) is not None


def _starts_block(lines: list[str], i: int) -> bool:
    """Whether line *i* ends a paragraph because something else starts there."""
    line = lines[i]
    if not line.strip() or _HEADING.match(line) or _RULE.match(line):
        return True
    return bool(
        _QUOTE_PREFIX.match(line) or _IMAGE_LINE.match(line) or _starts_table(lines, i)
    )


class _Parser:
    """Walks the body lines once, filling sections and the counters."""

    def __init__(self, lines: list[str], narrative: bool, stats: _Stats) -> None:
        self.lines, self.narrative, self.stats = lines, narrative, stats
        self.h1: str | None = None
        self.h2: str | None = None
        self.items: list[tuple[tuple[str, ...], list[Block]]] = [((), [])]

    def parse(self) -> tuple[CleanSection, ...]:
        """Consume every line and return the non-empty sections."""
        i = 0
        while i < len(self.lines):
            i = self._step(i)
        return tuple(CleanSection(p, tuple(b)) for p, b in self.items if b)

    def _step(self, i: int) -> int:
        """Handle the block starting at line *i*; return the next unread line."""
        line = self.lines[i]
        if not line.strip() or _RULE.match(line):
            return i + 1
        if heading := _HEADING.match(line):
            self._heading(len(heading.group(1)), _heading_text(heading.group(2)))
            return i + 1
        if _IMAGE_LINE.match(line):
            self.stats.embeds += 1
            has_caption = i + 1 < len(self.lines) and _CAPTION.match(self.lines[i + 1])
            return i + (2 if has_caption else 1)
        if _QUOTE_PREFIX.match(line):
            return self._quote(i)
        if _starts_table(self.lines, i):
            return self._table(i)
        return self._paragraph(i)

    def _add(self, block: Block | None) -> None:
        """Append a block to the current section."""
        if block:
            self.items[-1][1].append(block)

    def _heading(self, level: int, text: str) -> None:
        """Open a section for H2 and H3; fold H4+ into text; remember the first H1."""
        name = _clean_text(_strip_symbols(_inline(text, self.stats)))
        if not name:
            return
        if level == 1 and self.h1 is None and self.h2 is None:
            self.h1 = name
        elif level >= 4:
            self._add(name)
        elif level == 3 and self.h2:
            self.items.append(((self.h2, name), []))
        else:
            self.h2 = name if level <= 2 else self.h2
            self.items.append(((name,), []))

    def _paragraph(self, i: int) -> int:
        """Collect consecutive prose lines into one paragraph."""
        start = i
        while i < len(self.lines) and (i == start or not _starts_block(self.lines, i)):
            i += 1
        raw = "\n".join(_LIST_ITEM.sub("- ", line) for line in self.lines[start:i])
        # Cleaned as one text so a wikilink that wraps onto the next line still matches.
        lines = (line.strip() for line in _inline(raw, self.stats).split("\n"))
        self._add("\n".join(line for line in lines if line))
        return i

    def _quote(self, i: int) -> int:
        """Collect a run of ``>`` lines as a callout or a plain blockquote."""
        first = self.lines[i]
        callout = _CALLOUT.match(first)
        start = i
        while i < len(self.lines) and _QUOTE_PREFIX.match(self.lines[i]):
            i += 1
        body = [
            _inline(_QUOTE_PREFIX.sub("", line), self.stats)
            for line in self.lines[start + (1 if callout else 0) : i]
        ]
        if callout:
            self._callout(
                callout.group(1).lower(), _inline(callout.group(2), self.stats), body
            )
        else:
            self._add(_quote_block(body, None, self.narrative))
        return i

    def _callout(self, kind: str, title: str, body: list[str]) -> None:
        """A quote callout becomes a quote block; any other becomes a paragraph."""
        body = [line for line in body if line]
        if kind != "quote":
            self._add("\n".join([line for line in [title, *body] if line]))
        elif _OPEN_QUOTE.match(title):
            self._add(_quote_block([title, *body], None, False))
        else:
            self._add(_quote_block(body or [title], title if body else None, False))

    def _table(self, i: int) -> int:
        """Collect a table, rendering each row as ``Header: value; Header: value``."""
        header = self._cells(self.lines[i])
        i += 2
        rows: list[str] = []
        while i < len(self.lines) and "|" in self.lines[i] and self.lines[i].strip():
            rendered = _render_row(header, self._cells(self.lines[i]))
            rows.extend([rendered] if rendered else [])
            i += 1
        self._add(TableBlock(header, tuple(rows)) if rows else None)
        return i

    def _cells(self, line: str) -> tuple[str, ...]:
        """The cleaned cells of one table row; a pipe inside a wikilink is no divider."""
        inner = line.strip().removeprefix("|").removesuffix("|")
        inner = _WIKILINK_SPAN.sub(_hide_pipes, inner)
        return tuple(
            _inline(cell.replace("\\|", "|").replace(_PIPE, "|"), self.stats)
            for cell in _CELL_SPLIT.split(inner)
        )


def _heading_text(raw: str) -> str:
    """A heading's text without its optional closing hashes (``## Title ##``)."""
    text = raw.rstrip(" \t")
    without_hashes = text.rstrip("#")
    if without_hashes and without_hashes[-1] in " \t":
        return without_hashes.rstrip(" \t")
    return text


def _hide_pipes(match: Match[str]) -> str:
    """Swap the pipes of a wikilink, escaped or not, for a placeholder."""
    return match.group().replace("\\|", _PIPE).replace("|", _PIPE)


def _render_row(header: tuple[str, ...], cells: tuple[str, ...]) -> str:
    """Render one table row, leaving out empty cells."""
    pairs = []
    for n, cell in enumerate(cells):
        name = header[n] if n < len(header) else ""
        if cell:
            pairs.append(f"{name}: {cell}" if name else cell)
    return "; ".join(pairs)


def _fence_opener(line: str) -> tuple[str, int] | None:
    """The character and length of a fence that opens on *line*, or ``None``.

    A backtick fence cannot have a backtick in its info string, so a line such as
    ``` ```inline``` ``` is prose.
    """
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    marks, info = match.groups()
    if marks[0] == "`" and "`" in info:
        return None
    return marks[0], len(marks)


def _closes_fence(line: str, opener: tuple[str, int]) -> bool:
    """Whether *line* closes a fence: the same character, at least as long, no text."""
    stripped = line.strip()
    char, length = opener
    return len(stripped) >= length and stripped == char * len(stripped)


def _drop_code_fences(lines: list[str]) -> tuple[list[str], bool]:
    """Remove fenced code blocks (dataview included), by the CommonMark rule.

    Returns:
        The remaining lines, and whether a fence was left open, which runs to the end
        of the note and takes the rest of it with it.
    """
    kept: list[str] = []
    opener: tuple[str, int] | None = None
    for line in lines:
        if opener is None:
            opener = _fence_opener(line)
            if opener is None:
                kept.append(line)
        elif _closes_fence(line, opener):
            opener = None
    return kept, opener is not None


_NEGATING_PREFIXES: Final = ("pre-", "non-")
_TRADITION_WORD: Final = re.compile(r"[a-z]+(?:-[a-z]+)*")


def _tradition_keys(value: object) -> list[str]:
    """Keys for ``traditions_for`` from a free-text ``tradition`` frontmatter value.

    Each entry is tried whole first, as a hyphenated key (``Old Testament`` becomes
    ``old-testament``), then word by word (``Jewish (Rabbinic)`` gives ``jewish``). A
    word with a ``pre-`` or ``non-`` prefix names what the note is not, or what came
    before, and is ignored: ``Pre-Islamic Arabian`` is not an Islam note.
    """
    items = value if isinstance(value, list) else [value]
    keys: list[str] = []
    for item in items:
        text = scalar_text(item).lower()
        if not text:
            continue
        words = _TRADITION_WORD.findall(text)
        keys.append("-".join(re.findall(r"[a-z]+", text)))
        for word in words:
            if word.startswith(_NEGATING_PREFIXES):
                continue
            keys.append(word)
            # "Hindu-Buddhist" names both; each part is a key of its own.
            keys.extend(part for part in word.split("-") if "-" in word and part)
    return keys


def _sources(frontmatter: dict[str, object]) -> tuple[str, ...]:
    """The note's ``sources`` entries as clean strings."""
    raw = frontmatter.get("sources")
    items = raw if isinstance(raw, list) else [raw]
    cleaned = (_inline(scalar_text(item), _Stats()) for item in items if item)
    return tuple(text for text in cleaned if text)


def clean_note(
    rel_path: str, raw_text: str, known_targets: AbstractSet[str] | None = None
) -> CleanNote:
    """Clean one note.

    Args:
        rel_path: The note's path inside ``religion-study``.
        raw_text: The note exactly as read from disk.
        known_targets: Names from ``link_targets``; needed to count unresolved links.

    Raises:
        FrontmatterError: When the YAML frontmatter does not parse. Such notes are
            excluded by ``iter_vault_notes`` and never reach the cleaner.
    """
    frontmatter, body = split_frontmatter(unicodedata.normalize("NFC", raw_text))
    note_type = (
        scalar_text(frontmatter.get("type")).strip().lower() or DEFAULT_NOTE_TYPE
    )
    stats = _Stats(known=known_targets)
    body = _strip_comments(strip_fence_runs(body))
    lines, unclosed_fence = _drop_code_fences(body.splitlines())
    parser = _Parser(lines, note_type == _STORY, stats)
    sections = parser.parse()
    tags = tags_of(frontmatter)
    title = _inline(scalar_text(frontmatter.get("title")), _Stats())
    rel_path = unicodedata.normalize(
        "NFC", rel_path
    )  # one spelling for ids and citations
    return CleanNote(
        path=rel_path,
        title=title or parser.h1 or _clean_text(PurePosixPath(rel_path).stem),
        note_type=note_type,
        tags=tuple(tags),
        traditions=traditions_for(
            [*tags, *_tradition_keys(frontmatter.get("tradition"))], rel_path
        ),
        sources=_sources(frontmatter),
        sections=sections,
        unresolved_links=stats.unresolved,
        dropped_embeds=stats.embeds,
        unclosed_fence=unclosed_fence,
    )
