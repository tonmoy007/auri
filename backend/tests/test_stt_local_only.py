"""Tests for the speech-to-text ``local_only`` option.

The Guide treats a person's question as text that must never reach a hosted provider.
The shared speech route can fall back to OpenAI when local transcription fails; a
spoken Guide question asks for ``local_only`` so that fallback is switched off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from app.api.v1.stt import _last_transcription_at, get_clock
from app.main import app
from app.services.stt import WhisperTranscriber
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
AUDIO = {"audio": ("q.m4a", b"RIFF....WAVEfmt fake", "audio/m4a")}
HEADERS = {"X-Device-Token-Hash": "a" * 32}


class _EmptyModel:
    """A local model that hears nothing."""

    def transcribe(self, path: str) -> tuple[list[SimpleNamespace], None]:
        return [], None


class _FailingModel:
    def transcribe(self, path: str) -> tuple[list[SimpleNamespace], None]:
        raise RuntimeError("model crashed")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    _last_transcription_at.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
    _last_transcription_at.clear()


@pytest.mark.parametrize(
    "model", [_EmptyModel(), _FailingModel()], ids=["empty", "crash"]
)
def test_with_the_fallback_off_a_local_failure_never_reaches_a_hosted_provider(
    model: object, tmp_path, set_setting, network_violations: list[str]
) -> None:
    # Arrange — a key IS configured, so a fallback attempt would try to connect; the
    # suite's network guard records any connection attempt
    set_setting("OPENAI_API_KEY", "sk-test-not-real")
    audio = tmp_path / "q.m4a"
    audio.write_bytes(b"x")
    transcriber = WhisperTranscriber(allow_api_fallback=False)
    transcriber._model = model  # type: ignore[assignment]

    # Act
    text = transcriber.transcribe(audio)

    # Assert
    assert text == ""
    assert network_violations == []


def test_by_default_the_fallback_is_still_tried(tmp_path) -> None:
    # Arrange — the confession flow keeps its existing behaviour
    audio = tmp_path / "q.m4a"
    audio.write_bytes(b"x")
    transcriber = WhisperTranscriber()
    transcriber._model = _EmptyModel()  # type: ignore[assignment]

    # Act
    with patch.object(
        WhisperTranscriber, "_api_fallback", return_value="from api"
    ) as fallback:
        text = transcriber.transcribe(audio)

    # Assert
    assert text == "from api"
    fallback.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "allow"),
    [("", True), ("?local_only=true", False), ("?local_only=false", True)],
)
async def test_the_route_passes_the_option_to_the_transcriber(
    client: AsyncClient, query: str, allow: bool
) -> None:
    # Arrange
    fake = MagicMock()
    fake.return_value.transcribe.return_value = "hello there"

    # Act
    with patch("app.api.v1.stt.WhisperTranscriber", fake):
        response = await client.post(
            f"/api/v1/stt{query}", files=AUDIO, headers=HEADERS
        )

    # Assert
    assert response.status_code == 200
    fake.assert_called_once_with(allow_api_fallback=allow)
