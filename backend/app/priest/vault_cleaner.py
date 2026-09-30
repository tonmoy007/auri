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
  quote; a plain blockquote in a ``story`` note is narrative and carries none.
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
from app.priest.vault_rules import split_frontmatter, tags_of, traditions_for

CLEANER_VERSION: Final = "1"
DEFAULT_NOTE_TYPE: Final = "note"
_STORY: Final = "story"
_MAX_TRAILING_SOURCE: Final = 100

_COMMENT: Final = re.compile(r"%%.*?%%|<!--.*?-->", re.DOTALL)
_FENCE_LINE: Final = re.compile(r"^\s*(?:```|~~~)")
_HEADING: Final = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_RULE: Final = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_IMAGE_LINE: Final = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_CAPTION: Final = re.compile(r"^\s*([*_])(?![*_\s]).+\1\s*$")
_CALLOUT: Final = re.compile(r"^\s*>\s*\[!([\w-]+)\][+-]?[ \t]*(.*)$")
_QUOTE_PREFIX: Final = re.compile(r"^\s*(?:>[ \t]?)+")
_TABLE_SEP: Final = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_WIKILINK_SPAN: Final = re.compile(r"\[\[[^\]]*\]\]")
_PIPE: Final = "\ue000"  # stands in for a pipe inside a wikilink while a row is split
_CELL_SPLIT: Final = re.compile(r"(?<!\\)\|")
_LIST_ITEM: Final = re.compile(r"^\s*[*+-][ \t]+")

_EMBED: Final = re.compile(r"!\[\[[^\]]*\]\]")
_IMAGE: Final = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_WIKILINK: Final = re.compile(r"\[\[([^\]|#]*)(?:#([^\]|]*))?(?:\\?\|([^\]]*))?\]\]")
_MD_LINK: Final = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL: Final = re.compile(r"https?://\S+")
_HTML_TAG: Final = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_HIGHLIGHT: Final = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
_BOLD: Final = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC: Final = re.compile(
    r"(?<![\w*_])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![\w*_])"
)
_BLOCK_ID: Final = re.compile(r"[ \t]\^[\w-]+[ \t]*$", re.MULTILINE)
_SPACES: Final = re.compile(r"[ \t ]+")

_OPEN_QUOTE: Final = re.compile(r"^[\"“‘«]")
_CLOSE_THEN_DASH: Final = re.compile(r"([\"”’»])\s+[—–]{1,2}\s+")
_DASH_LINE: Final = re.compile(r"^(?:[—–]{1,2}|--)\s*(\S.*)$")
_TRAILING_SOURCE: Final = re.compile(r"\s[—–]\s+([^—–]+)$")
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


def _inline(text: str, stats: _Stats) -> str:
    """Reduce inline Markdown and Obsidian syntax on one line to its plain text."""
    text = _EMBED.sub(lambda _m: stats.drop_embed(), text)
    text = _IMAGE.sub(lambda _m: stats.drop_embed(), text)
    text = _WIKILINK.sub(lambda m: _link_text(m, stats), text)
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", _URL.sub("", text))
    text = _ITALIC.sub(r"\2", _BOLD.sub(r"\2", _HIGHLIGHT.sub(r"\1", text)))
    text = _BLOCK_ID.sub("", text.replace("`", ""))
    return _SPACES.sub(" ", strip_fence_runs(text)).strip()


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
    """Split quote lines into the quoted text and the source the note names for it."""
    if len(lines) > 1 and (dash := _DASH_LINE.match(lines[-1])):
        return "\n".join(lines[:-1]), dash.group(1).strip()
    text = "\n".join(lines)
    closers = list(_CLOSE_THEN_DASH.finditer(text))
    if closers:
        return text[: closers[-1].end(1)], text[closers[-1].end() :].strip()
    trailing = _TRAILING_SOURCE.search(text)
    if trailing and len(trailing.group(1)) <= _MAX_TRAILING_SOURCE:
        return text[: trailing.start()], trailing.group(1).strip()
    return text, None


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
            self._heading(heading)
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

    def _heading(self, match: Match[str]) -> None:
        """Open a section for H2 and H3; fold H4+ into text; remember the first H1."""
        level = len(match.group(1))
        name = _strip_symbols(_inline(match.group(2), self.stats))
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


def _drop_code_fences(lines: list[str]) -> list[str]:
    """Remove fenced code blocks (dataview included); an unclosed fence runs to the end."""
    kept: list[str] = []
    inside = False
    for line in lines:
        if _FENCE_LINE.match(line):
            inside = not inside
        elif not inside:
            kept.append(line)
    return kept


def _words(value: object) -> list[str]:
    """Lower-case words of a free-text frontmatter value such as ``Jewish (Rabbinic)``."""
    items = value if isinstance(value, list) else [value]
    return [
        w for item in items if item for w in re.findall(r"[a-z]+", str(item).lower())
    ]


def _sources(frontmatter: dict[str, object]) -> tuple[str, ...]:
    """The note's ``sources`` entries as clean strings."""
    raw = frontmatter.get("sources")
    items = raw if isinstance(raw, list) else [raw]
    cleaned = (_inline(str(item), _Stats()) for item in items if item)
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
    note_type = str(frontmatter.get("type") or "").strip().lower() or DEFAULT_NOTE_TYPE
    stats = _Stats(known=known_targets)
    body = _COMMENT.sub("", strip_fence_runs(body))
    lines = _drop_code_fences(body.splitlines())
    parser = _Parser(lines, note_type == _STORY, stats)
    sections = parser.parse()
    tags = tags_of(frontmatter)
    title = _inline(str(frontmatter.get("title") or ""), _Stats())
    return CleanNote(
        path=rel_path,
        title=title or parser.h1 or PurePosixPath(rel_path).stem,
        note_type=note_type,
        tags=tuple(tags),
        traditions=traditions_for(
            [*tags, *_words(frontmatter.get("tradition"))], rel_path
        ),
        sources=_sources(frontmatter),
        sections=sections,
        unresolved_links=stats.unresolved,
        dropped_embeds=stats.embeds,
    )
