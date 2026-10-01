"""The Guide's chat chain as the service uses it in production (plan 14.2; split out
of ``priest_service``).

It resolves the primary and fallback endpoints on every call, so a configuration change
applies at once, and it only ever uses endpoints ``app.llm.chat_endpoint`` validated,
which refuses hosted providers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from app.llm.chat_client import ChatClient, ChatMessage, ChatResult, PriestLLMChain
from app.llm.chat_endpoint import ChatEndpoint, resolve_fallback, resolve_primary


class LiveChain:
    """Resolves the priest endpoints on every call, so config changes apply at once.

    Never the ``auto`` chain: only the primary and fallback that
    ``app.llm.chat_endpoint`` validated, which refuses hosted providers.
    """

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        """Bind an optional HTTP client (tests pass a mock transport)."""
        self._http = http_client

    def _client(self, endpoint: ChatEndpoint | None) -> ChatClient | None:
        return None if endpoint is None else ChatClient(endpoint, client=self._http)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        request_id: str,
        max_tokens: int = 600,
        temperature: float = 0.2,
        seed: int | None = None,
        json_schema: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """Generate through the primary then the fallback.

        Raises:
            PriestEndpointError: If an address must not be used (a hosted provider).
            PriestLLMError: If no server is configured or none gave a reply.
        """
        chain = PriestLLMChain(
            self._client(resolve_primary()), self._client(resolve_fallback())
        )
        return await chain.complete(
            messages,
            request_id=request_id,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            json_schema=json_schema,
            timeout=timeout,
        )
