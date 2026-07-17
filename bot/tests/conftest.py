"""Shared fixtures for the bot test suite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import BotSettings


@pytest.fixture
def bot_settings() -> BotSettings:
    """A valid BotSettings instance isolated from any real .env file."""
    return BotSettings(
        _env_file=None,
        bot_token="test-bot-token",
        webhook_url="https://bot.test/webhook",
        webhook_secret="test-webhook-secret",
        backend_url="https://backend.test",
        environment="development",
    )


@pytest.fixture
def mock_update() -> MagicMock:
    """A Telegram Update with a mocked effective_user and effective_message."""
    update = MagicMock(name="Update")
    update.effective_user = MagicMock(first_name="Alex")
    update.effective_message = MagicMock(name="Message")
    update.effective_message.reply_text = AsyncMock()
    update.effective_message.chat_id = 12345
    update.effective_message.message_id = 678
    return update


@pytest.fixture
def mock_context(bot_settings: BotSettings) -> MagicMock:
    """A Telegram context whose bot_data carries the bot settings."""
    context = MagicMock(name="Context")
    context.bot_data = {"settings": bot_settings}
    return context
