"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic import Field, model_validator
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
    TTS_RATE_LIMIT_SECONDS: int = 10  # cost-abuse guard on POST /api/v1/tts
    STT_RATE_LIMIT_SECONDS: int = 10  # cost-abuse guard on POST /api/v1/stt
    VOICE_MASK_RATE_LIMIT_SECONDS: int = (
        10  # cost-abuse guard on POST /api/v1/voice/mask
    )

    # ── HR analytics (Phase 11) ──────────────────────────────────────────
    # Smallest bucket size an aggregate may report. Anything smaller is
    # suppressed, because a 2-person chart identifies those 2 people.
    ANALYTICS_MIN_COHORT: int = 5

    # ── Data retention ────────────────────────────────────────────────────
    RETENTION_HOURS: int = 24  # purge forwarded/deleted confessions after this long
    # How long a reply is kept after it was written (>= 1: zero would delete every
    # reply the moment it is emptied).
    REPLY_RETENTION_DAYS: int = Field(default=30, ge=1)

    # ── Speech-to-Text ────────────────────────────────────────────────────
    WHISPER_MODEL: str = "base"  # tiny / base / small / medium / large-v3
    STT_MAX_UPLOAD_BYTES: int = (
        25 * 1024 * 1024
    )  # 25MB, matches OpenAI Whisper API's cap

    # ── LLM ──────────────────────────────────────────────────────────────
    # Provider priority for LLMService(provider="auto"): Ollama, then Gemini,
    # then OpenAI — first provider to return a non-empty response wins.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Claude is explicit-provider-only (LLMService(provider="claude")) — not
    # part of the "auto" chain above.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-latest"

    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    MODERATOR_TELEGRAM_CHAT_ID: str = ""  # flagged confessions relay here for review
    MODERATION_API_KEY: str = ""  # shared secret the bot uses to call /moderation/*
    DELIVERY_API_KEY: str = ""  # shared secret the bot uses to call /delivery/*
    METRICS_API_KEY: str = ""  # bearer token a Prometheus scrape job sends to /metrics

    # ── Admin dashboard (Phase 10, local dev) ───────────────────────────────
    ADMIN_API_KEY: str = (
        ""  # shared secret for the config/build dashboard's /admin/* routes
    )

    # ── Staff accounts (Phase 11) ───────────────────────────────────────────
    # First-admin bootstrap, read once at startup and only applied when the
    # users table is empty. Never hardcode values here (AGENTS.md §12);
    # leaving either blank disables bootstrap entirely.
    ADMIN_BOOTSTRAP_EMAIL: str = ""
    ADMIN_BOOTSTRAP_PASSWORD: str = ""

    # Session tokens for dashboard logins. SESSION_TOKEN_SECRET falls back to
    # SECRET_KEY when empty; signing refuses to run outside development while
    # that value is still the shipped placeholder.
    SESSION_TOKEN_SECRET: str = ""
    ACCESS_TOKEN_TTL_MINUTES: int = 30
    REFRESH_TOKEN_TTL_HOURS: int = 12
    LOGIN_MAX_ATTEMPTS: int = 5  # per email, before the window locks out
    LOGIN_ATTEMPT_WINDOW_SECONDS: int = 300

    # ── LiveKit (Phase 7) ────────────────────────────────────────────────────
    # Self-hosted only — see docker-compose.yml's `livekit` service (--dev
    # mode). Defaults match that service's devkey/secret; LAN IP (not
    # localhost) is required for a real device to connect.
    LIVEKIT_URL: str = "ws://localhost:7880"
    LIVEKIT_API_KEY: str = "devkey"
    LIVEKIT_API_SECRET: str = "secret"

    # ── Recipient directory ──────────────────────────────────────────────
    DEPARTMENTS: str = "HR,Engineering,Management"  # comma-separated, admin-managed

    # ── Environment / Logging ─────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str = ""  # empty disables Sentry entirely

    @model_validator(mode="after")
    def _reply_outlives_confession(self) -> Settings:
        """Refuse a reply retention shorter than the confession's own.

        Otherwise a reply would be deleted before the row is ever emptied, and
        the reply-only shell would never exist.
        """
        if self.REPLY_RETENTION_DAYS * 24 < self.RETENTION_HOURS:
            raise ValueError(
                "REPLY_RETENTION_DAYS must cover at least RETENTION_HOURS "
                f"(got {self.REPLY_RETENTION_DAYS} days vs {self.RETENTION_HOURS} hours)"
            )
        return self


def parse_comma_separated_list(raw: str) -> list[str]:
    """Split a comma-separated settings value into a trimmed, non-empty list.

    Shared by ``CORS_ORIGINS`` and ``DEPARTMENTS`` parsing so both follow
    the same whitespace/empty-entry handling.
    """
    return [item.strip() for item in raw.split(",") if item.strip()]


settings = Settings()  # Singleton – import this everywhere.
