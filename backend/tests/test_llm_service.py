"""Unit tests for app.services.llm.LLMService.

Only the provider boundary (_call_openai, _call_ollama, ...) is mocked, per
AGENTS.md §16.4. Domain logic (fallback merging, prompt delimiting, the
auto-provider chain order) runs for real.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from app.config import settings
from app.exceptions import (
    CategorizationError,
    CounselingError,
    SentimentError,
    SummarizationError,
)
from app.services.deidentify import strip_pii_regex
from app.services.llm import LLMService


def test_deidentify_returns_llm_masked_text_on_happy_path() -> None:
    # Arrange
    service = LLMService(provider="openai")
    llm_reply = (
        "This is a fully rewritten, longer masked reply with placeholders "
        "like [NAME] and [EMAIL] included for safety"
    )

    # Act
    with patch.object(LLMService, "_call_openai", return_value=llm_reply):
        result = service.deidentify("hi short text")

    # Assert
    assert result == llm_reply


def test_deidentify_falls_back_to_regex_cleaned_text_when_llm_fails() -> None:
    # Arrange — regression test: LLM failure must never lose the confession
    # by returning an empty string (the historical data-loss bug).
    service = LLMService(provider="openai")
    text = "Email me at bob@example.com or call 555-222-3333 for more info"

    # Act
    with patch.object(LLMService, "_call_openai", return_value=""):
        result = service.deidentify(text)

    # Assert
    assert result == strip_pii_regex(text)
    assert result != ""


def test_categorize_strips_whitespace_and_lowercases_llm_response() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act
    with patch.object(LLMService, "_call_openai", return_value="  Health \n"):
        category = service.categorize("some confession text")

    # Assert
    assert category == "health"


def test_classify_sentiment_normalises_a_valid_label() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act
    with patch.object(LLMService, "_call_openai", return_value=" Negative \n"):
        sentiment = service.classify_sentiment("some confession text")

    # Assert
    assert sentiment == "negative"


def test_classify_sentiment_rejects_a_label_outside_the_known_set() -> None:
    # Arrange — an invented label would silently create a phantom chart bucket
    service = LLMService(provider="openai")

    # Act / Assert
    with (
        patch.object(LLMService, "_call_openai", return_value="devastated"),
        pytest.raises(SentimentError),
    ):
        service.classify_sentiment("some confession text")


def test_categorize_raises_categorization_error_on_empty_llm_response() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act / Assert
    with (
        patch.object(LLMService, "_call_openai", return_value=""),
        pytest.raises(CategorizationError),
    ):
        service.categorize("some confession text")


def test_summarize_returns_llm_response_when_non_empty() -> None:
    # Arrange
    service = LLMService(provider="openai")
    llm_reply = "A compassionate, neutral two-sentence summary."

    # Act
    with patch.object(LLMService, "_call_openai", return_value=llm_reply):
        summary = service.summarize("some confession text")

    # Assert
    assert summary == llm_reply


def test_summarize_raises_summarization_error_on_empty_llm_response() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act / Assert
    with (
        patch.object(LLMService, "_call_openai", return_value="   "),
        pytest.raises(SummarizationError),
    ):
        service.summarize("some confession text")


def test_counsel_returns_llm_response_when_non_empty() -> None:
    # Arrange
    service = LLMService(provider="openai")
    llm_reply = "You have been heard. That took courage to say."

    # Act
    with patch.object(LLMService, "_call_openai", return_value=llm_reply):
        response = service.counsel("some confession text")

    # Assert
    assert response == llm_reply


def test_counsel_raises_counseling_error_on_empty_llm_response() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act / Assert
    with (
        patch.object(LLMService, "_call_openai", return_value="   "),
        pytest.raises(CounselingError),
    ):
        service.counsel("some confession text")


def test_auto_chain_uses_ollama_reply_without_calling_gemini_or_openai() -> None:
    # Arrange
    service = LLMService()

    # Act
    with (
        patch.object(LLMService, "_call_ollama", return_value="ollama reply") as ollama,
        patch.object(LLMService, "_call_gemini") as gemini,
        patch.object(LLMService, "_call_openai") as openai,
    ):
        result = service.categorize("some confession text")

    # Assert
    assert result == "ollama reply"
    ollama.assert_called_once()
    gemini.assert_not_called()
    openai.assert_not_called()


def test_auto_chain_falls_through_to_gemini_when_ollama_is_empty() -> None:
    # Arrange
    service = LLMService()

    # Act
    with (
        patch.object(LLMService, "_call_ollama", return_value=""),
        patch.object(LLMService, "_call_gemini", return_value="gemini reply") as gemini,
        patch.object(LLMService, "_call_openai") as openai,
    ):
        result = service.categorize("some confession text")

    # Assert
    assert result == "gemini reply"
    gemini.assert_called_once()
    openai.assert_not_called()


def test_auto_chain_falls_through_to_openai_when_ollama_and_gemini_are_empty() -> None:
    # Arrange
    service = LLMService()

    # Act
    with (
        patch.object(LLMService, "_call_ollama", return_value=""),
        patch.object(LLMService, "_call_gemini", return_value=""),
        patch.object(LLMService, "_call_openai", return_value="openai reply") as openai,
    ):
        result = service.categorize("some confession text")

    # Assert
    assert result == "openai reply"
    openai.assert_called_once()


def test_auto_chain_raises_when_every_provider_returns_empty() -> None:
    # Arrange
    service = LLMService()

    # Act / Assert
    with (
        patch.object(LLMService, "_call_ollama", return_value=""),
        patch.object(LLMService, "_call_gemini", return_value=""),
        patch.object(LLMService, "_call_openai", return_value=""),
        pytest.raises(CategorizationError),
    ):
        service.categorize("some confession text")


def test_call_gemini_skips_network_call_when_api_key_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    service = LLMService(provider="gemini")

    # Act
    with patch("httpx.post") as mock_post:
        result = service._call_gemini("a prompt")

    # Assert
    assert result == ""
    mock_post.assert_not_called()


def test_call_gemini_parses_reply_text_from_candidates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    service = LLMService(provider="gemini")
    mock_response = Mock()
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}}]
    }

    # Act
    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = service._call_gemini("a prompt")

    # Assert
    assert result == "gemini says hi"
    mock_post.assert_called_once()


def test_call_ollama_returns_empty_string_when_server_unreachable() -> None:
    # Arrange
    service = LLMService(provider="ollama")

    # Act
    with patch("httpx.post", side_effect=ConnectionError("connection refused")):
        result = service._call_ollama("a prompt")

    # Assert
    assert result == ""


def test_call_ollama_parses_reply_text_from_chat_message_response() -> None:
    # Arrange
    service = LLMService(provider="ollama")
    mock_response = Mock()
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "local reply"}
    }

    # Act
    with patch("httpx.post", return_value=mock_response):
        result = service._call_ollama("a prompt")

    # Assert
    assert result == "local reply"


def test_call_ollama_caps_the_context_window_so_the_model_stays_small() -> None:
    # Arrange — without a cap Ollama reserves memory for the model's whole context: a
    # 3B model was seen holding 9.9 GB, enough to starve the machine it ran on
    service = LLMService(provider="ollama")
    mock_response = Mock()
    mock_response.json.return_value = {"message": {"content": "ok"}}

    # Act
    with patch("httpx.post", return_value=mock_response) as post:
        service._call_ollama("a prompt")

    # Assert
    assert post.call_args.kwargs["json"]["options"]["num_ctx"] == 4096


def test_the_context_cap_can_be_changed_from_the_config() -> None:
    # Arrange
    service = LLMService(provider="ollama")
    mock_response = Mock()
    mock_response.json.return_value = {"message": {"content": "ok"}}

    # Act
    with (
        patch("app.services.llm.settings.OLLAMA_NUM_CTX", 2048),
        patch("httpx.post", return_value=mock_response) as post,
    ):
        service._call_ollama("a prompt")

    # Assert
    assert post.call_args.kwargs["json"]["options"]["num_ctx"] == 2048


def test_build_delimited_prompt_wraps_content_and_treats_it_as_data() -> None:
    # Arrange
    instruction = "Assign exactly one category to the confession"
    content = "some untrusted user content"

    # Act
    prompt = LLMService._build_delimited_prompt(instruction, content)

    # Assert
    assert "<<<BEGIN_USER_CONTENT>>>" in prompt
    assert "<<<END_USER_CONTENT>>>" in prompt
    assert instruction in prompt
    assert content in prompt
    assert "untrusted" in prompt.lower()


def test_complete_fences_untrusted_content_and_strips_the_reply() -> None:
    # Arrange
    service = LLMService(provider="ollama")
    seen: list[str] = []

    def fake_ollama(_self: LLMService, prompt: str) -> str:
        seen.append(prompt)
        return "  the reply  \n"

    # Act
    with patch.object(LLMService, "_call_ollama", new=fake_ollama):
        reply = service.complete("Do the task.", "untrusted words")

    # Assert
    assert reply == "the reply"
    assert seen[0].startswith("Do the task.")
    assert (
        "<<<BEGIN_USER_CONTENT>>>\nuntrusted words\n<<<END_USER_CONTENT>>>" in seen[0]
    )


# ── Credentials and content never reach the logs (11.19) ─────────────────


def test_gemini_key_is_sent_in_a_header_never_in_the_url() -> None:
    # Arrange
    service = LLMService(provider="gemini")
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> Mock:
        captured.update(url=url, **kwargs)
        response = Mock()
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        return response

    # Act
    with (
        patch("httpx.post", side_effect=fake_post),
        patch(
            "app.services.llm.get_config",
            side_effect=lambda _n, d: "g-key-1234" if _n == "GEMINI_API_KEY" else d,
        ),
    ):
        service._call_gemini("hello")

    # Assert
    assert "g-key-1234" not in str(captured["url"])
    assert "params" not in captured
    assert captured["headers"] == {"x-goog-api-key": "g-key-1234"}


def test_a_failed_gemini_call_logs_nothing_that_contains_the_key(caplog) -> None:
    # Arrange — an httpx error's text embeds the request URL
    service = LLMService(provider="gemini")

    def failing_post(
        url: str, params: dict[str, str] | None = None, **kwargs: object
    ) -> Mock:
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        raise RuntimeError(f"boom calling {url}" + (f"?{query}" if query else ""))

    # Act
    with (
        caplog.at_level("WARNING"),
        patch("httpx.post", side_effect=failing_post),
        patch(
            "app.services.llm.get_config",
            side_effect=lambda _n, d: "g-key-1234" if _n == "GEMINI_API_KEY" else d,
        ),
    ):
        service._call_gemini("hello")

    # Assert
    assert "boom calling" in caplog.text
    assert "g-key-1234" not in caplog.text


def test_an_unparseable_moderation_answer_is_logged_by_length_not_content(
    caplog,
) -> None:
    # Arrange — moderation reads the ORIGINAL transcript, so its output can echo it
    service = LLMService(provider="ollama")
    echoed = "the person said their manager Dana touched them"

    # Act
    with (
        caplog.at_level("WARNING"),
        patch.object(LLMService, "_call_ollama", return_value=echoed),
    ):
        severity = service.moderate("original words")

    # Assert
    assert severity.value == "policy"
    assert "Dana" not in caplog.text
    assert f"{len(echoed)} chars" in caplog.text


def test_an_unusable_sentiment_answer_is_logged_by_length_not_content(caplog) -> None:
    # Arrange
    service = LLMService(provider="ollama")
    echoed = "this reads like a private detail about Dana"

    # Act
    with (
        caplog.at_level("ERROR"),
        patch.object(LLMService, "_call_ollama", return_value=echoed),
        pytest.raises(SentimentError),
    ):
        service.classify_sentiment("a confession")

    # Assert
    assert "Dana" not in caplog.text
    assert f"{len(echoed)} chars" in caplog.text
