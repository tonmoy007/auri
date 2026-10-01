"""Live smoke test of the chat server named in the config (the external vLLM box).

Skipped unless ``RUN_LIVE_LLM=1`` and never run in CI. It sends one harmless prompt,
no user data, to whatever ``PRIEST_LLM_*`` (or, for the prototype, ``THEMES_LLM_*``)
points at, and checks that a reply comes back. Run it on purpose:

    RUN_LIVE_LLM=1 python3 -m pytest backend/tests/live -q

It reads your environment, so it will use the server and key you have configured;
the key is sent only to that server.
"""

from __future__ import annotations

import pytest
from app.llm.chat_client import ChatClient, ChatMessage
from app.llm.chat_endpoint import resolve_primary

pytestmark = pytest.mark.live_llm


@pytest.mark.asyncio
async def test_the_configured_chat_server_answers_a_harmless_prompt() -> None:
    # Arrange
    endpoint = resolve_primary()
    if endpoint is None:
        pytest.skip(
            "no chat server is configured (PRIEST_LLM_BASE_URL or THEMES_LLM_BASE_URL)"
        )
        return
    client = ChatClient(endpoint)

    # Act
    result = await client.complete(
        [ChatMessage(role="user", content="Reply with the single word: ready")],
        request_id="live-smoke",
        max_tokens=16,
    )

    # Assert
    assert result.text.strip() != ""
    assert result.endpoint_kind in {"vllm", "ollama"}
