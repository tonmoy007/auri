"""Unit tests for app.agent's LiveKit credential resolution (task 10.7).

Only the DB boundary (async_session_factory) is mocked, per AGENTS.md
§16.4 — settings_service's cache/fallback logic runs for real.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from app.agent import _resolve_livekit_credentials
from app.config import settings
from app.services import settings_service


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    # Arrange / Cleanup — settings_service._cache is a module-level global;
    # tests must not leak DB-override state into each other (AGENTS.md §16.3).
    settings_service._cache.clear()
    yield
    settings_service._cache.clear()


@pytest.mark.asyncio
async def test_resolve_falls_back_to_settings_defaults_when_db_unavailable() -> None:
    # Arrange
    @asynccontextmanager
    async def _broken_session_factory():
        raise ConnectionError("could not connect to server")
        yield  # pragma: no cover — makes this an async generator/context manager

    # Act
    with patch("app.agent.async_session_factory", _broken_session_factory):
        ws_url, api_key, api_secret = await _resolve_livekit_credentials()

    # Assert
    assert ws_url == settings.LIVEKIT_URL
    assert api_key == settings.LIVEKIT_API_KEY
    assert api_secret == settings.LIVEKIT_API_SECRET


@pytest.mark.asyncio
async def test_resolve_prefers_db_override_over_settings_default() -> None:
    # Arrange
    @asynccontextmanager
    async def _noop_session_factory():
        yield None

    async def _fake_load_cache(_session: object) -> None:
        settings_service._cache["LIVEKIT_URL"] = "ws://livekit-override:7880"

    # Act
    with (
        patch("app.agent.async_session_factory", _noop_session_factory),
        patch("app.agent.load_cache", _fake_load_cache),
    ):
        ws_url, api_key, api_secret = await _resolve_livekit_credentials()

    # Assert
    assert ws_url == "ws://livekit-override:7880"
    assert api_key == settings.LIVEKIT_API_KEY
    assert api_secret == settings.LIVEKIT_API_SECRET
