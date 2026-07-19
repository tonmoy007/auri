"""Tests for bot/moderation_handlers.py.

httpx calls are mocked via httpx.MockTransport (no extra test dependency,
and it exercises the real request/response parsing code rather than
stubbing the whole client).
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.config import BotSettings
from bot.moderation_handlers import handle_moderation_callback, poll_moderation_queue


@pytest.fixture
def moderation_settings(bot_settings: BotSettings) -> BotSettings:
    """bot_settings (conftest) with moderation configured and enabled."""
    return bot_settings.model_copy(
        update={
            "moderator_chat_id": "999",
            "moderation_api_key": "test-mod-key",
        }
    )


_RealAsyncClient = httpx.AsyncClient


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
async def test_poll_moderation_queue_notifies_new_flagged_confession(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange
    mock_context.bot_data["settings"] = moderation_settings
    mock_context.bot.send_message = AsyncMock()
    queue_item = {
        "id": "abc-123",
        "category": "work",
        "ai_summary": "A summary",
        "transcript": "the full transcript",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-moderation-api-key"] == "test-mod-key"
        return httpx.Response(200, json=[queue_item])

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await poll_moderation_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_called_once()
    call_kwargs = mock_context.bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == "999"
    assert "work" in call_kwargs["text"]
    assert "abc-123" in mock_context.bot_data["notified_moderation_ids"]


@pytest.mark.asyncio
async def test_poll_moderation_queue_skips_already_notified(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange — regression: a repeating job must not re-notify the same item
    mock_context.bot_data["settings"] = moderation_settings
    mock_context.bot_data["notified_moderation_ids"] = {"abc-123"}
    mock_context.bot.send_message = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "category": "x",
                    "ai_summary": None,
                    "transcript": "t",
                }
            ],
        )

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await poll_moderation_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_poll_moderation_queue_noop_when_moderation_disabled(
    mock_context: MagicMock, bot_settings: BotSettings
) -> None:
    # Arrange — bot_settings (unmodified) has no moderator_chat_id configured
    mock_context.bot_data["settings"] = bot_settings
    mock_context.bot.send_message = AsyncMock()

    # Act
    await poll_moderation_queue(mock_context)

    # Assert
    mock_context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_handle_moderation_callback_approve_edits_message(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange
    mock_context.bot_data["settings"] = moderation_settings
    mock_context.bot_data["notified_moderation_ids"] = {"abc-123"}
    update = MagicMock()
    update.callback_query.data = "modapprove:abc-123"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/moderation/abc-123/approve"
        return httpx.Response(200, json={"id": "abc-123", "status": "pending"})

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await handle_moderation_callback(update, mock_context)

    # Assert
    update.callback_query.answer.assert_called_once()
    update.callback_query.edit_message_text.assert_called_once()
    assert "Approved" in update.callback_query.edit_message_text.call_args.args[0]
    assert "abc-123" not in mock_context.bot_data["notified_moderation_ids"]


@pytest.mark.asyncio
async def test_handle_moderation_callback_reject_edits_message(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange
    mock_context.bot_data["settings"] = moderation_settings
    update = MagicMock()
    update.callback_query.data = "modreject:abc-123"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/moderation/abc-123/reject"
        return httpx.Response(200, json={"id": "abc-123", "status": "deleted"})

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await handle_moderation_callback(update, mock_context)

    # Assert
    assert "Rejected" in update.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_moderation_callback_handles_already_processed_404(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange — regression: two moderators racing on the same item
    mock_context.bot_data["settings"] = moderation_settings
    update = MagicMock()
    update.callback_query.data = "modapprove:abc-123"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await handle_moderation_callback(update, mock_context)

    # Assert
    assert (
        "already handled" in update.callback_query.edit_message_text.call_args.args[0]
    )


@pytest.mark.asyncio
async def test_handle_moderation_callback_ignores_unrelated_callback_data(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange
    mock_context.bot_data["settings"] = moderation_settings
    update = MagicMock()
    update.callback_query.data = "something-else"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    # Act
    await handle_moderation_callback(update, mock_context)

    # Assert
    update.callback_query.answer.assert_not_called()
    update.callback_query.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_moderation_callback_reports_backend_call_failure(
    mock_context: MagicMock, moderation_settings: BotSettings
) -> None:
    # Arrange — regression: a network error must not crash the handler
    mock_context.bot_data["settings"] = moderation_settings
    update = MagicMock()
    update.callback_query.data = "modapprove:abc-123"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    # Act
    with patch(
        "bot.moderation_handlers.httpx.AsyncClient", _mock_async_client(handler)
    ):
        await handle_moderation_callback(update, mock_context)

    # Assert
    assert (
        "Could not reach" in update.callback_query.edit_message_text.call_args.args[0]
    )
