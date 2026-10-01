"""The fixed reply given when a confession (or a Guide question) signals crisis.

Generated text is never trusted to produce a phone number or a safety message: a
model that invents a helpline is worse than none. The reply is a template, its
contacts come from configuration, and with none configured a compiled-in message
is used. The text is identical on every call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from app.config import settings
from app.services.settings_service import get_config

logger = logging.getLogger(__name__)

COMPILED_IN_MESSAGE: Final = (
    "What you shared sounds really heavy, and your safety matters more than "
    "anything else here. If you are in immediate danger, please contact your "
    "local emergency number now. If you can, reach out to someone you trust or "
    "to a local crisis line; you do not have to carry this alone."
)

# What the confession flow adds: it is true there, because a crisis confession is held
# for a named person to acknowledge (11.9).
CONFESSION_CLOSING_LINE: Final = (
    "Your confession has been received, and a person at the company will "
    "look at it soon."
)

_INTRO: Final = (
    "What you shared sounds really heavy, and your safety matters more than "
    "anything else here. You do not have to carry this alone. Please reach out "
    "to someone right now"
)
_EMERGENCY: Final = (
    "If you are in immediate danger, contact your local emergency number."
)
_DIALABLE: Final = re.compile(r"^\+?[0-9][0-9 ()\-]{2,24}[0-9]$")
_MAX_FIELD: Final = 120


@dataclass(frozen=True)
class CrisisContact:
    """One way to reach help, as shown and (when a number) as dialled."""

    label: str
    detail: str
    dial: str | None = None


@dataclass(frozen=True)
class CrisisResponse:
    """The reply text and the same contacts as structured data."""

    text: str
    contacts: list[CrisisContact]


def _value(key: str) -> str:
    """Return a configured text value, trimmed, with control characters removed."""
    raw = get_config(key, str(getattr(settings, key, "")))
    cleaned = "".join(ch for ch in raw if ch.isprintable()).strip()
    return cleaned[:_MAX_FIELD]


def contacts() -> list[CrisisContact]:
    """Return the configured contacts; values that are not safe to publish are dropped."""
    found: list[CrisisContact] = []
    name = _value("CRISIS_HELPLINE_NAME")
    number = _value("CRISIS_HELPLINE_NUMBER")
    if number and _DIALABLE.match(number):
        found.append(
            CrisisContact(
                label=name or "Helpline",
                detail=number,
                dial=re.sub(r"[ ()\-]", "", number),
            )
        )
    elif number:
        logger.warning("CRISIS_HELPLINE_NUMBER is not a dialable number; ignoring it")
    eap = _value("CRISIS_EAP_CONTACT")
    if eap and "<" not in eap and ">" not in eap:
        found.append(CrisisContact(label="Employee assistance", detail=eap))
    return found


def render(closing_line: str | None = None) -> CrisisResponse:
    """Build the fixed crisis reply.

    Args:
        closing_line: Fixed text appended after the contacts (for example what the
            confession flow does next). Must be a constant, never model output.

    Returns:
        The text and its contacts. With no contacts configured the compiled-in
        message is used unchanged (plus the closing line).
    """
    found = contacts()
    if found:
        listed = "; ".join(f"{c.label}: {c.detail}" for c in found)
        text = f"{_INTRO}: {listed}. {_EMERGENCY}"
    else:
        text = COMPILED_IN_MESSAGE
    if closing_line:
        text = f"{text} {closing_line}"
    return CrisisResponse(text=text, contacts=found)
