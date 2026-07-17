"""Tests for bot/main.py command and message handlers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from bot.config import BotSettings
from bot.main import confess, forward, handle_confession_message, help_command, start


@pytest.mark.asyncio
async def test_start_replies_with_welcome_and_web_url(
    mock_update: MagicMock, mock_context: MagicMock, bot_settings: BotSettings
) -> None:
    # Arrange — mock_update/mock_context come from conftest fixtures

    # Act
    await start(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "Alex" in reply_text
    assert bot_settings.web_url in reply_text


@pytest.mark.asyncio
async def test_start_ignores_update_without_effective_user(
    mock_update: MagicMock, mock_context: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    mock_update.effective_user = None
    caplog.set_level(logging.DEBUG, logger="bot.main")

    # Act
    await start(mock_update, mock_context)

    # Assert
    mock_update.effective_message.reply_text.assert_not_called()
    assert "no effective_user" in caplog.text


@pytest.mark.asyncio
async def test_help_command_lists_all_commands(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — mock_update/mock_context come from conftest fixtures

    # Act
    await help_command(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "/start" in reply_text
    assert "/confess" in reply_text
    assert "/forward" in reply_text


@pytest.mark.asyncio
async def test_confess_explains_voice_mask_choice(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — mock_update/mock_context come from conftest fixtures

    # Act
    await confess(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "voice mask" in reply_text


@pytest.mark.asyncio
async def test_forward_confirms_delivery(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — mock_update/mock_context come from conftest fixtures

    # Act
    await forward(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "Confession Received" in reply_text


@pytest.mark.asyncio
async def test_handle_confession_message_confirms_delivery(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — mock_update/mock_context come from conftest fixtures

    # Act
    await handle_confession_message(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "Delivery Confirmed" in reply_text


@pytest.mark.asyncio
async def test_handle_confession_message_ignores_missing_message(
    mock_update: MagicMock, mock_context: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    original_message = mock_update.effective_message
    mock_update.effective_message = None
    caplog.set_level(logging.DEBUG, logger="bot.main")

    # Act
    await handle_confession_message(mock_update, mock_context)

    # Assert
    original_message.reply_text.assert_not_called()
    assert "no effective_message" in caplog.text
