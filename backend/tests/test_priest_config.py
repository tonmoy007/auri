"""Tests for the typed priest-mode settings and the dashboard allowlist.

The flag is a kill switch, so anything that is not clearly "on" means off, and every
other value falls back to a safe default instead of raising in the request path.
"""

from __future__ import annotations

import pytest
from app.api.v1.admin import ALLOWED_CONFIG_KEYS
from app.models.user import UserRole
from app.priest import priest_config
from app.services import settings_service
from httpx import AsyncClient

from tests.conftest import SettingPatcher, StaffFactory

RLO = chr(0x202E)  # right-to-left override, a control character


def _override(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setitem(settings_service._cache, key, value)


def test_priest_mode_is_off_by_default(set_setting: SettingPatcher) -> None:
    # Arrange
    set_setting("PRIEST_MODE_ENABLED", False)

    # Act / Assert
    assert priest_config.enabled() is False


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on", " True "])
def test_clearly_on_values_turn_it_on(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _override(monkeypatch, "PRIEST_MODE_ENABLED", raw)

    # Act / Assert
    assert priest_config.enabled() is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", "maybe", "tru", "2"])
def test_anything_else_keeps_it_off(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — an unclear value must never enable a feature that reads religion
    _override(monkeypatch, "PRIEST_MODE_ENABLED", raw)

    # Act / Assert
    assert priest_config.enabled() is False


@pytest.mark.parametrize(
    ("key", "accessor", "raw", "expected"),
    [
        ("PRIEST_TOP_K", "top_k", "8", 8),
        ("PRIEST_TOP_K", "top_k", "not a number", 6),
        ("PRIEST_TOP_K", "top_k", "0", 6),
        ("PRIEST_TOP_K", "top_k", "99", 6),
        ("PRIEST_LLM_TIMEOUT_SECONDS", "llm_timeout_seconds", "25", 25),
        ("PRIEST_LLM_TIMEOUT_SECONDS", "llm_timeout_seconds", "-3", 20),
        ("PRIEST_TOTAL_DEADLINE_SECONDS", "total_deadline_seconds", "45", 45),
        ("PRIEST_TOTAL_DEADLINE_SECONDS", "total_deadline_seconds", "x", 30),
        ("PRIEST_RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute", "9", 9),
        ("PRIEST_RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute", "0", 4),
        ("PRIEST_RATE_LIMIT_PER_DAY", "rate_limit_per_day", "100", 100),
        ("PRIEST_MAX_CONCURRENCY", "max_concurrency", "2", 2),
        ("PRIEST_MAX_CONCURRENCY", "max_concurrency", "500", 4),
        ("PRIEST_MIN_RELEVANCE_DENSE", "min_relevance_dense", "0.62", 0.62),
        ("PRIEST_MIN_RELEVANCE_DENSE", "min_relevance_dense", "nan", 0.5),
        ("PRIEST_MIN_RELEVANCE_DENSE", "min_relevance_dense", "1.5", 0.5),
        ("PRIEST_MIN_RELEVANCE_BM25", "min_relevance_bm25", "4.25", 4.25),
        ("PRIEST_MIN_RELEVANCE_BM25", "min_relevance_bm25", "-1", 3.0),
    ],
)
def test_numbers_are_parsed_and_fall_back_when_unusable(
    key: str,
    accessor: str,
    raw: str,
    expected: float,
    monkeypatch: pytest.MonkeyPatch,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _override(monkeypatch, key, raw)

    # Act
    value = getattr(priest_config, accessor)()

    # Assert
    assert value == expected


def test_the_persona_name_is_cleaned_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _override(monkeypatch, "PRIEST_PERSONA_NAME", f"  Reflect{RLO} with\nthe library  ")

    # Act
    name = priest_config.persona_name()

    # Assert
    assert name == "Reflect with the library"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 200, RLO])
def test_an_unusable_persona_name_falls_back_to_guide(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _override(monkeypatch, "PRIEST_PERSONA_NAME", raw)

    # Act / Assert
    assert priest_config.persona_name() == "Guide"


def test_enabled_traditions_default_to_all(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _override(monkeypatch, "PRIEST_TRADITIONS_ENABLED", "")

    # Act / Assert
    assert priest_config.enabled_traditions() is None


def test_enabled_traditions_keep_only_known_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _override(
        monkeypatch, "PRIEST_TRADITIONS_ENABLED", '["islam", "buddhism", "nonsense"]'
    )

    # Act
    enabled = priest_config.enabled_traditions()

    # Assert
    assert enabled == frozenset({"islam", "buddhism"})


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', "[1, 2]", '["nonsense"]'])
def test_a_malformed_tradition_list_means_all(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _override(monkeypatch, "PRIEST_TRADITIONS_ENABLED", raw)

    # Act / Assert
    assert priest_config.enabled_traditions() is None


def test_the_fallback_chat_address_is_derived_from_the_ollama_address(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("PRIEST_FALLBACK_BASE_URL", "")
    set_setting("OLLAMA_BASE_URL", "http://localhost:11434/")

    # Act / Assert
    assert priest_config.fallback_base_url() == "http://localhost:11434/v1"


def test_an_explicit_fallback_address_wins(set_setting: SettingPatcher) -> None:
    # Arrange
    set_setting("PRIEST_FALLBACK_BASE_URL", "http://ollama:11434/v1/")

    # Act / Assert
    assert priest_config.fallback_base_url() == "http://ollama:11434/v1"


# ── the dashboard allowlist ──────────────────────────────────────────────


def test_addresses_keys_and_paths_cannot_be_edited_from_the_dashboard() -> None:
    # Assert — a dashboard edit must not be able to redirect people's questions
    for key in (
        "PRIEST_LLM_BASE_URL",
        "PRIEST_LLM_API_KEY",
        "PRIEST_FALLBACK_BASE_URL",
        "PRIEST_VAULT_DIR",
        "PRIEST_INDEX_DIR",
        "PRIEST_LLM_ALLOW_INSECURE_HTTP",
    ):
        assert key not in ALLOWED_CONFIG_KEYS


def test_the_kill_switch_and_tuning_keys_can_be_edited() -> None:
    # Assert
    for key in (
        "PRIEST_MODE_ENABLED",
        "PRIEST_LLM_MODEL",
        "PRIEST_TOP_K",
        "CRISIS_HELPLINE_NUMBER",
    ):
        assert key in ALLOWED_CONFIG_KEYS


@pytest.mark.asyncio
async def test_an_env_only_key_is_refused_by_the_config_endpoint(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await api_client.put(
        "/api/v1/admin/config",
        json={"key": "PRIEST_LLM_BASE_URL", "value": "http://attacker.example"},
        headers=headers,
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_config_listing_groups_priest_and_crisis_keys(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    body = (await api_client.get("/api/v1/admin/config", headers=headers)).json()

    # Assert
    assert "PRIEST_MODE_ENABLED" in {e["key"] for e in body["priest"]}
    assert "CRISIS_HELPLINE_NUMBER" in {e["key"] for e in body["crisis"]}
    assert "PRIEST_LLM_API_KEY" not in str(body)
