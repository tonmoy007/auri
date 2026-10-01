"""The bot's tests must not read a developer's ``.env`` either (plan task 15.5)."""

from __future__ import annotations

from bot.config import BotSettings


def test_the_bot_settings_read_no_env_file_under_test() -> None:
    # Arrange
    expected: None = None

    # Act
    configured_file = BotSettings.model_config.get("env_file")

    # Assert
    assert configured_file is expected
