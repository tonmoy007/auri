"""What may be read from the study vault, and which notes are not worth indexing.

The vault sits next to tool debris: plugin configs that commonly hold API keys, image
scripts, a sibling folder of scriptures with unverified licences. The preflight, the
sync script and the index builder all call the same rules here, so the allowlist
cannot drift between them. Paths are relative to the ``religion-study`` folder and use
``/``. Nothing here opens a file except ``iter_vault_notes``, and that never follows a
symlink.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from app.priest.schemas import TraditionId

TOOL_DIR_NAMES: Final = frozenset(
    {".obsidian", ".smart-env", ".claudian", ".claude", ".omc", ".git"}
)
ASSETS_DIR: Final = "assets"
MARKDOWN_SUFFIX: Final = ".md"
BOM: Final = chr(0xFEFF)

# Navigation hubs: retrieval would fill up with link lists if these were indexed.
EXCLUDED_TYPES: Final = frozenset(
    {"redirect", "moc", "index", "scripture-index", "scripture-library"}
)
PLANNED: Final = "planned"
BAD_FRONTMATTER: Final = "bad_frontmatter"
BAD_ENCODING: Final = "bad_encoding"

_FRONTMATTER: Final = re.compile(
    r"\A---[ \t]*\r?\n(?:(?P<block>.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE: Final = re.compile(r"^[A-Za-z]:")

_J, _C = TraditionId.judaism.value, TraditionId.christianity.value
_I, _H = TraditionId.islam.value, TraditionId.hinduism.value
_B, _JN = TraditionId.buddhism.value, TraditionId.jainism.value
_S, _Z = TraditionId.sikhism.value, TraditionId.zoroastrianism.value
_CF, _D = TraditionId.confucianism.value, TraditionId.daoism.value
_E, _M = TraditionId.egyptian.value, TraditionId.mesopotamian.value

# Tag (or tradition word) to the ids of the TraditionId enum. An Old Testament note
# serves both the Jewish and the Christian filter.
TRADITION_MAP: Final[dict[str, tuple[str, ...]]] = {
    **dict.fromkeys(
        ("judaism", "jewish", "mishnah", "talmud", "tannaim", "tanakh"), (_J,)
    ),
    **dict.fromkeys(("kabbalah", "rishonim", "midrash"), (_J,)),
    **dict.fromkeys(("christianity", "christian", "bible", "new-testament"), (_C,)),
    "jesus": (_C,),
    "old-testament": (_J, _C),
    **dict.fromkeys(("islam", "islamic", "muslim", "quran", "hadith"), (_I,)),
    **dict.fromkeys(("sahaba", "muhammad", "quraysh"), (_I,)),
    **dict.fromkeys(("hinduism", "hindu", "itihasa", "mahabharata"), (_H,)),
    **dict.fromkeys(("ramayana", "puranas", "rama", "vedic"), (_H,)),
    **dict.fromkeys(("buddhism", "buddhist", "buddha"), (_B,)),
    **dict.fromkeys(("jainism", "jain", "tirthankara"), (_JN,)),
    **dict.fromkeys(("sikhism", "sikh", "gurus", "khalsa"), (_S,)),
    **dict.fromkeys(("zoroastrianism", "zoroastrian"), (_Z,)),
    **dict.fromkeys(("confucianism", "confucian", "confucius", "mencius"), (_CF,)),
    **dict.fromkeys(("daoism", "daoist", "taoism", "zhuangzi", "liezi"), (_D,)),
    "egyptian": (_E,),
    **dict.fromkeys(("mesopotamian", "sumerian"), (_M,)),
}

FOLDER_TRADITIONS: Final[dict[str, tuple[str, ...]]] = {
    "buddhist": (_B,),
    "confucian": (_CF,),
    "daoist": (_D,),
    "islamic": (_I,),
    "islamic-concepts": (_I,),
    "jain": (_JN,),
    "jewish": (_J,),
    "mesopotamian": (_M,),
    "sikh": (_S,),
    "zoroastrian": (_Z,),
}

_ENUM_ORDER: Final = {member.value: n for n, member in enumerate(TraditionId)}


class FrontmatterError(ValueError):
    """A note's YAML frontmatter is missing its closing fence or does not parse."""


@dataclass(frozen=True)
class VaultNote:
    """One readable note and why, if at all, it should not be indexed."""

    rel_path: str
    text: str
    sha256: str
    frontmatter: dict[str, object]
    exclusion_reason: str | None


def is_denied_path(rel_path: str) -> str | None:
    """Return why *rel_path* must never be read or sent, or ``None`` if it is allowed.

    Args:
        rel_path: A ``/``-separated path relative to the ``religion-study`` folder.

    Returns:
        One of ``outside_scope``, ``path_traversal``, ``tool_dir``, ``hidden``,
        ``assets`` or ``not_markdown``; ``None`` for an ordinary ``.md`` note.
    """
    if not rel_path or _CONTROL.search(rel_path) or "\\" in rel_path:
        return "outside_scope"
    if rel_path.startswith("/") or _DRIVE.match(rel_path):
        return "outside_scope"
    parts = rel_path.split("/")
    if ".." in parts:
        return "path_traversal"
    if "" in parts or "." in parts:
        return "outside_scope"
    return _segment_denial(parts[:-1]) or _file_denial(parts[-1])


def _segment_denial(dir_names: Sequence[str]) -> str | None:
    """Denial reason for directory names, tool dirs first so the reason is specific."""
    lowered = [name.lower() for name in dir_names]
    if any(name in TOOL_DIR_NAMES for name in lowered):
        return "tool_dir"
    if any(name.startswith(".") for name in lowered):
        return "hidden"
    if ASSETS_DIR in lowered:
        return ASSETS_DIR
    return None


def _file_denial(name: str) -> str | None:
    """Denial reason for the last path segment, a file name."""
    if name.lower() in TOOL_DIR_NAMES:
        return "tool_dir"
    if name.startswith("."):
        return "hidden"
    return None if name.endswith(MARKDOWN_SUFFIX) else "not_markdown"


def _normalise_tag(value: object) -> str:
    """Lower-case a tag and drop a leading ``#``."""
    return str(value).strip().lstrip("#").strip().lower()


def tags_of(frontmatter: Mapping[str, object]) -> list[str]:
    """The note's tags, lower-cased and without ``#``, from a YAML list or a string."""
    raw = frontmatter.get("tags")
    if isinstance(raw, str):
        return [_normalise_tag(raw)]
    if isinstance(raw, (list, tuple)):
        return [_normalise_tag(tag) for tag in raw if tag is not None]
    return []


def exclusion_reason(frontmatter: Mapping[str, object]) -> str | None:
    """Return why a note is left out of the index, or ``None`` to keep it.

    Navigation types come back as ``type:<name>``; planned stubs (a ``planned`` tag or
    ``depth: planned``) as ``planned``. The builder records a count per reason.
    """
    note_type = str(frontmatter.get("type") or "").strip().lower()
    if note_type in EXCLUDED_TYPES:
        return f"type:{note_type}"
    depth = str(frontmatter.get("depth") or "").strip().lower()
    if depth == PLANNED or PLANNED in tags_of(frontmatter):
        return PLANNED
    return None


def traditions_for(tags: Sequence[str], rel_path: str) -> tuple[str, ...]:
    """Map tags and folders to ``TraditionId`` values, in the enum's fixed order.

    Args:
        tags: The note's tags (``#`` prefix and nested ``a/b`` forms are accepted).
        rel_path: The note's path, whose folders such as ``stories/buddhist`` count.
    """
    found: set[str] = set()
    for tag in tags:
        normalised = _normalise_tag(tag)
        for key in (normalised, *normalised.split("/")):
            found.update(TRADITION_MAP.get(key, ()))
    for folder in rel_path.split("/")[:-1]:
        found.update(FOLDER_TRADITIONS.get(folder.lower(), ()))
    return tuple(sorted(found, key=_ENUM_ORDER.__getitem__))


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a note into its YAML frontmatter mapping and the body.

    Raises:
        FrontmatterError: When the opening fence has no closing one, or the YAML does
            not parse into a mapping.
    """
    text_no_bom = text.removeprefix(BOM)
    if not re.match(r"---[ \t]*\r?\n", text_no_bom):
        return {}, text
    match = _FRONTMATTER.match(text_no_bom)
    if match is None:
        raise FrontmatterError("frontmatter is never closed")
    try:
        loaded = yaml.safe_load(match.group("block") or "")
    except (yaml.YAMLError, ValueError) as exc:
        raise FrontmatterError("frontmatter is not valid YAML") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return {str(key): value for key, value in loaded.items()}, text_no_bom[
        match.end() :
    ]


def _allowed_files(dirpath: str, prefix: str, filenames: list[str]) -> list[str]:
    """Relative paths of the regular, allowed files listed in one directory."""
    rel_paths = [f"{prefix}{name}" for name in filenames]
    return [
        rel
        for rel, name in zip(rel_paths, filenames, strict=True)
        if not os.path.islink(os.path.join(dirpath, name)) and not is_denied_path(rel)
    ]


def _walk_allowed(root: str) -> list[str]:
    """Relative paths of every non-symlink file under *root* that the rules allow."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        # followlinks=False already keeps os.walk out of symlinked folders.
        dirnames[:] = [d for d in dirnames if not _segment_denial([d])]
        found.extend(_allowed_files(dirpath, prefix, filenames))
    return sorted(found)


def _load_note(root: str, rel_path: str) -> VaultNote:
    """Read one allowed note, turning a read or parse failure into an exclusion."""
    raw = Path(root, rel_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return VaultNote(rel_path, "", digest, {}, BAD_ENCODING)
    try:
        frontmatter, _body = split_frontmatter(text)
    except FrontmatterError:
        return VaultNote(rel_path, text, digest, {}, BAD_FRONTMATTER)
    return VaultNote(rel_path, text, digest, frontmatter, exclusion_reason(frontmatter))


def iter_vault_notes(root: os.PathLike[str] | str) -> Iterator[VaultNote]:
    """Yield every allowed note under *root* in path order, never following symlinks.

    Denied paths are skipped without being opened. Excluded notes are still yielded,
    with their ``exclusion_reason`` set, so the builder can count them per reason.
    """
    base = os.fspath(root)
    for rel_path in _walk_allowed(base):
        yield _load_note(base, rel_path)
