"""Tests for bot/main.py command and message handlers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from bot.config import BotSettings
from bot.main import (
    confess,
    error_handler,
    forward,
    handle_confession_message,
    help_command,
    start,
)


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


# Phrases that promise more than the system gives: a device code is stored, pending
# and flagged confessions are kept until someone acts, staff read what is held or
# forwarded, and the words themselves can point to a person.
OVERCLAIMS = (
    "never stored",
    "stays between",
    "stays anonymous",
    "remains anonymous",
    "without knowing who sent",
    "delete forever",
    "limited time",
    "safely delivered",
    "has been delivered",
    "strips any identifying",
    "forward anonymously here",
    "arrive here automatically",
    "confirm receipt",
    "send to a specific person",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name", ["start", "help_command", "confess", "forward"]
)
async def test_no_command_reply_promises_more_anonymity_than_the_system_gives(
    handler_name: str, mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange
    handler = {
        "start": start,
        "help_command": help_command,
        "confess": confess,
        "forward": forward,
    }[handler_name]

    # Act
    await handler(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0].lower()
    assert [phrase for phrase in OVERCLAIMS if phrase in reply_text] == []


@pytest.mark.asyncio
async def test_start_tells_people_that_handlers_can_read_what_is_forwarded(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Act
    await start(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0].lower()
    assert "can read" in reply_text
    assert "point to you" in reply_text


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
async def test_forward_says_nothing_was_sent_from_the_chat(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — this command does not forward anything, so it must not say it did

    # Act
    await forward(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "Nothing was sent from this chat" in reply_text
    assert "/start" in reply_text


@pytest.mark.asyncio
async def test_handle_confession_message_does_not_claim_a_delivery(
    mock_update: MagicMock, mock_context: MagicMock
) -> None:
    # Arrange — a plain message in a chat forwards nothing, so nothing is confirmed

    # Act
    await handle_confession_message(mock_update, mock_context)

    # Assert
    reply_text = mock_update.effective_message.reply_text.call_args.args[0]
    assert "nothing was forwarded" in reply_text
    assert [p for p in OVERCLAIMS if p in reply_text.lower()] == []


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


@pytest.mark.asyncio
async def test_error_handler_never_logs_raw_update_content(
    mock_context: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange — regression (2026-07-20 privacy review): Update.__str__
    # includes full message text; logging the raw object would leak
    # confession content to application logs on any processing failure.
    update = MagicMock()
    update.update_id = 42
    update.__str__ = MagicMock(  # type: ignore[method-assign]
        return_value="Update(message=Message(text='sensitive confession text'))"
    )
    mock_context.error = RuntimeError("boom")
    caplog.set_level(logging.ERROR, logger="bot.main")

    # Act
    await error_handler(update, mock_context)

    # Assert
    assert "sensitive confession text" not in caplog.text
    assert "update_id=42" in caplog.text
    assert "boom" in caplog.text
