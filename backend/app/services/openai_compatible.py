"""A minimal async client for an OpenAI-compatible chat endpoint (vLLM and others).

Async on purpose: the local Ollama path is a blocking call pushed to a worker
thread, but a remote server is awaited directly. Like every provider here it
fails safe, returning ``""`` rather than raising, and it never logs the key, the
prompt or the reply — only what kind of failure happened.

The server is not trusted. It can be buggy, or on the wrong end of a network
path, so the response is capped in size, the whole call has one deadline, no
redirect is followed, and a reply of an unexpected shape is an empty reply, not
an exception: an exception here would carry the request's local variables (the
key among them) into an error tracker and turn the HR page into a 500.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Final

import httpx

from app.services.themes_endpoint import ThemesEndpoint

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES: Final = 64 * 1024
MAX_REPLY_TOKENS: Final = 2048
_THINK_CLOSE: Final = "</think>"
_THINK_OPEN: Final = "<think>"


class _ResponseTooLarge(Exception):
    """The server sent more than ``MAX_RESPONSE_BYTES``."""


def _request_body(endpoint: ThemesEndpoint, prompt: str) -> dict[str, object]:
    """Build the chat request; deterministic, bounded, with reasoning switched off."""
    return {
        "model": endpoint.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_REPLY_TOKENS,
        # vLLM forwards this to the chat template; models without a thinking
        # mode ignore it.
        "chat_template_kwargs": {"enable_thinking": False},
    }


def strip_reasoning(content: str) -> str:
    """Remove a leading reasoning block from *content*.

    Reasoning models may emit ``<think>…</think>`` before the answer, and the
    braces inside it would confuse JSON extraction. Done with string
    operations rather than a regular expression: an unclosed tag makes a
    lazy pattern quadratic, and this runs on the event loop.

    Args:
        content: The model's reply.

    Returns:
        The text after the last closing tag; ``""`` if a block was opened and
        never closed (the answer, if any, is unusable); else *content* stripped.
    """
    if _THINK_CLOSE in content:
        return content.rpartition(_THINK_CLOSE)[2].strip()
    if _THINK_OPEN in content:
        return ""
    return content.strip()


async def _read_capped(response: httpx.Response) -> bytes:
    """Read the body, refusing to buffer more than ``MAX_RESPONSE_BYTES``."""
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _reply_text(body: bytes) -> str:
    """Pull the assistant text out of a chat-completions body; ``""`` if malformed."""
    content = json.loads(body)["choices"][0]["message"]["content"]
    return strip_reasoning(content) if isinstance(content, str) else ""


async def _post(
    endpoint: ThemesEndpoint,
    prompt: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    """Send the request and return the reply text (may raise; see ``chat_complete``)."""
    headers = (
        {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
    )
    # trust_env=False: proxy environment variables must not redirect the key and
    # the summaries to a proxy, even for a loopback address.
    async with (
        httpx.AsyncClient(
            transport=transport, timeout=endpoint.timeout, trust_env=False
        ) as client,
        client.stream(
            "POST",
            f"{endpoint.base_url}/v1/chat/completions",
            json=_request_body(endpoint, prompt),
            headers=headers,
        ) as response,
    ):
        response.raise_for_status()
        return _reply_text(await _read_capped(response))


def _kind(exc: BaseException) -> str:
    """Name the failure for the log: the exception type, never its message.

    Task groups wrap the real error in an exception group; name the inner one.
    """
    inner = getattr(exc, "exceptions", None)
    if inner:
        return type(inner[0]).__name__
    return type(exc).__name__


async def chat_complete(
    endpoint: ThemesEndpoint,
    prompt: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Send *prompt* to *endpoint* and return the reply text.

    Args:
        endpoint: A validated endpoint (see ``resolve_endpoint``).
        prompt: The full prompt, already fenced against injection.
        transport: An httpx transport, for tests; ``None`` uses the network.

    Returns:
        The reply with any reasoning block removed, or ``""`` on any failure:
        a network or HTTP error, a timeout, an oversized or malformed body, or
        a reply that is not text.
    """
    try:
        # One deadline for the whole call; httpx's own timeout is per phase, so
        # a server dripping a byte at a time could otherwise hold it open.
        return await asyncio.wait_for(
            _post(endpoint, prompt, transport), timeout=endpoint.timeout
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe boundary around an untrusted remote server; the failure kinds (network, HTTP, timeout, oversized or malformed body, task-group wrapping) are open-ended and an escaping exception would carry the request's locals, the key among them, into an error tracker
        logger.warning("themes model call failed (%s)", _kind(exc))
        return ""
