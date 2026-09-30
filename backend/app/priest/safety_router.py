"""Deterministic routing of a priest-mode question, before any retrieval or model call.

Everything here is a lexicon match or a fixed template. Crisis handling must never
depend on generation: a model that invents a helpline, or is down, or is talked out of
it, is worse than none. So the router calls no model and opens no connection, and a
test pins that.

Precedence: crisis, then abuse, medical, legal and judge-a-person deferrals, then a
pass (with a flag when the question asks for a religious ruling). Lexicons and
templates are versioned text files under ``lexicons/`` and ``templates/``, loaded once.

Decision on idioms: only explicit or common indirect self-harm phrasing is a crisis hit.
"This deadline is killing me" and "killing time" pass; the parallel ``moderate()`` call
in the service is the second net. For explicit self-harm phrasing, including the bare
word "suicide", a false alarm is accepted over a miss.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final, Literal

from app.services import crisis_response

DecisionKind = Literal["pass", "crisis", "deferral"]

_ROOT: Final = Path(__file__).parent
LEXICON_FILES: Final[dict[str, str]] = {
    "crisis": "crisis_en.txt",
    "medical": "medical_en.txt",
    "legal": "legal_en.txt",
    "abuse": "abuse_en.txt",
    "judge_person": "judge_person_en.txt",
    "ruling": "ruling_en.txt",
}
DEFERRAL_TEMPLATES: Final[dict[str, str]] = {
    "abuse": "deferral_abuse.md",
    "medical": "deferral_medical.md",
    "legal": "deferral_legal.md",
    "judge_person": "deferral_judge.md",
}
TEMPLATE_FILES: Final = (
    *DEFERRAL_TEMPLATES.values(),
    "not_covered.md",
    "ruling_footer.md",
    "priest_crisis_line.md",
)
# Deferral categories in the order they are tried; abuse first, because a disclosure
# needs contacts more than it needs a referral.
_DEFERRAL_ORDER: Final = ("abuse", "medical", "legal", "judge_person")

_VERSION_LINE: Final = re.compile(r"^#\s*version:\s*(\d+)\s*$")
_TEMPLATE_VERSION: Final = re.compile(r"^<!--\s*version:\s*(\d+)\s*-->$")
_REGEX_PREFIX: Final = "re:"
_APOSTROPHES: Final = dict.fromkeys(map(ord, "'’‘`´ʼ"))
_LEET: Final = str.maketrans("013457", "oieast")
# Cyrillic and Greek letters that look like Latin ones, so "kіll" (Cyrillic і) still
# reads as "kill". Lexicon matching only; the script check runs on the raw text.
_CONFUSABLES: Final = str.maketrans("аеорсхуіјѕкмтнвοιανε", "aeopcxyijskmthboiave")
_JOINERS: Final = re.compile(r"(?<=\w)[-._*·](?=\w)")


@dataclass(frozen=True)
class SafetyDecision:
    """Where a question goes. ``ruling_footer`` only ever applies to a pass."""

    kind: DecisionKind
    category: str | None
    ruling_footer: bool


@dataclass(frozen=True)
class Lexicon:
    """A versioned list of patterns, matched against normalised text."""

    name: str
    version: int
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, normalised: str) -> bool:
        """Whether any pattern occurs in *normalised* (see ``normalise_text``)."""
        return any(pattern.search(normalised) for pattern in self.patterns)


@dataclass(frozen=True)
class Template:
    """A fixed, versioned reply text."""

    name: str
    version: int
    text: str


def _read_leet(word: str) -> str:
    """Read digits inside a word as letters ("k1ll" is "kill"); plain numbers stay."""
    return word.translate(_LEET) if any(ch.isalpha() for ch in word) else word


def normalise_text(text: str, *, leet: bool = True, squeeze: bool = False) -> str:
    """Reduce *text* to lower-case words so lexicon entries match regardless of styling.

    NFKC, invisible format characters and combining marks removed, casefolded,
    look-alike Cyrillic and Greek letters read as Latin, apostrophes dropped ("don't"
    is "dont"), other punctuation turned into a space, whitespace collapsed, and (when
    *leet*) digits and ``@ $`` inside words read as letters. With *squeeze*, separators
    inside a word ("sui-cide", "k.i.l.l") and spaces between single letters
    ("s u i c i d e") are removed too, to read deliberately broken-up words.
    """
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKC", text)
        if unicodedata.category(ch) != "Cf"
    )
    decomposed = unicodedata.normalize("NFD", folded.casefold())
    bare = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    bare = bare.translate(_CONFUSABLES)
    if leet:
        bare = re.sub(r"(?<=\w)@(?=\w)", "a", bare)
        bare = re.sub(r"\$(?=[a-z])", "s", bare)
        bare = re.sub(r"(?<=\w)\$(?=\w)", "s", bare)
    bare = bare.translate(_APOSTROPHES)
    if squeeze:
        bare = _JOINERS.sub("", bare)
    spaced = "".join(ch if ch.isalnum() else " " for ch in bare)
    words = spaced.split()
    if squeeze:
        words = _join_single_letters(words)
    return " ".join(_read_leet(w) for w in words) if leet else " ".join(words)


def _join_single_letters(words: list[str]) -> list[str]:
    """Join runs of single-letter words ("s u i c i d e" becomes "suicide")."""
    joined: list[str] = []
    run = ""
    for word in words:
        if len(word) == 1 and word.isalpha():
            run += word
            continue
        if run:
            joined.append(run)
            run = ""
        joined.append(word)
    if run:
        joined.append(run)
    return joined


def _compile_entry(line: str, name: str, number: int) -> re.Pattern[str] | None:
    """Compile one lexicon line: a regex after ``re:``, else a whole-word phrase."""
    try:
        if line.startswith(_REGEX_PREFIX):
            return re.compile(line[len(_REGEX_PREFIX) :], re.IGNORECASE)
        phrase = normalise_text(line)
        if not phrase:
            return None
        return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
    except re.error:
        raise ValueError(f"{name}: line {number} is not a valid pattern") from None


def parse_lexicon(text: str, name: str) -> Lexicon:
    """Parse lexicon file text.

    The first non-blank line must be ``# version: N``; other ``#`` lines are comments.

    Raises:
        ValueError: If the version header is missing or a pattern does not compile.
    """
    lines = [
        (i, raw.strip()) for i, raw in enumerate(text.splitlines(), 1) if raw.strip()
    ]
    header = _VERSION_LINE.match(lines[0][1]) if lines else None
    if header is None:
        raise ValueError(f"{name}: the first line must be '# version: N'")
    compiled = (
        _compile_entry(line, name, number)
        for number, line in lines[1:]
        if not line.startswith("#")
    )
    return Lexicon(name, int(header.group(1)), tuple(p for p in compiled if p))


@cache
def load_lexicon(filename: str) -> Lexicon:
    """Load a lexicon from ``lexicons/`` once; later calls return the same object."""
    return parse_lexicon((_ROOT / "lexicons" / filename).read_text("utf-8"), filename)


@cache
def load_template(name: str) -> Template:
    """Load a fixed template from ``templates/`` once.

    The first line is ``<!-- version: N -->``. Paragraphs are separated by a blank
    line; line breaks inside a paragraph are joined, so a file can be hard-wrapped.

    Raises:
        ValueError: If the version comment is missing.
    """
    lines = (_ROOT / "templates" / name).read_text("utf-8").strip().splitlines()
    header = _TEMPLATE_VERSION.match(lines[0].strip()) if lines else None
    if header is None:
        raise ValueError(f"{name}: the first line must be '<!-- version: N -->'")
    paragraphs = "\n".join(lines[1:]).strip().split("\n\n")
    text = "\n\n".join(" ".join(p.split()) for p in paragraphs)
    return Template(name, int(header.group(1)), text)


def lexicon_versions() -> dict[str, int]:
    """The version of each routing lexicon, for logging beside a decision."""
    return {key: load_lexicon(file).version for key, file in LEXICON_FILES.items()}


def _hit(category: str, normalised: str) -> bool:
    return load_lexicon(LEXICON_FILES[category]).matches(normalised)


def route(question: str) -> SafetyDecision:
    """Decide where *question* goes. Deterministic; no model call, no network.

    Args:
        question: The user's question, as typed.

    Returns:
        ``crisis`` if it signals self-harm or harm to others (wins over everything);
        ``deferral`` with a category if it needs a person rather than the library;
        otherwise ``pass``, with ``ruling_footer`` set if it asks for a ruling.
    """
    normalised = normalise_text(question)
    squeezed = normalise_text(question, squeeze=True)
    if _hit("crisis", normalised) or _hit("crisis", squeezed):
        return SafetyDecision("crisis", "crisis", False)
    for category in _DEFERRAL_ORDER:
        if _hit(category, normalised):
            return SafetyDecision("deferral", category, False)
    return SafetyDecision("pass", None, _hit("ruling", normalised))


def crisis_reply() -> crisis_response.CrisisResponse:
    """The fixed crisis reply: the shared template plus the priest-mode line."""
    return crisis_response.render(load_template("priest_crisis_line.md").text)


def render_deferral(category: str) -> tuple[str, list[crisis_response.CrisisContact]]:
    """The fixed text for a deferral category, and the contacts to show with it.

    Only an abuse disclosure carries the configured contacts.

    Raises:
        ValueError: If *category* has no deferral (``crisis`` is not one).
    """
    name = DEFERRAL_TEMPLATES.get(category)
    if name is None:
        raise ValueError("no deferral exists for that category")
    contacts = crisis_response.contacts() if category == "abuse" else []
    return load_template(name).text, contacts


def not_covered_text() -> str:
    """The fixed reply for a question the library does not cover."""
    return load_template("not_covered.md").text


# The library and the model run are English only, and the crisis lexicon is English, so
# a question in another script cannot be safety-checked in its own words: it gets a
# fixed notice that points to emergency help instead. Modifier letters (the marks in
# "Qurʾān" transliterations) are not a script of their own.


def is_unsupported_script(text: str) -> bool:
    """Whether *text* has a letter outside the Latin script (Bengali, Arabic, ...)."""
    return any(
        ch.isalpha()
        and unicodedata.category(ch) != "Lm"
        and not unicodedata.name(ch, "").startswith("LATIN")
        for ch in unicodedata.normalize("NFKC", text)
    )


def english_only_text() -> str:
    """The fixed reply for a question in a script the Guide does not support."""
    return load_template("english_only.md").text


def ruling_footer_text() -> str:
    """The footer appended when a question asks for a religious ruling."""
    return load_template("ruling_footer.md").text
