"""Loading versioned prompt templates from ``app/llm/prompts/*.md``.

A prompt file is YAML frontmatter (``name``, ``version``, optional ``model_hints``)
between ``---`` lines, then the template body. Placeholders are ``{name}`` and are
filled in a single pass, so a value is inserted literally and is never expanded
again, even if it contains ``{other}``. This is deliberately not ``str.format`` or
an f-string: untrusted text must never be able to reach a template's syntax
(AGENTS.md section 8.4). Fencing of untrusted content stays in code, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import yaml

from app.exceptions import AuriError

PROMPTS_DIR: Final = Path(__file__).parent / "prompts"

_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")
# Only {identifier} is a placeholder, so literal JSON such as {"kind": "answer"}
# in a template needs no escaping.
_PLACEHOLDER: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_FRONTMATTER_MARK: Final = "---"


class PromptError(AuriError):
    """Raised when a prompt file is missing or malformed, or a render is misused."""


@dataclass(frozen=True)
class Prompt:
    """One loaded prompt template."""

    name: str
    version: str
    model_hints: tuple[str, ...]
    body: str

    def render(self, **values: str) -> str:
        """Return the body with every ``{name}`` replaced by its value.

        Values are inserted literally in one pass.

        Raises:
            PromptError: If a placeholder has no value, a value has no placeholder,
                or a value is not a string.
        """
        wanted = set(_PLACEHOLDER.findall(self.body))
        missing = sorted(wanted - values.keys())
        unknown = sorted(values.keys() - wanted)
        if missing:
            raise PromptError(f"prompt {self.name!r} is missing values for: {missing}")
        if unknown:
            raise PromptError(f"prompt {self.name!r} got unknown values: {unknown}")
        if not all(isinstance(v, str) for v in values.values()):
            raise PromptError(f"prompt {self.name!r} values must all be strings")
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], self.body)


def _split_frontmatter(name: str, text: str) -> tuple[str, str]:
    """Split *text* into its frontmatter and body, both unparsed."""
    lines = text.split("\n")
    if lines[0].rstrip() != _FRONTMATTER_MARK:
        raise PromptError(f"prompt {name!r} has no frontmatter")
    for index in range(1, len(lines)):
        if lines[index].rstrip() == _FRONTMATTER_MARK:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise PromptError(f"prompt {name!r} frontmatter is not closed")


def _parse_meta(name: str, raw: str) -> dict[str, Any]:
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PromptError(f"prompt {name!r} frontmatter is not valid YAML") from exc
    if not isinstance(meta, dict):
        raise PromptError(f"prompt {name!r} frontmatter must be a mapping")
    return meta


def _model_hints(name: str, meta: dict[str, Any]) -> tuple[str, ...]:
    hints = meta.get("model_hints", [])
    if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
        raise PromptError(f"prompt {name!r} model_hints must be a list of strings")
    return tuple(hints)


@cache
def load_prompt(name: str) -> Prompt:
    """Load, parse and cache the prompt called *name*.

    Args:
        name: The file stem in ``app/llm/prompts/``, lowercase with underscores.

    Raises:
        PromptError: If the name is unsafe, the file is missing, or its
            frontmatter is malformed, unnamed, unversioned or names another prompt.
    """
    if not _NAME.match(name):
        raise PromptError(f"invalid prompt name {name!r}")
    try:
        text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"prompt {name!r} could not be read") from exc
    raw_meta, body = _split_frontmatter(name, text)
    meta = _parse_meta(name, raw_meta)
    if meta.get("name") != name:
        raise PromptError(f"prompt file {name!r} must declare name: {name}")
    version = meta.get("version")
    if not isinstance(version, str) or not version:
        raise PromptError(f"prompt {name!r} needs a quoted string version")
    return Prompt(name, version, _model_hints(name, meta), body.strip())
