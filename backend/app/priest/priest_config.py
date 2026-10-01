"""Typed, live priest-mode settings with safe fallbacks.

Values can be changed from the dashboard without a restart, so every accessor reads
the live layer and treats an unusable value as the built-in default rather than
raising in the request path. The kill switch is the exception in spirit: anything
that is not clearly "on" means off.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections.abc import Callable
from typing import Any, Final

from app.config import Settings, settings
from app.priest.schemas import TraditionId
from app.services.settings_service import get_config

logger = logging.getLogger(__name__)

_TRUE: Final = frozenset({"true", "1", "yes", "on"})
_FALSE: Final = frozenset({"false", "0", "no", "off"})
_DEFAULT_PERSONA: Final = "Guide"
_MAX_PERSONA_CHARS: Final = 40
_KNOWN_TRADITIONS: Final = frozenset(t.value for t in TraditionId)
_MODEL_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")

# (low, high, default) per numeric setting. The live accessors and the write-time
# validator both read these, so what an admin may store and what is used agree.
_INT_RANGES: Final[dict[str, tuple[int, int, int]]] = {
    "PRIEST_TOP_K": (1, 12, 6),
    "PRIEST_LLM_TIMEOUT_SECONDS": (1, 120, 20),
    "PRIEST_TOTAL_DEADLINE_SECONDS": (5, 300, 30),
    "PRIEST_RATE_LIMIT_PER_MINUTE": (1, 1000, 4),
    "PRIEST_RATE_LIMIT_PER_DAY": (1, 100000, 40),
    "PRIEST_MAX_CONCURRENCY": (1, 64, 4),
}
_FLOAT_RANGES: Final[dict[str, tuple[float, float]]] = {
    "PRIEST_MIN_RELEVANCE_DENSE": (0.0, 1.0),
    "PRIEST_MIN_RELEVANCE_BM25": (0.0, 1000.0),
}


def _default(key: str) -> Any:
    """The built-in default of a setting, so there is one place that pins it."""
    return Settings.model_fields[key].default


def _raw(key: str) -> str:
    return get_config(key, str(getattr(settings, key)))


def _int(key: str, low: int, high: int, default: int) -> int:
    """Return *key* as an integer in ``[low, high]``, else *default*."""
    try:
        value = int(_raw(key).strip())
    except ValueError:
        logger.warning("%s is not an integer; using the default", key)
        return default
    return value if low <= value <= high else default


def _float(key: str, low: float, high: float, default: float) -> float:
    """Return *key* as a finite float in ``[low, high]``, else *default*."""
    try:
        value = float(_raw(key).strip())
    except ValueError:
        logger.warning("%s is not a number; using the default", key)
        return default
    if not math.isfinite(value) or not low <= value <= high:
        return default
    return value


def enabled() -> bool:
    """Whether priest mode is on. Anything other than a clear "yes" is off."""
    return _raw("PRIEST_MODE_ENABLED").strip().lower() in _TRUE


def _clean_persona(raw: str) -> str:
    """*raw* with control characters as spaces and whitespace collapsed."""
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("C") else ch for ch in raw
    )
    return " ".join(cleaned.split())


def _persona_problem(name: str) -> str | None:
    """Why a cleaned persona name is unusable, or ``None``."""
    if not name or len(name) > _MAX_PERSONA_CHARS:
        return f"must be 1 to {_MAX_PERSONA_CHARS} characters"
    # Letters, spaces, apostrophes and hyphens only: the name goes into the prompt, so
    # it must not be able to carry a sentence of instructions.
    if not all(ch.isalpha() or ch in " '-" for ch in name):
        return "may hold only letters, spaces, apostrophes and hyphens"
    return None


def persona_name() -> str:
    """The user-facing name: printable, one line, at most 40 characters."""
    name = _clean_persona(_raw("PRIEST_PERSONA_NAME"))
    return _DEFAULT_PERSONA if _persona_problem(name) else name


def top_k() -> int:
    """How many passages to hand the model."""
    return _int("PRIEST_TOP_K", *_INT_RANGES["PRIEST_TOP_K"])


def llm_timeout_seconds() -> int:
    """Per-call limit for a chat request."""
    return _int(
        "PRIEST_LLM_TIMEOUT_SECONDS", *_INT_RANGES["PRIEST_LLM_TIMEOUT_SECONDS"]
    )


def total_deadline_seconds() -> int:
    """Limit for one whole question, after which excerpts are returned."""
    return _int(
        "PRIEST_TOTAL_DEADLINE_SECONDS", *_INT_RANGES["PRIEST_TOTAL_DEADLINE_SECONDS"]
    )


def rate_limit_per_minute() -> int:
    """Questions one device may ask per minute."""
    return _int(
        "PRIEST_RATE_LIMIT_PER_MINUTE", *_INT_RANGES["PRIEST_RATE_LIMIT_PER_MINUTE"]
    )


def rate_limit_per_day() -> int:
    """Questions one device may ask per day."""
    return _int("PRIEST_RATE_LIMIT_PER_DAY", *_INT_RANGES["PRIEST_RATE_LIMIT_PER_DAY"])


def rate_limit_per_ip_per_minute() -> int:
    """Questions one client address may ask per minute (only with a trusted proxy header).

    Wider than the device limit on purpose: a whole office can share one address.
    """
    return _int("PRIEST_RATE_LIMIT_PER_IP_PER_MINUTE", 1, 10000, 30)


def rate_limit_per_ip_per_day() -> int:
    """Questions one client address may ask per day (only with a trusted proxy header)."""
    return _int("PRIEST_RATE_LIMIT_PER_IP_PER_DAY", 1, 1000000, 1000)


def max_concurrency() -> int:
    """Questions answered at once per API process."""
    return _int("PRIEST_MAX_CONCURRENCY", *_INT_RANGES["PRIEST_MAX_CONCURRENCY"])


def min_relevance_dense() -> float:
    """Best dense cosine at or above which the library counts as covering a question."""
    low, high = _FLOAT_RANGES["PRIEST_MIN_RELEVANCE_DENSE"]
    return _float(
        "PRIEST_MIN_RELEVANCE_DENSE", low, high, _default("PRIEST_MIN_RELEVANCE_DENSE")
    )


def min_relevance_bm25() -> float:
    """Best BM25 score at or above which the library counts as covering a question."""
    low, high = _FLOAT_RANGES["PRIEST_MIN_RELEVANCE_BM25"]
    return _float(
        "PRIEST_MIN_RELEVANCE_BM25", low, high, _default("PRIEST_MIN_RELEVANCE_BM25")
    )


def llm_model() -> str:
    """The model name asked of the primary chat server."""
    return _raw("PRIEST_LLM_MODEL").strip()


def fallback_model() -> str:
    """The model asked of the fallback server; empty means no fallback."""
    return _raw("PRIEST_FALLBACK_MODEL").strip()


def embed_model() -> str:
    """The embedding model for the next index build."""
    return _raw("PRIEST_EMBED_MODEL").strip() or str(_default("PRIEST_EMBED_MODEL"))


def fallback_base_url() -> str:
    """The fallback chat server: the configured one, else Ollama's ``/v1``.

    Both come from the environment. ``OLLAMA_BASE_URL`` is editable from the dashboard
    for confession moderation, but a question must never follow a dashboard edit, so
    the live layer is not read here.
    """
    explicit = settings.PRIEST_FALLBACK_BASE_URL.strip().rstrip("/")
    if explicit:
        return explicit
    return f"{settings.OLLAMA_BASE_URL.strip().rstrip('/')}/v1"


def enabled_traditions() -> frozenset[str] | None:
    """The traditions an admin has left on, or ``None`` for all.

    A malformed list, or one with no known id, means all: a typo must not silently
    empty the library.
    """
    raw = _raw("PRIEST_TRADITIONS_ENABLED").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("PRIEST_TRADITIONS_ENABLED is not valid JSON; using all")
        return None
    if not isinstance(parsed, list):
        return None
    known = frozenset(
        v for v in parsed if isinstance(v, str) and v in _KNOWN_TRADITIONS
    )
    return known or None


# ── write-time validation (PUT /admin/config) ─────────────────────────────


def _int_problem(value: str, low: int, high: int) -> str | None:
    try:
        number = int(value.strip())
    except ValueError:
        return "must be a whole number"
    return None if low <= number <= high else f"must be between {low} and {high}"


def _float_problem(value: str, low: float, high: float) -> str | None:
    try:
        number = float(value.strip())
    except ValueError:
        return "must be a number"
    if not math.isfinite(number) or not low <= number <= high:
        return f"must be a number between {low} and {high}"
    return None


def _switch_problem(value: str) -> str | None:
    if value.strip().lower() in _TRUE | _FALSE:
        return None
    return "must be true or false"


def _persona_write_problem(value: str) -> str | None:
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        return "must not contain control characters"
    return _persona_problem(_clean_persona(value))


def _model_problem(value: str, *, optional: bool = False) -> str | None:
    if optional and value == "":
        return None
    if _MODEL_NAME.fullmatch(value):
        return None
    return "must be a model name (letters, digits and . _ : / @ + -, up to 200)"


def _traditions_problem(value: str) -> str | None:
    if value.strip() == "":
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return "must be empty or a JSON list of tradition ids"
    if not isinstance(parsed, list) or not parsed:
        return "must be empty (all traditions) or a non-empty JSON list"
    if not all(isinstance(v, str) and v in _KNOWN_TRADITIONS for v in parsed):
        return "names a tradition that does not exist"
    return None


_TEXT_RULES: Final[dict[str, Callable[[str], str | None]]] = {
    "PRIEST_MODE_ENABLED": _switch_problem,
    "PRIEST_PERSONA_NAME": _persona_write_problem,
    "PRIEST_LLM_MODEL": _model_problem,
    "PRIEST_FALLBACK_MODEL": lambda v: _model_problem(v, optional=True),
    "PRIEST_EMBED_MODEL": _model_problem,
    "PRIEST_TRADITIONS_ENABLED": _traditions_problem,
}


def validation_error(key: str, value: str) -> str | None:
    """Why *value* may not be stored for the Guide setting *key*, or ``None``.

    The live accessors already fall back on a bad value; this refuses it at the
    door so a typo cannot silently turn the Guide off or loosen a limit. The reason
    never repeats the value. A key with no rule is refused (fail closed).
    """
    if key in _INT_RANGES:
        low, high, _default_value = _INT_RANGES[key]
        return _int_problem(value, low, high)
    if key in _FLOAT_RANGES:
        return _float_problem(value, *_FLOAT_RANGES[key])
    rule = _TEXT_RULES.get(key)
    return rule(value) if rule else "has no validation rule"
