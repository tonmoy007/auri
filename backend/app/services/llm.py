"""LLM orchestration service for de-identification, categorisation and summarisation."""

from __future__ import annotations

import logging
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)

Provider = Literal["openai", "claude"]


class LLMService:
    """Thin wrapper around LLM providers for Auri-specific tasks.

    Each method sends a structured prompt to the configured provider and
    returns the parsed result.  The class defaults to OpenAI but can be
    switched to Claude via the ``provider`` argument.
    """

    def __init__(self, provider: Provider = "openai") -> None:
        self._provider: Provider = provider

    # ── Public API ────────────────────────────────────────────────────────

    def deidentify(self, text: str) -> str:
        """Remove or obfuscate personally-identifiable information from *text*.

        Steps:
        1. Run regex-based PII stripping (emails, phones, SSNs, etc.).
        2. Send the result through an LLM prompt to catch edge-cases the
           regex missed.

        Args:
            text: Raw transcript potentially containing PII.

        Returns:
            De-identified text with PII replaced by placeholders.
        """
        from app.services.deidentify import strip_pii_regex

        # First pass — fast regex.
        cleaned = strip_pii_regex(text)

        # Second pass — LLM polish.
        prompt = (
            "You are a PII redaction assistant. Review the following text and "
            "replace any remaining personally-identifiable information (names, "
            "addresses, phone numbers, email addresses, IP addresses, etc.) with "
            "appropriate placeholders like [NAME], [ADDRESS], [PHONE]. "
            "Do **not** change the meaning or flow of the text.\n\n"
            f"Text:\n{cleaned}"
        )
        return self._call_llm(prompt)

    def categorize(self, text: str) -> str:
        """Assign a single category label to *text*.

        Returns:
            A short category string such as ``"health"``, ``"faith"``,
            ``"relationships"``, ``"work"``, ``"family"`` or ``"other"``.
        """
        prompt = (
            "Assign exactly one category to the following confession text. "
            "Choose from: health, faith, relationships, work, family, guilt, "
            "grief, addiction, trauma, other. Return ONLY the category label, "
            "nothing else.\n\n"
            f"Text:\n{text}"
        )
        return self._call_llm(prompt).strip().lower()

    def summarize(self, text: str) -> str:
        """Produce a concise, de-identified summary of *text*.

        The summary is suitable for forwarding to a recipient department
        and must **not** contain any PII.

        Args:
            text: Already de-identified transcript.

        Returns:
            A 2–3 sentence summary.
        """
        prompt = (
            "Summarise the following confession in 2-3 sentences. "
            "Remove all identifying details. Be compassionate and neutral "
            "in tone. Output only the summary.\n\n"
            f"Text:\n{text}"
        )
        return self._call_llm(prompt)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """Route *prompt* to the active provider and return the response text."""
        if self._provider == "openai":
            return self._call_openai(prompt)
        elif self._provider == "claude":
            return self._call_claude(prompt)
        else:
            raise ValueError(f"Unsupported LLM provider: {self._provider!r}")

    def _call_openai(self, prompt: str) -> str:
        """Send *prompt* to OpenAI ChatCompletion and return the assistant reply."""
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is not installed") from exc

        client = OpenAI(api_key=settings.LLM_API_KEY)
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""

    def _call_claude(self, prompt: str) -> str:
        """Send *prompt* to Anthropic Claude and return the assistant reply."""
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx package is not installed") from exc

        api_key = settings.LLM_API_KEY
        if not api_key:
            logger.error("LLM_API_KEY is not set — cannot call Claude.")
            return ""

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": settings.LLM_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]
        except Exception as exc:
            logger.error("Claude API call failed: %s", exc)
            return ""
