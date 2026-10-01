"""An async OpenAI-compatible chat client for priest mode, and the server chain.

Works against vLLM and against Ollama's ``/v1``. Async on purpose: a generation takes
seconds, and a blocking call in an async handler would stall every other request.

The server is not trusted. The reply is capped in size, the whole call (retry
included) has one deadline, no redirect is followed, proxy environment variables are
ignored, and any failure raises ``ChatFailure`` with a fixed message. That message
never carries the prompt, the reply or the key, and the original exception is
dropped (an httpx error holds the request, headers and key included).

Retries follow plan 3.3, not AGENTS 9.1: one retry, on a connect error only, never on
a timeout, because a 30 s answer deadline cannot absorb a backoff ladder.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import httpx

from app.exceptions import PriestLLMError
from app.llm.chat_endpoint import ChatEndpoint, EndpointKind, refuse_third_party_host
from app.services.openai_compatible import strip_reasoning

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES: Final = 256 * 1024
# A server that does not answer a connection within this is treated as down, so the
# fallback still has time left.
_CONNECT_SECONDS: Final = 3.0
_MIN_SERVER_SECONDS: Final = 0.1
_SCHEMA_NAME: Final = "priest_answer"
_RETRYABLE_ELSEWHERE: Final = frozenset({408, 429})
# Reasons whose fixed messages are safe to show; nothing else ever reaches a message.
_REASONS: Final = frozenset(
    {"connect", "network", "timeout", "http_status", "too_large", "malformed", "empty"}
)


@dataclass(frozen=True)
class ChatMessage:
    """One message of a chat request."""

    role: str
    content: str


@dataclass(frozen=True)
class ChatResult:
    """A usable reply, and which kind of server gave it."""

    text: str
    model: str
    endpoint_kind: EndpointKind


class ChatFailure(PriestLLMError):
    """A chat call that produced no usable reply; ``reason`` says how it failed.

    The message is built from a fixed reason and, for an HTTP error, its status code.
    """

    def __init__(self, reason: str, status: int | None = None) -> None:
        if reason not in _REASONS:
            raise ValueError("unknown failure reason")
        suffix = f" {status}" if status is not None else ""
        super().__init__(f"chat server call failed ({reason}{suffix})")
        self.reason = reason
        self.status = status

    @property
    def allows_fallback(self) -> bool:
        """Whether another server may be tried: anything but a 4xx other than 408/429."""
        if self.status is None:
            return True
        return not (400 <= self.status < 500) or self.status in _RETRYABLE_ELSEWHERE


def _response_format(
    kind: EndpointKind, json_schema: Mapping[str, object] | None
) -> dict[str, object] | None:
    """vLLM enforces the schema by guided decoding; Ollama gets plain JSON mode."""
    if json_schema is None:
        return None
    if kind == "ollama":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _SCHEMA_NAME,
            "strict": True,
            "schema": dict(json_schema),
        },
    }


def _request_body(
    endpoint: ChatEndpoint,
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int,
    temperature: float,
    seed: int | None,
    json_schema: Mapping[str, object] | None,
) -> dict[str, object]:
    """Build the chat request: bounded, low temperature, reasoning switched off."""
    body: dict[str, object] = {
        "model": endpoint.model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # vLLM hands this to the chat template; servers without a thinking mode ignore it.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if seed is not None:
        body["seed"] = seed
    response_format = _response_format(endpoint.kind, json_schema)
    if response_format is not None:
        body["response_format"] = response_format
    return body


async def _read_capped(response: httpx.Response) -> bytes:
    """Read the body, refusing to buffer more than ``MAX_RESPONSE_BYTES``."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ChatFailure("too_large")
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > MAX_RESPONSE_BYTES:
            raise ChatFailure("too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _reply_text(body: bytes) -> str:
    """Pull the assistant text out of a chat-completions body.

    Raises:
        ChatFailure: If the body is not the expected shape, or the text is empty once
            any reasoning block is removed.
    """
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        raise ChatFailure("malformed") from None
    if not isinstance(content, str):
        raise ChatFailure("malformed")
    text = strip_reasoning(content)
    if not text:
        raise ChatFailure("empty")
    return text


class ChatClient:
    """Sends chat requests to one validated endpoint."""

    def __init__(
        self, endpoint: ChatEndpoint, *, client: httpx.AsyncClient | None = None
    ) -> None:
        """Bind to *endpoint*; *client* is for tests (a mock transport).

        Raises:
            PriestEndpointError: If the endpoint is a hosted provider.
        """
        refuse_third_party_host(endpoint.host)
        self._endpoint = endpoint
        self._client = client

    @property
    def kind(self) -> EndpointKind:
        """Whether this client talks to vLLM or Ollama."""
        return self._endpoint.kind

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
        """Send *messages* and return the reply text.

        Args:
            messages: The system and user messages.
            request_id: Sent as ``X-Request-ID``; a random id, never derived from a user.
            max_tokens: Reply budget.
            temperature: Sampling temperature.
            seed: For repeatable evals.
            json_schema: Asks for structured output (guided decoding on vLLM, JSON
                mode on Ollama).
            timeout: One deadline for the whole call; defaults to the endpoint's.

        Returns:
            The reply with any reasoning block removed.

        Raises:
            ChatFailure: On any failure, with a fixed message.
        """
        body = _request_body(
            self._endpoint,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            json_schema=json_schema,
        )
        deadline = timeout if timeout is not None else self._endpoint.timeout_seconds
        try:
            text = await asyncio.wait_for(
                self._post_with_retry(body, request_id, deadline), timeout=deadline
            )
        except TimeoutError:
            raise ChatFailure("timeout") from None
        return ChatResult(
            text=text, model=self._endpoint.model, endpoint_kind=self._endpoint.kind
        )

    async def _post_with_retry(
        self, body: dict[str, object], request_id: str, deadline: float
    ) -> str:
        """One attempt, and a second only if the first could not connect."""
        try:
            return await self._attempt(body, request_id, deadline)
        except ChatFailure as failure:
            if failure.reason != "connect":
                raise
        return await self._attempt(body, request_id, deadline)

    async def _attempt(
        self, body: dict[str, object], request_id: str, deadline: float
    ) -> str:
        """Post once and return the reply text, translating transport errors."""
        owned = self._client is None
        client = self._client or httpx.AsyncClient(trust_env=False)
        try:
            return await self._stream_reply(client, body, request_id, deadline)
        except httpx.TimeoutException:
            raise ChatFailure("timeout") from None
        except httpx.ConnectError:
            raise ChatFailure("connect") from None
        except httpx.HTTPError:
            raise ChatFailure("network") from None
        finally:
            if owned:
                await client.aclose()

    async def _stream_reply(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
        request_id: str,
        deadline: float,
    ) -> str:
        """Send the request and read at most ``MAX_RESPONSE_BYTES`` of the reply."""
        # No compression: the size cap counts bytes read, so a reply must not expand.
        headers = {"X-Request-ID": request_id, "Accept-Encoding": "identity"}
        if self._endpoint.api_key:
            headers["Authorization"] = f"Bearer {self._endpoint.api_key}"
        async with client.stream(
            "POST",
            f"{self._endpoint.base_url}/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=httpx.Timeout(deadline, connect=min(_CONNECT_SECONDS, deadline)),
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise ChatFailure("http_status", response.status_code)
            return _reply_text(await _read_capped(response))


class PriestLLMChain:
    """Tries the primary server, then the fallback, and nothing else.

    Never the ``auto`` chain: that one can reach hosted providers.
    """

    def __init__(self, primary: ChatClient | None, fallback: ChatClient | None) -> None:
        """Bind the two clients; either may be ``None``."""
        self._primary = primary
        self._fallback = fallback

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
        """Ask the primary, falling back on a failure that another server could fix.

        The fallback is tried after a connect error, a timeout, a 5xx, a 408/429 or an
        empty reply, not after any other 4xx (a bad request would fail there too).
        ``ChatResult.endpoint_kind`` records which kind of server answered.

        Raises:
            PriestLLMError: If no server is configured or none gave a usable reply.
        """
        servers = [c for c in (self._primary, self._fallback) if c is not None]
        if not servers:
            raise PriestLLMError("no chat server is configured")
        clock = asyncio.get_running_loop().time
        overall_end = None if timeout is None else clock() + timeout
        for index, server in enumerate(servers):
            left = (
                None
                if overall_end is None
                else max(_MIN_SERVER_SECONDS, overall_end - clock())
            )
            try:
                result = await server.complete(
                    messages,
                    request_id=request_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    seed=seed,
                    json_schema=json_schema,
                    timeout=left,
                )
            except ChatFailure as failure:
                logger.warning(
                    "priest chat %s failed (%s)", server.kind, failure.reason
                )
                if not failure.allows_fallback:
                    raise
                continue
            logger.info(
                "priest chat answered by %s (server %d)", result.endpoint_kind, index
            )
            return result
        raise PriestLLMError("no chat server gave a usable reply")
