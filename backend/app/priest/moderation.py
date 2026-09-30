"""Moderation of a Guide question, on the operator's own Ollama and nowhere else.

Moderation reads the question before any clean-up, so this is the most sensitive call
the Guide makes. Its address and model come from the environment (``OLLAMA_BASE_URL``
and ``OLLAMA_MODEL``), never from the dashboard-editable layer that confession
moderation reads, and it is refused for a hosted provider, for plain http to a public
address and for an Ollama ``-cloud`` model. The call cannot outlive the cap the service
gives it, so a stuck Ollama cannot pile up threads. Any refusal or failure returns
``policy`` (the same fail-closed answer as an unreadable reply), never a crisis.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.exceptions import PriestEndpointError
from app.llm.chat_endpoint import checked_base, refuse_cloud_model
from app.models.confession import ModerationSeverity
from app.services.llm import LLMService

logger = logging.getLogger(__name__)

_CONNECT_SECONDS = 2.0


class PriestModerator(LLMService):
    """``LLMService.moderate`` over the environment's Ollama, bounded by *timeout*."""

    def __init__(self, timeout: float) -> None:
        """Bind the longest one call may take, in seconds."""
        super().__init__(provider="ollama")
        self._timeout = timeout

    def _call_ollama(self, prompt: str) -> str:
        """Ask the environment's Ollama; ``""`` (unparseable, so policy) on any failure."""
        try:
            base, _ = checked_base(settings.OLLAMA_BASE_URL, "OLLAMA_BASE_URL")
            model = settings.OLLAMA_MODEL
            refuse_cloud_model(model)
        except PriestEndpointError:
            logger.warning("priest moderation address or model refused")
            return ""
        try:
            response = httpx.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_ctx": settings.OLLAMA_NUM_CTX},
                },
                timeout=httpx.Timeout(
                    self._timeout, connect=min(_CONNECT_SECONDS, self._timeout)
                ),
                follow_redirects=False,
                trust_env=False,
            )
            response.raise_for_status()
            return str(response.json()["message"]["content"] or "")
        except Exception as exc:  # noqa: BLE001 — fail-safe boundary around an external call; the class name is logged, never the message, which can echo the question
            logger.warning("priest moderation call failed (%s)", type(exc).__name__)
            return ""


def moderate(text: str, *, timeout: float) -> ModerationSeverity:
    """Classify *text*; ``policy`` if the address is refused or the call fails.

    Args:
        text: The question as typed (un-cleaned).
        timeout: The longest the call may take, in seconds.

    Returns:
        The severity the model gave, or ``policy``.
    """
    return PriestModerator(timeout).moderate(text)
