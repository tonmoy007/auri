"""Tests that theme grouping uses a configured OpenAI-compatible endpoint.

The HTTP boundary (``chat_complete``) is replaced; endpoint resolution, prompt
fencing, parsing, suppression and the category fallback run for real
(AGENTS.md §16.4).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from app.models.user import UserRole
from app.services.themes_endpoint import ThemesEndpoint
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SettingPatcher, StaffFactory
from tests.theme_seeding import (
    FakeModel,
    add_confessions,
    get_themes,
    pinned_clock_and_cohort,  # noqa: F401 - autouse fixture
)


class RemoteRecorder:
    """Stands in for ``chat_complete``: records the call and returns a reply."""

    def __init__(self, reply: str = "") -> None:
        self.calls: list[tuple[ThemesEndpoint, str]] = []
        self.reply = reply

    async def __call__(self, endpoint: ThemesEndpoint, prompt: str) -> str:
        self.calls.append((endpoint, prompt))
        return self.reply

    def patch(self):
        return patch("app.services.theme_service.chat_complete", new=self)


def _configure(
    set_setting: SettingPatcher, url: str = "https://models.example.net"
) -> None:
    set_setting("THEMES_LLM_BASE_URL", url)
    set_setting("THEMES_LLM_MODEL", "qwen3.5-9b")
    set_setting("THEMES_LLM_API_KEY", "sk-themes-key")
    set_setting("THEMES_LLM_ALLOW_INSECURE_HTTP", False)


def _grouping_reply(*item_numbers: int) -> str:
    return json.dumps(
        {"themes": [{"label": "Pay concerns", "items": list(item_numbers)}]}
    )


@pytest.mark.asyncio
async def test_a_configured_endpoint_is_used_instead_of_the_local_model(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    remote = RemoteRecorder(_grouping_reply(1, 2, 3, 4))
    local = FakeModel()

    # Act
    with remote.patch(), local.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert local.calls == []
    assert len(remote.calls) == 1
    assert remote.calls[0][0].host == "models.example.net"
    assert remote.calls[0][0].model == "qwen3.5-9b"
    assert [(t["label"], t["confessions"]) for t in body["themes"]] == [
        ("Pay concerns", 4)
    ]
    assert body["method"] == "model"


@pytest.mark.asyncio
async def test_the_remote_prompt_is_fenced_and_holds_summaries_only(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    remote = RemoteRecorder(_grouping_reply(1, 2, 3))

    # Act
    with remote.patch():
        await get_themes(api_client, headers)

    # Assert
    prompt = remote.calls[0][1]
    assert "<<<BEGIN_USER_CONTENT>>>" in prompt
    assert "<<<END_USER_CONTENT>>>" in prompt
    assert "concern about pay number 0" in prompt
    assert "PRIVATE-TRANSCRIPT" not in prompt
    assert "sk-themes-key" not in prompt


@pytest.mark.asyncio
async def test_an_empty_remote_reply_falls_back_to_categories(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", category="compensation")
    remote = RemoteRecorder("")

    # Act
    with remote.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert — an empty reply means the server is down; no retry
    assert len(remote.calls) == 1
    assert body["method"] == "category"
    assert [t["label"] for t in body["themes"]] == ["compensation"]


@pytest.mark.asyncio
async def test_a_refused_endpoint_sends_nothing_and_says_why(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange — plain http to a public address, no opt-in
    _configure(set_setting, url="http://118.67.212.45:8000")
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", category="compensation")
    remote = RemoteRecorder(_grouping_reply(1, 2, 3, 4))
    local = FakeModel()

    # Act
    with remote.patch(), local.patch():
        response = await get_themes(api_client, headers)

    # Assert
    body = response.json()
    assert remote.calls == []
    assert local.calls == []
    assert body["method"] == "category"
    assert "unencrypted" in body["notice"]
    assert "118.67.212.45" not in response.text
    assert "sk-themes-key" not in response.text


@pytest.mark.asyncio
async def test_the_opt_in_lets_plain_http_to_a_public_address_through(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting, url="http://118.67.212.45:8000")
    set_setting("THEMES_LLM_ALLOW_INSECURE_HTTP", True)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    remote = RemoteRecorder(_grouping_reply(1, 2, 3))

    # Act
    with remote.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert len(remote.calls) == 1
    assert body["method"] == "model"


@pytest.mark.asyncio
async def test_an_unparseable_remote_reply_is_retried_once(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    remote = RemoteRecorder("not json at all")

    # Act
    with remote.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert len(remote.calls) == 2
    assert body["method"] == "category"


@pytest.mark.asyncio
async def test_too_few_confessions_never_reach_the_remote_model(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 2, "pay")
    remote = RemoteRecorder(_grouping_reply(1, 2))

    # Act
    with remote.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert remote.calls == []
    assert body["method"] == "none"


@pytest.mark.asyncio
async def test_a_refused_endpoint_logs_neither_the_address_nor_the_key(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
    caplog,
) -> None:
    # Arrange
    _configure(set_setting, url="http://118.67.212.45:8000")
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", category="compensation")

    # Act
    with caplog.at_level("WARNING"):
        await get_themes(api_client, headers)

    # Assert
    assert "themes endpoint refused" in caplog.text
    assert "118.67.212.45" not in caplog.text
    assert "sk-themes-key" not in caplog.text
