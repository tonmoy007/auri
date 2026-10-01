"""Tests for the Guide's moderation call.

The question reaches this call un-cleaned, so its address comes from the environment
only (a dashboard edit must not move it), it must not be a hosted provider or a
``-cloud`` model, and it may not run longer than the cap the service allows.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.models.confession import ModerationSeverity
from app.priest import moderation
from app.services import settings_service

from tests.conftest import SettingPatcher

QUESTION = "MODERATIONMARKER question about patience"


class _Recorder:
    """Stands in for ``httpx.post``; records the call and replies as scripted."""

    def __init__(self, reply: str = "none") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={"message": {"content": self.reply}},
            request=httpx.Request("POST", url),
        )


@pytest.fixture
def post(monkeypatch: pytest.MonkeyPatch, set_setting: SettingPatcher) -> _Recorder:
    """A recorder in place of the HTTP call, with a private Ollama configured."""
    recorder = _Recorder()
    monkeypatch.setattr(moderation.httpx, "post", recorder)
    set_setting("OLLAMA_BASE_URL", "http://localhost:11434")
    set_setting("OLLAMA_MODEL", "llama3.2:3b")
    return recorder


def test_it_asks_the_environment_ollama_with_the_question_and_returns_the_severity(
    post: _Recorder,
) -> None:
    # Arrange
    post.reply = "crisis"

    # Act
    severity = moderation.moderate(QUESTION, timeout=5.0)

    # Assert
    assert severity is ModerationSeverity.crisis
    assert post.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert post.calls[0]["json"]["model"] == "llama3.2:3b"
    assert QUESTION in post.calls[0]["json"]["messages"][0]["content"]


def test_a_dashboard_edit_of_the_address_or_model_changes_nothing(
    post: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setitem(
        settings_service._cache, "OLLAMA_BASE_URL", "https://collector.example"
    )
    monkeypatch.setitem(settings_service._cache, "OLLAMA_MODEL", "other-cloud")

    # Act
    moderation.moderate(QUESTION, timeout=5.0)

    # Assert
    assert post.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert post.calls[0]["json"]["model"] == "llama3.2:3b"


def test_the_call_never_runs_longer_than_the_cap(post: _Recorder) -> None:
    # Act
    moderation.moderate(QUESTION, timeout=3.0)

    # Assert
    timeout = post.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 3.0
    assert (timeout.connect or 0) <= 3.0


@pytest.mark.parametrize(
    "address",
    ["https://api.openai.com", "https://openrouter.ai/api", "ftp://x", "not a url"],
)
def test_a_hosted_or_malformed_address_is_refused_before_any_call(
    post: _Recorder, set_setting: SettingPatcher, address: str
) -> None:
    # Arrange
    set_setting("OLLAMA_BASE_URL", address)

    # Act
    severity = moderation.moderate(QUESTION, timeout=5.0)

    # Assert — fails closed to policy and sends nothing
    assert severity is ModerationSeverity.policy
    assert post.calls == []


def test_plain_http_to_a_public_host_is_refused(
    post: _Recorder, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("OLLAMA_BASE_URL", "http://203.0.113.10:11434")
    set_setting("PRIEST_LLM_ALLOW_INSECURE_HTTP", False)

    # Act
    severity = moderation.moderate(QUESTION, timeout=5.0)

    # Assert
    assert severity is ModerationSeverity.policy
    assert post.calls == []


def test_a_cloud_hosted_model_is_refused_before_any_call(
    post: _Recorder, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("OLLAMA_MODEL", "gpt-oss:120b-cloud")

    # Act
    severity = moderation.moderate(QUESTION, timeout=5.0)

    # Assert
    assert severity is ModerationSeverity.policy
    assert post.calls == []


def test_an_unparseable_reply_and_a_failed_call_both_count_as_policy(
    post: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    post.reply = "maybe?"

    # Act / Assert
    assert moderation.moderate(QUESTION, timeout=5.0) is ModerationSeverity.policy

    def boom(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(moderation.httpx, "post", boom)
    assert moderation.moderate(QUESTION, timeout=5.0) is ModerationSeverity.policy


def test_the_log_never_carries_the_question(
    post: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    def boom(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError(f"refused while sending {QUESTION}")

    monkeypatch.setattr(moderation.httpx, "post", boom)
    caplog.set_level("DEBUG")

    # Act
    moderation.moderate(QUESTION, timeout=5.0)

    # Assert
    assert "MODERATIONMARKER" not in caplog.text
