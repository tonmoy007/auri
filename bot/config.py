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
        populate_by_name=True,
    )

    # Telegram
    bot_token: str = Field(..., description="Telegram Bot API token")
    webhook_url: str = Field(..., description="Public HTTPS URL for Telegram webhook")
    webhook_secret: str | None = Field(
        None,
        description="Secret token used to validate incoming webhook requests "
        "(required when environment=production)",
    )

    # Server
    host: str = Field("0.0.0.0", description="Bind address for the webhook server")
    port: int = Field(8080, description="Port for the webhook server")

    # Backend API
    backend_url: HttpUrl = Field(..., description="Auri backend API base URL")

    # Moderation queue
    moderator_chat_id: str | None = Field(
        None,
        validation_alias="MODERATOR_TELEGRAM_CHAT_ID",
        description="Telegram chat ID flagged confessions are relayed to for "
        "review. Moderation polling is disabled entirely when unset. Shares "
        "the backend's MODERATOR_TELEGRAM_CHAT_ID env var name so one .env "
        "value configures both services.",
    )
    moderation_api_key: str | None = Field(
        None,
        description="Shared secret for calling the backend's /api/v1/moderation/* "
        "endpoints (must match the backend's MODERATION_API_KEY).",
    )
    moderation_poll_seconds: int = Field(
        30, description="How often to poll the backend moderation queue"
    )

    # Delivery queue
    delivery_api_key: str | None = Field(
        None,
        description="Shared secret for calling the backend's /api/v1/delivery/* "
        "endpoints (must match the backend's DELIVERY_API_KEY). Delivery "
        "polling is disabled entirely when unset.",
    )
    delivery_poll_seconds: int = Field(
        30, description="How often to poll the backend delivery queue"
    )
    department_chat_ids: str = Field(
        "",
        description="Comma-separated department:chat_id pairs routing forwarded "
        "confessions to each department's Telegram chat, e.g. "
        "'HR:111,Engineering:222'. A department missing from this map is "
        "logged and left undelivered (retried every poll) rather than dropped.",
    )

    # Web
    web_url: str = Field(
        "https://auri.app", description="Public URL of the Auri confession booth"
    )

    # Runtime
    environment: str = Field(
        "development",
        description="Runtime environment (development/staging/production)",
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.environment.lower() == "development"

    @property
    def moderation_enabled(self) -> bool:
        return bool(self.moderator_chat_id and self.moderation_api_key)

    @property
    def delivery_enabled(self) -> bool:
        return bool(self.delivery_api_key)

    def department_chat_id_map(self) -> dict[str, str]:
        """Parse ``department_chat_ids`` into a ``{department: chat_id}`` dict.

        Malformed entries (missing ``:``, empty department/chat_id) are
        skipped rather than raising — a typo in one pair must not take down
        delivery for every other correctly-configured department.
        """
        result: dict[str, str] = {}
        for pair in self.department_chat_ids.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            department, _, chat_id = pair.partition(":")
            department = department.strip()
            chat_id = chat_id.strip()
            if department and chat_id:
                result[department] = chat_id
        return result

    @model_validator(mode="after")
    def _require_webhook_secret_in_production(self) -> "BotSettings":
        if self.is_production and not self.webhook_secret:
            raise ValueError(
                "webhook_secret (env WEBHOOK_SECRET) is required when environment=production"
            )
        return self

    @model_validator(mode="after")
    def _require_both_moderation_settings_together(self) -> "BotSettings":
        if bool(self.moderator_chat_id) != bool(self.moderation_api_key):
            raise ValueError(
                "moderator_chat_id and moderation_api_key must be set together "
                "(both or neither) — a lone value is almost certainly a "
                "misconfiguration that would leave moderation half-enabled"
            )
        return self
