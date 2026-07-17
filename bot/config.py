"""Telegram bot configuration via pydantic-settings."""

from __future__ import annotations

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Configuration for the Telegram confession bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str = Field(..., description="Telegram Bot API token")
    webhook_url: str = Field(
        ..., description="Public HTTPS URL for Telegram webhook"
    )
    webhook_secret: str | None = Field(
        None,
        description="Secret token used to validate incoming webhook requests "
        "(required when environment=production)",
    )

    # Server
    host: str = Field("0.0.0.0", description="Bind address for the webhook server")
    port: int = Field(8080, description="Port for the webhook server")

    # Backend API
    backend_url: HttpUrl = Field(
        ..., description="Auri backend API base URL"
    )

    # Web
    web_url: str = Field(
        "https://auri.app", description="Public URL of the Auri confession booth"
    )

    # Runtime
    environment: str = Field(
        "development", description="Runtime environment (development/staging/production)"
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.environment.lower() == "development"

    @model_validator(mode="after")
    def _require_webhook_secret_in_production(self) -> "BotSettings":
        if self.is_production and not self.webhook_secret:
            raise ValueError(
                "webhook_secret (env WEBHOOK_SECRET) is required when environment=production"
            )
        return self
