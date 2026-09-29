"""Turning a pile of de-identified summaries into candidate recurring themes.

The model's only job here is to *group and label*. Everything a reader could
mistake for a fact — how many people, which way sentiment moved — is computed
afterwards in code from the stored rows (see ``theme_report``), and every
privacy rule is applied there too. So this module treats the model's reply as
untrusted input: it is parsed strictly, indices are range-checked, labels are
sanitised, and anything unusable raises rather than being repaired.

Only ``ai_summary`` text ever reaches the model — never a transcript.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from app.exceptions import ThemeClusteringError

MAX_SUMMARIES_PER_WINDOW: Final = 60
MAX_SUMMARY_CHARS: Final = 240
MAX_THEMES: Final = 8
MAX_LABEL_CHARS: Final = 60
MIN_GROUP_MEMBERS: Final = 2
MAX_LABEL_WORDS: Final = 6
MAX_REPLY_CHARS: Final = 20_000

CLUSTERING_INSTRUCTION: Final = (
    "Below is a numbered list of anonymised summaries of workplace feedback. "
    f"Group them into at most {MAX_THEMES} recurring themes. Reply with JSON "
    'only, in exactly this shape: {"themes": [{"label": "short neutral name '
    'of at most six words", "items": [1, 4, 7]}]}. Put each item number in at '
    "most one theme, and only create a theme when at least "
    f"{MIN_GROUP_MEMBERS} items genuinely share it. A label must describe the "
    "topic only: never include a person, team, place, or quotation."
)

_WHITESPACE = re.compile(r"\s+")
# The prompt fences untrusted text with <<<...>>> markers; a summary that
# contains one could close the fence and start giving instructions.
_FENCE_RUN = re.compile(r"<{3,}|>{3,}")
# Labels that carry an identifier rather than a topic.
_IDENTIFIER_PATTERN = re.compile(r"\d|@|://|www\.", re.IGNORECASE)


class Window(str, Enum):
    """Which reporting period a summary belongs to."""

    current = "current"
    previous = "previous"


@dataclass(frozen=True)
class SummaryItem:
    """One de-identified confession as the theme pipeline sees it."""

    text: str
    sentiment: str | None
    category: str | None
    window: Window


@dataclass(frozen=True)
class ThemeGroup:
    """A labelled set of items, as zero-based indices into the item list."""

    label: str
    members: tuple[int, ...]


def _one_line(text: str) -> str:
    """Make one summary one prompt line: no fence markers, one space, capped."""
    unfenced = _FENCE_RUN.sub(" ", text)
    return _WHITESPACE.sub(" ", unfenced).strip()[:MAX_SUMMARY_CHARS]


def numbered_summaries(items: list[SummaryItem]) -> str:
    """Render *items* as the one-based numbered list the prompt refers to.

    Args:
        items: The summaries, in the order the model will index them.

    Returns:
        One ``"N. text"`` line per item, with prompt-fence markers removed.
    """
    return "\n".join(
        f"{position}. {_one_line(item.text)}"
        for position, item in enumerate(items, start=1)
    )


def clean_label(value: object) -> str | None:
    """Return *value* as a safe single-line topic label, or ``None`` if it is not one.

    Rejects non-strings, empty, over-long or over-wordy labels, any control
    character, and anything carrying an identifier (a digit, ``@``, a URL).
    The label ends up in a table, a Markdown file and a CSV, was written by a
    model reading user speech, and is the most identifying text shown — so it
    must read as a topic, not as a detail.

    Args:
        value: The candidate label, of any type.

    Returns:
        The whitespace-normalised label, or ``None``.
    """
    if not isinstance(value, str):
        return None
    label = _WHITESPACE.sub(" ", value).strip()
    if not label or len(label) > MAX_LABEL_CHARS:
        return None
    if len(label.split(" ")) > MAX_LABEL_WORDS or _IDENTIFIER_PATTERN.search(label):
        return None
    if any(unicodedata.category(char).startswith("C") for char in label):
        return None
    return label


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Return the JSON object embedded in *raw*, tolerating fences and chatter.

    Raises:
        ThemeClusteringError: If no JSON object can be decoded. The message
            never includes *raw*, which may quote summaries.
    """
    if len(raw) > MAX_REPLY_CHARS:
        raise ThemeClusteringError("model reply was too long")
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ThemeClusteringError("model reply contained no JSON object")
    try:
        payload = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ThemeClusteringError("model reply was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ThemeClusteringError("model reply JSON was not an object")
    return payload


def _claim_members(
    raw_items: object, item_count: int, claimed: set[int]
) -> tuple[int, ...]:
    """Return the still-unclaimed, in-range members of one proposed theme."""
    if not isinstance(raw_items, list):
        return ()
    members: list[int] = []
    for value in raw_items:
        is_index = isinstance(value, int) and not isinstance(value, bool)
        if is_index and 1 <= value <= item_count and value - 1 not in claimed:
            members.append(value - 1)
            claimed.add(value - 1)
    return tuple(members)


def _parse_group(
    entry: object, item_count: int, claimed: set[int]
) -> ThemeGroup | None:
    """Validate one proposed theme; ``None`` if it is not usable."""
    if not isinstance(entry, dict):
        return None
    label = clean_label(entry.get("label"))
    if label is None:
        return None
    members = _claim_members(entry.get("items"), item_count, claimed)
    if len(members) < MIN_GROUP_MEMBERS:
        return None
    return ThemeGroup(label=label, members=members)


def parse_theme_groups(raw: str, item_count: int) -> list[ThemeGroup]:
    """Validate the model's reply into at most ``MAX_THEMES`` theme groups.

    An item claimed by two themes stays with the first; out-of-range or
    non-integer indices are ignored; a theme with a bad label or fewer than
    ``MIN_GROUP_MEMBERS`` valid members is dropped.

    Raises:
        ThemeClusteringError: If the reply is not JSON of the expected shape
            or yields no usable theme at all.
    """
    themes = _extract_json_object(raw).get("themes")
    if not isinstance(themes, list):
        raise ThemeClusteringError("model reply had no 'themes' list")

    claimed: set[int] = set()
    groups: list[ThemeGroup] = []
    for entry in themes:
        group = _parse_group(entry, item_count, claimed)
        if group is not None:
            groups.append(group)
        if len(groups) == MAX_THEMES:
            break
    if not groups:
        raise ThemeClusteringError("model reply held no usable theme")
    return groups


def group_by_category(items: list[SummaryItem]) -> list[ThemeGroup]:
    """Group *items* by their stored category — the no-model fallback.

    Categories were assigned at submit time, so this needs no model at all.
    Items without a usable category are left out.

    Args:
        items: The summaries to group.

    Returns:
        One group per usable category, in order of first appearance.
    """
    by_label: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        label = clean_label(item.category)
        if label is not None:
            by_label.setdefault(label, []).append(index)
    return [
        ThemeGroup(label=label, members=tuple(members))
        for label, members in by_label.items()
    ]
