"""Tests for bot/delivery_handlers.py.

httpx calls are mocked via httpx.MockTransport (no extra test dependency,
and it exercises the real request/response parsing code rather than
stubbing the whole client), matching test_moderation_handlers.py's pattern.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.config import BotSettings
from bot.delivery_handlers import delivery_dedupe_key, poll_delivery_queue

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def delivery_settings(bot_settings: BotSettings) -> BotSettings:
    """bot_settings (conftest) with delivery configured and enabled."""
    return bot_settings.model_copy(
        update={
            "delivery_api_key": "test-delivery-key",
            "department_chat_ids": "HR:111,Engineering:222",
        }
    )


def _mock_async_client(handler: Callable[[httpx.Request], httpx.Response]):
    """Factory that replaces httpx.AsyncClient(...) with one on a MockTransport.

    Must call the ORIGINAL AsyncClient class (captured above, before any
    patching), not `httpx.AsyncClient` — the latter recurses into whatever
    `patch(...)` currently has it replaced with, i.e. this very factory.
    """

    def factory(*args, **kwargs) -> httpx.AsyncClient:
        return _RealAsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url", ""),
        )

    return factory


@pytest.mark.asyncio
async def test_poll_delivery_queue_delivers_and_marks_delivered(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock()
    queue_item = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": "A summary",
        "transcript": "the full transcript",
        "recipient_dept": "HR",
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/delivery/queue":
            assert request.headers["x-delivery-api-key"] == "test-delivery-key"
            return httpx.Response(200, json=[queue_item])
        assert request.url.path == "/api/v1/delivery/abc-123/delivered"
        return httpx.Response(200, json={**queue_item, "status": "forwarded"})

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_called_once()
    call_kwargs = mock_context.bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "111"
    assert "work" in call_kwargs["text"]
    assert delivery_dedupe_key(queue_item) in mock_context.bot_data["delivered_ids"]
    assert "/api/v1/delivery/abc-123/delivered" in calls


@pytest.mark.asyncio
async def test_poll_delivery_queue_skips_already_delivered(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — regression: a repeating job must not re-deliver the same item
    mock_context.bot_data["settings"] = delivery_settings
    queue_item = {
        "id": "abc-123",
        "category": "x",
        "ai_summary": None,
        "transcript": "t",
        "recipient_dept": "HR",
    }
    mock_context.bot_data["delivered_ids"] = {delivery_dedupe_key(queue_item)}
    mock_context.bot.send_message = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[queue_item])

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_poll_delivery_queue_noop_when_delivery_disabled(
    mock_context: MagicMock, bot_settings: BotSettings
) -> None:
    # Arrange — bot_settings (unmodified) has no delivery_api_key configured
    mock_context.bot_data["settings"] = bot_settings
    mock_context.bot.send_message = AsyncMock()

    # Act
    await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_poll_delivery_queue_skips_unmapped_department(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — regression: a department with no configured chat must not crash
    # the poll, and must be left undelivered (retried) rather than dropped.
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock()
    queue_item = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": "A summary",
        "transcript": "t",
        "recipient_dept": "Unmapped Dept",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[queue_item])

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()
    assert mock_context.bot_data.get("delivered_ids", set()) == set()


@pytest.mark.asyncio
async def test_poll_delivery_queue_does_not_mark_delivered_id_when_mark_call_fails(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — regression: if the backend mark-delivered call fails after a
    # successful Telegram send, the item must be retried next poll, not
    # silently forgotten as "delivered" locally.
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock()
    queue_item = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": "A summary",
        "transcript": "t",
        "recipient_dept": "HR",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/delivery/queue":
            return httpx.Response(200, json=[queue_item])
        return httpx.Response(500, json={"detail": "backend error"})

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_called_once()
    assert mock_context.bot_data["delivered_ids"] == set()


@pytest.mark.asyncio
async def test_poll_delivery_queue_continues_after_send_failure(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — regression: one bad delivery must not block the rest of the queue
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock(
        side_effect=[Exception("telegram down"), None]
    )
    items = [
        {
            "id": "bad-1",
            "category": "x",
            "ai_summary": None,
            "transcript": "t1",
            "recipient_dept": "HR",
        },
        {
            "id": "good-2",
            "category": "y",
            "ai_summary": None,
            "transcript": "t2",
            "recipient_dept": "Engineering",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/delivery/queue":
            return httpx.Response(200, json=items)
        return httpx.Response(200, json={"status": "forwarded"})

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    assert mock_context.bot.send_message.call_count == 2
    delivered = mock_context.bot_data["delivered_ids"]
    assert delivery_dedupe_key({"id": "good-2"}) in delivered
    assert delivery_dedupe_key({"id": "bad-1"}) not in delivered


@pytest.mark.asyncio
async def test_poll_delivery_queue_resends_after_a_backend_resend(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — regression for 11.11: HR resending a confession clears
    # delivered_at on the backend, which bumps updated_at. The bot must send
    # it again rather than skipping it as already-delivered.
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock()
    already_sent = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": None,
        "transcript": "t",
        "recipient_dept": "HR",
        "updated_at": "2026-09-04T10:00:00Z",
    }
    after_resend = {**already_sent, "updated_at": "2026-09-04T11:00:00Z"}
    mock_context.bot_data["delivered_ids"] = {delivery_dedupe_key(already_sent)}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/delivery/queue":
            return httpx.Response(200, json=[after_resend])
        return httpx.Response(200, json=after_resend)

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_called_once()
    assert delivery_dedupe_key(after_resend) in mock_context.bot_data["delivered_ids"]


@pytest.mark.asyncio
async def test_poll_delivery_queue_still_skips_an_unchanged_repeat_poll(
    mock_context: MagicMock, delivery_settings: BotSettings
) -> None:
    # Arrange — the dedupe guard must still hold for a row that has not
    # changed, which is the race it was added for
    mock_context.bot_data["settings"] = delivery_settings
    mock_context.bot.send_message = AsyncMock()
    queue_item = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": None,
        "transcript": "t",
        "recipient_dept": "HR",
        "updated_at": "2026-09-04T10:00:00Z",
    }
    mock_context.bot_data["delivered_ids"] = {delivery_dedupe_key(queue_item)}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[queue_item])

    # Act
    with patch("bot.delivery_handlers.httpx.AsyncClient", _mock_async_client(handler)):
        await poll_delivery_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()
