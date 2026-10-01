"""Building the chat messages for one priest-mode question.

The system message carries the instructions and a per-request canary; the user
message carries the fenced source notes and the fenced question. All untrusted text
(note text, note titles, the question) is stripped of fence runs and fenced in code
before it is handed to the template, and the template fills values in a single pass
without ever interpreting them (see ``app.llm.prompt_loader``).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from app.llm.fencing import fence, strip_fence_runs
from app.llm.prompt_loader import Prompt, PromptError, load_prompt
from app.priest.types import RetrievedChunk

PROMPT_NAME: Final = "priest_answer"
# Splits the template into its system half and its user half.
_USER_MARKER: Final = "<!-- user-message -->"
_MAX_TITLE_CHARS: Final = 120
_MAX_LABEL_CHARS: Final = 60
# Room for every correction line (V1 to V9) at once; a shorter cap cut the later ones.
_MAX_CORRECTION_CHARS: Final = 900
# Where the worked examples start in the system half of the template.
_EXAMPLES_MARKER: Final = "Example 1"
_CANARY_BYTES: Final = 8
_NO_SOURCES: Final = "(no source notes were found)"

_SCOPE_ALL: Final = (
    "The notes may come from any of the traditions in the study library."
)
_DEFAULT_PERSONA: Final = "Guide"


@dataclass(frozen=True)
class BuiltPrompt:
    """The messages to send, plus what the validator needs to check the reply."""

    messages: list[dict[str, str]]
    source_ids: tuple[str, ...]
    canary: str
    prompt_version: str
    # The instructions without the worked examples, for the leak check: a natural reply
    # can share a few words with an example without leaking anything.
    rules_text: str = ""


def generate_canary() -> str:
    """Return a fresh random 16-hex-character token for one request."""
    return secrets.token_hex(_CANARY_BYTES)


def _one_line(text: str, limit: int) -> str:
    """Strip fence runs, collapse all whitespace to single spaces and cap the length."""
    return " ".join(strip_fence_runs(text).split())[:limit].strip()


def _sections(prompt: Prompt) -> tuple[Prompt, Prompt]:
    """Split *prompt* at the user marker into a system template and a user template."""
    system, marker, user = prompt.body.partition(_USER_MARKER)
    if not marker:
        raise PromptError(f"prompt {prompt.name!r} has no user message section")
    return (
        replace(prompt, body=system.strip()),
        replace(prompt, body=user.strip()),
    )


def _scope_line(tradition_label: str | None) -> str:
    """The sentence telling the model which traditions the notes cover."""
    if tradition_label is None:
        return _SCOPE_ALL
    label = _one_line(tradition_label, _MAX_LABEL_CHARS)
    return f"The person asked about {label}. The notes below are limited to that tradition."


def _sources_block(sources: Sequence[RetrievedChunk]) -> tuple[str, tuple[str, ...]]:
    """Fence each source as S1..Sn in rank order; return the block and the ids."""
    ranked = sorted(sources, key=lambda s: s.rank)
    ids = tuple(f"S{i}" for i in range(1, len(ranked) + 1))
    fenced = [
        fence(
            f"SOURCE {source_id} {_one_line(item.chunk.note_title, _MAX_TITLE_CHARS)}",
            item.chunk.text,
        )
        for source_id, item in zip(ids, ranked, strict=True)
    ]
    return ("\n\n".join(fenced) or _NO_SOURCES), ids


def build_messages(
    question: str,
    sources: Sequence[RetrievedChunk],
    *,
    persona_name: str,
    tradition_label: str | None,
    canary: str,
    correction: str | None = None,
) -> BuiltPrompt:
    """Build the system and user messages for one question.

    Args:
        question: The user's question, already de-identified by the caller.
        sources: Retrieved chunks; numbered S1..Sn in ascending rank order.
        persona_name: The guide's display name, from config.
        tradition_label: The tradition filter's label, or ``None`` for all.
        canary: This request's random token; placed in the system message only.
        correction: Optional retry hint, appended to the user message.

    Returns:
        The messages, the source ids offered, the canary and the prompt version.

    Raises:
        PromptError: If the prompt file is missing or malformed.
    """
    prompt = load_prompt(PROMPT_NAME)
    system, user = _sections(prompt)
    block, ids = _sources_block(sources)
    note = _one_line(correction, _MAX_CORRECTION_CHARS) if correction else ""
    messages = [
        {
            "role": "system",
            "content": system.render(
                persona_name=_one_line(persona_name, _MAX_LABEL_CHARS)
                or _DEFAULT_PERSONA,
                tradition_scope_line=_scope_line(tradition_label),
                canary=canary,
            ),
        },
        {
            "role": "user",
            "content": user.render(
                sources_block=block,
                question_block=fence("QUESTION", question),
                correction_block=note,
            ).strip(),
        },
    ]
    rules = messages[0]["content"].split(_EXAMPLES_MARKER, 1)[0]
    return BuiltPrompt(messages, ids, canary, prompt.version, rules)
