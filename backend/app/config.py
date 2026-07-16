"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pydantic ``BaseSettings`` model for all Auri backend configuration.

    Values are read from environment variables first, falling back to a
    ``.env`` file located in the project root (one level above ``app/``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "auri"
    DB_USER: str = "auri"
    DB_PASS: str = ""

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"

    # ── Speech-to-Text ────────────────────────────────────────────────────
    WHISPER_MODEL: str = "base"  # tiny / base / small / medium / large-v3

    # ── LLM ──────────────────────────────────────────────────────────────
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"

    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""

    # ── Environment / Logging ─────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"


settings = Settings()  # Singleton – import this everywhere.
