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
    DB_PASSWORD: str = ""
    DATABASE_URL: str = ""  # If set (e.g. by CI), overrides the DB_* parts above.

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8081"

    # ── Rate limiting ─────────────────────────────────────────────────────
    CONFESSION_RATE_LIMIT_SECONDS: int = 300  # 1 confession per 5 min (AGENTS.md §8.5)

    # ── Speech-to-Text ────────────────────────────────────────────────────
    WHISPER_MODEL: str = "base"  # tiny / base / small / medium / large-v3

    # ── LLM ──────────────────────────────────────────────────────────────
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"

    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    MODERATOR_TELEGRAM_CHAT_ID: str = ""  # flagged confessions relay here for review

    # ── Recipient directory ──────────────────────────────────────────────
    DEPARTMENTS: str = "HR,Engineering,Management"  # comma-separated, admin-managed

    # ── Environment / Logging ─────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"


def parse_comma_separated_list(raw: str) -> list[str]:
    """Split a comma-separated settings value into a trimmed, non-empty list.

    Shared by ``CORS_ORIGINS`` and ``DEPARTMENTS`` parsing so both follow
    the same whitespace/empty-entry handling.
    """
    return [item.strip() for item in raw.split(",") if item.strip()]


settings = Settings()  # Singleton – import this everywhere.
