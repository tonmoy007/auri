"""Tests for app.config.Settings and the DATABASE_URL resolution in
app.database — DATABASE_URL override precedence and CORS_ORIGINS parsing.
"""

from __future__ import annotations

import importlib

from app.config import Settings
from app.main import create_app


def test_settings_database_url_override_wins_over_db_parts() -> None:
    # Arrange
    explicit_url = "postgresql+asyncpg://ci_user:ci_pass@ci-host:5432/ci_db"

    # Act
    settings = Settings(
        DATABASE_URL=explicit_url,
        DB_HOST="ignored-host",
        DB_USER="ignored-user",
        DB_PASSWORD="ignored-pass",
        DB_NAME="ignored-db",
    )
    resolved = settings.DATABASE_URL or (
        f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )

    # Assert
    assert resolved == explicit_url


def test_database_module_uses_explicit_database_url_when_set(
    monkeypatch,
) -> None:
    # Arrange
    explicit_url = "postgresql+asyncpg://ci_user:ci_pass@ci-host:5432/ci_db"
    monkeypatch.setenv("DATABASE_URL", explicit_url)

    # Act
    import app.config as config_module
    import app.database as database_module

    importlib.reload(config_module)
    importlib.reload(database_module)
    resolved = database_module.DATABASE_URL

    # Assert
    assert resolved == explicit_url

    # Cleanup — restore modules to their non-overridden state.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(config_module)
    importlib.reload(database_module)


def test_database_module_builds_url_from_parts_when_unset(monkeypatch) -> None:
    # Arrange
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "parts-host")
    monkeypatch.setenv("DB_PORT", "6543")
    monkeypatch.setenv("DB_USER", "parts-user")
    monkeypatch.setenv("DB_PASSWORD", "parts-pass")
    monkeypatch.setenv("DB_NAME", "parts-db")

    # Act
    import app.config as config_module
    import app.database as database_module

    importlib.reload(config_module)
    importlib.reload(database_module)
    resolved = database_module.DATABASE_URL

    # Assert
    assert (
        resolved
        == "postgresql+asyncpg://parts-user:parts-pass@parts-host:6543/parts-db"
    )

    # Cleanup — restore modules to their non-overridden state.
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.delenv("DB_PORT", raising=False)
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    importlib.reload(config_module)
    importlib.reload(database_module)


def test_cors_origins_are_split_and_stripped_into_middleware_config(
    monkeypatch,
) -> None:
    # Arrange
    monkeypatch.setenv("CORS_ORIGINS", "https://auri.app, https://staging.auri.app ,, ")
    import app.config as config_module

    importlib.reload(config_module)
    monkeypatch.setattr("app.main.settings", config_module.settings)

    # Act
    app = create_app()
    cors_middleware = next(
        m
        for m in app.user_middleware
        if getattr(m.cls, "__name__", "") == "CORSMiddleware"
    )

    # Assert
    assert cors_middleware.kwargs["allow_origins"] == [
        "https://auri.app",
        "https://staging.auri.app",
    ]

    # Cleanup — restore module to its non-overridden state.
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    importlib.reload(config_module)
