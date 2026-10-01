"""Tests must not read a developer's ``.env`` (AGENTS.md 16.3, plan task 15.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings, settings


def test_the_test_suite_settings_read_no_env_file() -> None:
    # Arrange
    configured_file = Settings.model_config.get("env_file")

    # Act
    instance_file = settings.model_config.get("env_file")

    # Assert
    assert configured_file is None
    assert instance_file is None


def test_a_stray_env_file_in_the_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    (tmp_path / ".env").write_text("SESSION_TOKEN_SECRET=from-a-developer-env-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SESSION_TOKEN_SECRET", raising=False)

    # Act
    loaded = Settings()

    # Assert
    assert loaded.SESSION_TOKEN_SECRET != "from-a-developer-env-file"
