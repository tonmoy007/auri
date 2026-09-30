"""Tests that no test depends on, or touches, a developer's own Guide files."""

from __future__ import annotations

from pathlib import Path

from app.config import settings


def test_the_guide_index_and_vault_are_not_the_developers_own(tmp_path: Path) -> None:
    # Assert — an admin probe of /index or /reindex/status must read an empty folder,
    # never the real index (which can be hundreds of megabytes) or the real vault
    assert str(tmp_path) in settings.PRIEST_INDEX_DIR
    assert str(tmp_path) in settings.PRIEST_VAULT_DIR


def test_the_ollama_address_and_model_are_the_documented_defaults() -> None:
    # Assert — a developer's .env must not change what the suite asserts
    assert settings.OLLAMA_BASE_URL == "http://localhost:11434"
    assert settings.OLLAMA_MODEL == "llama3.2:3b"
