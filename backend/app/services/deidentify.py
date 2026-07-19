"""PII-stripping utilities combining regex patterns with LLM-driven cleanup."""

from __future__ import annotations

import re
from typing import Final

# Signals that an LLM response is a refusal or meta-commentary rather than
# the redacted text itself — discovered via live testing against a local
# Ollama model (2026-07-18): a smaller/weaker model will sometimes refuse
# the redaction prompt outright (self-harm content) or narrate its own
# reasoning instead of returning only the redacted result. Either failure
# mode previously passed the length-only check below and got stored as the
# "de-identified" transcript, corrupting every downstream step that reads
# it (categorization, summarization, and — most seriously — moderation).
_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "i can't help",
    "i cannot help",
    "i can't assist",
    "i cannot assist",
    "i'm not able to",
    "i am not able to",
    "as an ai",
    "<<<begin_user_content>>>",
)

# ── Regex patterns ───────────────────────────────────────────────────────

# Email addresses.
RE_EMAIL: Final[re.Pattern] = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# US/UK phone numbers (basic).
RE_PHONE: Final[re.Pattern] = re.compile(
    r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"
)

# Social Security Numbers (XXX-XX-XXXX).
RE_SSN: Final[re.Pattern] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# IP addresses (IPv4).
RE_IP: Final[re.Pattern] = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Simple name patterns — "My name is X" / "I'm Y".
RE_NAME_PATTERN: Final[re.Pattern] = re.compile(
    r"\b(?:my name is|i['’]m|called)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    re.IGNORECASE,
)

# Street addresses (basic).
RE_ADDRESS: Final[re.Pattern] = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9\s.,]+(?:Street|St|Avenue|Ave|Road|Rd|"
    r"Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl)\b",
    re.IGNORECASE,
)

# Combined ordered list for iterative replacement.
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (RE_EMAIL, "[EMAIL]"),
    (RE_PHONE, "[PHONE]"),
    (RE_SSN, "[SSN]"),
    (RE_IP, "[IP_ADDRESS]"),
    (RE_ADDRESS, "[ADDRESS]"),
    (RE_NAME_PATTERN, "[NAME]"),
]


def strip_pii_regex(text: str) -> str:
    """Remove or replace common PII patterns using regular expressions.

    This is the **first pass** performed before the LLM-based de-identification
    step in :meth:`app.services.llm.LLMService.deidentify`.

    Args:
        text: Input string that may contain PII.

    Returns:
        Text with known PII patterns replaced by generic placeholders.
    """
    result = text

    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def mask_pii_llm_fallback(text: str, llm_response: str) -> str:
    """Merge LLM de-identification output with the original text.

    If the LLM returns a non-empty, plausible result it is used; otherwise
    the regex-pass result is kept as a fallback. "Plausible" excludes
    refusals and prompt meta-commentary (see :data:`_REFUSAL_MARKERS`) —
    those must never be stored as if they were the redacted transcript.

    Args:
        text: Original text (pre-regex pass).
        llm_response: Output from the LLM de-identification call.

    Returns:
        The best-effort de-identified text.
    """
    cleaned = llm_response.strip()
    looks_like_refusal = any(marker in cleaned.lower() for marker in _REFUSAL_MARKERS)
    if cleaned and not looks_like_refusal and len(cleaned) > len(text) * 0.5:
        return cleaned
    return strip_pii_regex(text)
