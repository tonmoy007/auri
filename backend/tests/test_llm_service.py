"""Unit tests for app.services.llm.LLMService.

Only the provider boundary (_call_openai) is mocked, per AGENTS.md §16.4.
Domain logic (fallback merging, prompt delimiting) runs for real.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.exceptions import CategorizationError, SummarizationError
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


def test_categorize_raises_categorization_error_on_empty_llm_response() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act / Assert
    with patch.object(LLMService, "_call_openai", return_value=""):
        with pytest.raises(CategorizationError):
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
    with patch.object(LLMService, "_call_openai", return_value="   "):
        with pytest.raises(SummarizationError):
            service.summarize("some confession text")


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
