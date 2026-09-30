"""The request, response and model-output contracts for priest mode.

``PriestAskRequest`` forbids extra fields on purpose: no confession id, text or device
detail can ever be sent to the guide, and a test pins it. ``PriestDraft`` is what the
model must return; it is validated, never trusted (see ``answer_validator``).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_QUESTION_CHARS: Final = 1000
MIN_QUESTION_CHARS: Final = 3
MAX_SNIPPET_CHARS: Final = 280
DISCLAIMER_VERSION: Final = "1"

_SOURCE_ID = re.compile(r"^S[1-9][0-9]?$")


class AnswerKind(str, Enum):
    """What sort of response this is; the app renders each differently."""

    answer = "answer"
    not_covered = "not_covered"
    crisis = "crisis"
    deferral = "deferral"
    library_excerpts = "library_excerpts"


class TraditionId(str, Enum):
    """The traditions the study library covers, for the optional filter."""

    judaism = "judaism"
    christianity = "christianity"
    islam = "islam"
    hinduism = "hinduism"
    buddhism = "buddhism"
    jainism = "jainism"
    sikhism = "sikhism"
    zoroastrianism = "zoroastrianism"
    confucianism = "confucianism"
    daoism = "daoism"
    egyptian = "egyptian"
    mesopotamian = "mesopotamian"


TRADITION_LABELS: Final[dict[str, str]] = {
    "judaism": "Judaism",
    "christianity": "Christianity",
    "islam": "Islam",
    "hinduism": "Hinduism",
    "buddhism": "Buddhism",
    "jainism": "Jainism",
    "sikhism": "Sikhism",
    "zoroastrianism": "Zoroastrianism",
    "confucianism": "Confucianism",
    "daoism": "Daoism",
    "egyptian": "Egyptian religion",
    "mesopotamian": "Mesopotamian religion",
}


def _has_control_character(text: str) -> bool:
    """Whether *text* holds a control character other than a line break or tab."""
    return any(ord(ch) < 32 and ch not in "\n\r\t" or ord(ch) == 127 for ch in text)


class PriestAskRequest(BaseModel):
    """A question for the guide. Nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")

    question: str
    tradition: TraditionId | None = None
    language: Literal["en"] = "en"

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        """Strip the question and refuse NUL or other control characters."""
        stripped = value.strip()
        if _has_control_character(stripped):
            raise ValueError("question must not contain control characters")
        if not MIN_QUESTION_CHARS <= len(stripped) <= MAX_QUESTION_CHARS:
            raise ValueError(
                f"question must be {MIN_QUESTION_CHARS}-{MAX_QUESTION_CHARS} characters"
            )
        return stripped


class PriestPoint(BaseModel):
    """One statement from the library, with the sources it rests on."""

    text: str
    citation_ids: list[str]


class PriestQuote(BaseModel):
    """A verbatim quotation, labelled by code from the note's metadata."""

    text: str
    citation_id: str
    label: str


class PriestCitation(BaseModel):
    """A source note the answer rests on, as the app shows it."""

    id: str
    note_title: str
    heading_path: list[str]
    note_type: str
    tradition_labels: list[str]
    snippet: str = Field(max_length=MAX_SNIPPET_CHARS)


class CrisisContactOut(BaseModel):
    """A way to reach help; ``dial`` is a number safe to pass to ``tel:``."""

    label: str
    detail: str
    dial: str | None = None


class PriestAnswerResponse(BaseModel):
    """What the guide returns for one question."""

    request_id: str
    kind: AnswerKind
    points: list[PriestPoint] = Field(default_factory=list)
    quotes: list[PriestQuote] = Field(default_factory=list)
    reflection: str | None = None
    citations: list[PriestCitation] = Field(default_factory=list)
    notice: str | None = None
    contacts: list[CrisisContactOut] | None = None
    disclaimer_version: str = DISCLAIMER_VERSION
    index_version: str | None = None
    prompt_version: str | None = None


class TraditionOption(BaseModel):
    """One entry of the tradition picker."""

    id: str
    label: str


class PriestStatusResponse(BaseModel):
    """Whether the guide is on, and what the app needs to draw its entry point."""

    enabled: bool
    persona_name: str
    traditions: list[TraditionOption]
    disclaimer_version: str = DISCLAIMER_VERSION
    max_question_chars: int = MAX_QUESTION_CHARS


# ── What the model must return (validated by answer_validator) ───────────


class DraftPoint(BaseModel):
    """A point in the model's draft: text plus the source ids it cites."""

    text: str = Field(max_length=300)
    sources: list[str] = Field(min_length=1, max_length=3)

    @field_validator("sources")
    @classmethod
    def _source_ids(cls, value: list[str]) -> list[str]:
        if not all(_SOURCE_ID.match(v) for v in value):
            raise ValueError("source ids look like S1, S2")
        return value


class DraftQuote(BaseModel):
    """A quote in the model's draft, to be checked against its source."""

    text: str = Field(min_length=12, max_length=280)
    source: str

    @field_validator("source")
    @classmethod
    def _source_id(cls, value: str) -> str:
        if not _SOURCE_ID.match(value):
            raise ValueError("source ids look like S1, S2")
        return value


class PriestDraft(BaseModel):
    """The model's answer, before any validator has looked at it."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["answer", "not_covered"]
    points: list[DraftPoint] = Field(default_factory=list, max_length=4)
    quotes: list[DraftQuote] = Field(default_factory=list, max_length=2)
    reflection: str | None = Field(default=None, max_length=400)
