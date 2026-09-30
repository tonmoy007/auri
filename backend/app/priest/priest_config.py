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
import unicodedata
from typing import Any, Final

from app.config import Settings, settings
from app.priest.schemas import TraditionId
from app.services.settings_service import get_config

logger = logging.getLogger(__name__)

_TRUE: Final = frozenset({"true", "1", "yes", "on"})
_DEFAULT_PERSONA: Final = "Guide"
_MAX_PERSONA_CHARS: Final = 40
_KNOWN_TRADITIONS: Final = frozenset(t.value for t in TraditionId)


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


def persona_name() -> str:
    """The user-facing name: printable, one line, at most 40 characters."""
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("C") else ch
        for ch in _raw("PRIEST_PERSONA_NAME")
    )
    name = " ".join(cleaned.split())
    if not name or len(name) > _MAX_PERSONA_CHARS:
        return _DEFAULT_PERSONA
    return name


def top_k() -> int:
    """How many passages to hand the model."""
    return _int("PRIEST_TOP_K", 1, 12, 6)


def llm_timeout_seconds() -> int:
    """Per-call limit for a chat request."""
    return _int("PRIEST_LLM_TIMEOUT_SECONDS", 1, 120, 20)


def total_deadline_seconds() -> int:
    """Limit for one whole question, after which excerpts are returned."""
    return _int("PRIEST_TOTAL_DEADLINE_SECONDS", 5, 300, 30)


def rate_limit_per_minute() -> int:
    """Questions one device may ask per minute."""
    return _int("PRIEST_RATE_LIMIT_PER_MINUTE", 1, 1000, 4)


def rate_limit_per_day() -> int:
    """Questions one device may ask per day."""
    return _int("PRIEST_RATE_LIMIT_PER_DAY", 1, 100000, 40)


def max_concurrency() -> int:
    """Questions answered at once per API process."""
    return _int("PRIEST_MAX_CONCURRENCY", 1, 64, 4)


def min_relevance_dense() -> float:
    """Best dense cosine at or above which the library counts as covering a question."""
    return _float(
        "PRIEST_MIN_RELEVANCE_DENSE", 0.0, 1.0, _default("PRIEST_MIN_RELEVANCE_DENSE")
    )


def min_relevance_bm25() -> float:
    """Best BM25 score at or above which the library counts as covering a question."""
    return _float(
        "PRIEST_MIN_RELEVANCE_BM25", 0.0, 1000.0, _default("PRIEST_MIN_RELEVANCE_BM25")
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
    """The fallback chat server: the configured one, else Ollama's ``/v1``."""
    explicit = settings.PRIEST_FALLBACK_BASE_URL.strip().rstrip("/")
    if explicit:
        return explicit
    return f"{_raw('OLLAMA_BASE_URL').strip().rstrip('/')}/v1"


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
