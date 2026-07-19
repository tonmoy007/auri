"""Tests for BotSettings validation rules."""

from __future__ import annotations

import pytest
from pydantic import HttpUrl, ValidationError

from bot.config import BotSettings


def test_production_without_webhook_secret_raises() -> None:
    # Arrange
    environment = "production"

    # Act
    with pytest.raises(ValidationError) as exc_info:
        BotSettings(
            _env_file=None,
            bot_token="t",
            webhook_url="https://bot.test/webhook",
            backend_url=HttpUrl("https://backend.test"),
            environment=environment,
        )

    # Assert
    assert "webhook_secret" in str(exc_info.value)


def test_production_with_webhook_secret_passes() -> None:
    # Arrange
    environment = "production"

    # Act
    settings = BotSettings(
        _env_file=None,
        bot_token="t",
        webhook_url="https://bot.test/webhook",
        webhook_secret="s3cr3t",
        backend_url=HttpUrl("https://backend.test"),
        environment=environment,
    )

    # Assert
    assert settings.webhook_secret == "s3cr3t"
    assert settings.is_production is True


def test_web_url_defaults_to_auri_app(bot_settings: BotSettings) -> None:
    # Arrange
    settings = bot_settings

    # Act
    web_url = settings.web_url

    # Assert
    assert web_url == "https://auri.app"


def test_moderator_chat_id_without_api_key_raises() -> None:
    # Act
    with pytest.raises(ValidationError) as exc_info:
        BotSettings(
            _env_file=None,
            bot_token="t",
            webhook_url="https://bot.test/webhook",
            webhook_secret="s",
            backend_url=HttpUrl("https://backend.test"),
            moderator_chat_id="999",
        )

    # Assert
    assert "moderator_chat_id and moderation_api_key" in str(exc_info.value)


def test_moderation_api_key_without_chat_id_raises() -> None:
    # Act
    with pytest.raises(ValidationError) as exc_info:
        BotSettings(
            _env_file=None,
            bot_token="t",
            webhook_url="https://bot.test/webhook",
            webhook_secret="s",
            backend_url=HttpUrl("https://backend.test"),
            moderation_api_key="key",
        )

    # Assert
    assert "moderator_chat_id and moderation_api_key" in str(exc_info.value)


def test_moderation_enabled_true_when_both_configured() -> None:
    # Act
    settings = BotSettings(
        _env_file=None,
        bot_token="t",
        webhook_url="https://bot.test/webhook",
        webhook_secret="s",
        backend_url=HttpUrl("https://backend.test"),
        moderator_chat_id="999",
        moderation_api_key="key",
    )

    # Assert
    assert settings.moderation_enabled is True


def test_moderation_enabled_false_by_default(bot_settings: BotSettings) -> None:
    # Assert
    assert bot_settings.moderation_enabled is False


def test_moderator_chat_id_reads_shared_backend_env_var_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — regression: the bot must read the SAME env var name the
    # backend uses (MODERATOR_TELEGRAM_CHAT_ID), not a bot-only variant,
    # so one .env configures both services.
    monkeypatch.setenv("MODERATOR_TELEGRAM_CHAT_ID", "555")
    monkeypatch.setenv("MODERATION_API_KEY", "key")

    # Act
    settings = BotSettings(
        _env_file=None,
        bot_token="t",
        webhook_url="https://bot.test/webhook",
        webhook_secret="s",
        backend_url=HttpUrl("https://backend.test"),
    )

    # Assert
    assert settings.moderator_chat_id == "555"
