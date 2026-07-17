"""Tests for BotSettings validation rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bot.config import BotSettings


def test_production_without_webhook_secret_raises() -> None:
    # Arrange
    kwargs = {
        "_env_file": None,
        "bot_token": "t",
        "webhook_url": "https://bot.test/webhook",
        "backend_url": "https://backend.test",
        "environment": "production",
    }

    # Act
    with pytest.raises(ValidationError) as exc_info:
        BotSettings(**kwargs)

    # Assert
    assert "webhook_secret" in str(exc_info.value)


def test_production_with_webhook_secret_passes() -> None:
    # Arrange
    kwargs = {
        "_env_file": None,
        "bot_token": "t",
        "webhook_url": "https://bot.test/webhook",
        "webhook_secret": "s3cr3t",
        "backend_url": "https://backend.test",
        "environment": "production",
    }

    # Act
    settings = BotSettings(**kwargs)

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
