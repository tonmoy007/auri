"""Tests for the bot Application factory."""

from __future__ import annotations

from telegram.ext import CommandHandler, MessageHandler

from bot.config import BotSettings
from bot.main import (
    build_application,
    confess,
    error_handler,
    forward,
    handle_confession_message,
    help_command,
    start,
)


def test_build_application_registers_command_handlers(
    bot_settings: BotSettings,
) -> None:
    # Arrange — bot_settings comes from conftest fixture

    # Act
    application = build_application(bot_settings)

    # Assert
    command_handlers = [
        h for h in application.handlers[0] if isinstance(h, CommandHandler)
    ]
    registered_callbacks = {h.callback for h in command_handlers}
    assert registered_callbacks == {start, help_command, confess, forward}


def test_build_application_registers_message_and_error_handlers(
    bot_settings: BotSettings,
) -> None:
    # Arrange — bot_settings comes from conftest fixture

    # Act
    application = build_application(bot_settings)

    # Assert
    message_handlers = [
        h for h in application.handlers[0] if isinstance(h, MessageHandler)
    ]
    assert message_handlers[0].callback is handle_confession_message
    assert error_handler in application.error_handlers
