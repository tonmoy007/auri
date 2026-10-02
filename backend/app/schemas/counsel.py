"""The counselor's reply as structured parts, and the parser for a model's JSON.

The model is asked for one JSON object (see ``llm/prompts/counsel.md``). It is parsed
and validated here so the app can draw the parts separately and so later checks (plan
12.10) can read each field. Anything that does not fit is a ``CounselingError``, and
the confession flow then falls back to a fixed message; a malformed reply is never
shown. The error text carries lengths and field names only, because the reply is
generated from a confession.
"""

from __future__ import annotations

import enum
import json
import re
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.exceptions import CounselingError

MAX_ACKNOWLEDGEMENT_CHARS: Final = 400
MAX_REFLECTION_CHARS: Final = 400
MAX_SUGGESTION_CHARS: Final = 200
MAX_SUGGESTIONS: Final = 3
MAX_CLOSING_CHARS: Final = 200
_FENCE: Final = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$")

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CounselTone(str, enum.Enum):
    """The register of a reply, so the app can pick a matching presentation."""

    warm = "warm"
    gentle = "gentle"
    light = "light"
    celebratory = "celebratory"
    steady = "steady"


class CounselReply(BaseModel):
    """A counselor reply, split into the parts the app draws separately."""

    model_config = ConfigDict(extra="ignore")

    acknowledgement: _Text = Field(max_length=MAX_ACKNOWLEDGEMENT_CHARS)
    reflection: _Text = Field(max_length=MAX_REFLECTION_CHARS)
    suggestions: list[Annotated[_Text, Field(max_length=MAX_SUGGESTION_CHARS)]] = Field(
        default_factory=list, max_length=MAX_SUGGESTIONS
    )
    closing: _Text = Field(max_length=MAX_CLOSING_CHARS)
    tone: CounselTone

    def render(self) -> str:
        """The reply as one paragraph, for the text column older clients still read."""
        parts = [self.acknowledgement, self.reflection, *self.suggestions, self.closing]
        return " ".join(parts)


def lenient_counsel_reply(value: object) -> object:
    """For a response field: a stored reply that no longer fits the schema reads as absent.

    A row edited by hand, or written under an older schema, must not turn a read of the
    confessor's own history into an error; the rendered text column still has the reply.
    """
    if value is None or isinstance(value, CounselReply):
        return value
    try:
        return CounselReply.model_validate(value)
    except ValidationError:
        return None


def _first_json_object(raw: str) -> object:
    """Decode the first JSON object in *raw*, ignoring a code fence or surrounding prose."""
    unfenced = _FENCE.sub("", raw.strip())
    start = unfenced.find("{")
    if start == -1:
        raise ValueError("no JSON object")
    value, _ = json.JSONDecoder().raw_decode(unfenced[start:])
    return value


def parse_counsel_reply(raw: str) -> CounselReply:
    """Parse and validate a model's reply into a :class:`CounselReply`.

    Args:
        raw: The model's output: a JSON object, optionally in a code fence or with
            prose around it.

    Raises:
        CounselingError: If there is no JSON object, it is not an object, or it does
            not satisfy the schema. The message never contains the reply text.
    """
    try:
        value = _first_json_object(raw)
    except ValueError as exc:
        raise CounselingError(
            f"counselor reply is not valid JSON ({len(raw)} chars)"
        ) from exc
    if not isinstance(value, dict):
        raise CounselingError("counselor reply is not a JSON object")
    try:
        return CounselReply.model_validate(value)
    except ValidationError as exc:
        fields = sorted(
            {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
        )
        raise CounselingError(
            f"counselor reply does not fit the schema (fields: {', '.join(fields)})"
        ) from exc
