"""Unit tests for app.services.deidentify — 100% line coverage per AGENTS.md §5.2.

No mocks: strip_pii_regex and mask_pii_llm_fallback are pure domain logic and
must never be mocked (AGENTS.md §16.4).
"""

from __future__ import annotations

from app.services.deidentify import mask_pii_llm_fallback, strip_pii_regex


def test_strip_pii_regex_masks_email() -> None:
    # Arrange
    text = "Contact me at john.doe@example.com please"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "Contact me at [EMAIL] please"


def test_strip_pii_regex_masks_phone_number() -> None:
    # Arrange
    text = "Call me at 555-123-4567 today"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "Call me at [PHONE] today"


def test_strip_pii_regex_masks_ssn() -> None:
    # Arrange
    text = "My SSN is 123-45-6789 keep private"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "My SSN is [SSN] keep private"


def test_strip_pii_regex_masks_ipv4_address() -> None:
    # Arrange
    text = "Server IP is 192.168.1.1 for reference"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "Server IP is [IP_ADDRESS] for reference"


def test_strip_pii_regex_masks_street_address() -> None:
    # Arrange
    text = "I live at 123 Main Street in the city"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "I live at [ADDRESS] in the city"


def test_strip_pii_regex_masks_name_pattern() -> None:
    # Arrange
    text = "My name is John Smith and I did this"

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == "[NAME] and I did this"


def test_strip_pii_regex_leaves_text_without_pii_unchanged() -> None:
    # Arrange
    text = "Hi there, no PII in this sentence at all."

    # Act
    result = strip_pii_regex(text)

    # Assert
    assert result == text


def test_mask_pii_llm_fallback_uses_regex_when_llm_response_empty() -> None:
    # Arrange
    text = "hello world this is a test"
    llm_response = ""

    # Act
    result = mask_pii_llm_fallback(text, llm_response)

    # Assert
    assert result == strip_pii_regex(text)
    assert result != ""


def test_mask_pii_llm_fallback_uses_regex_when_llm_response_too_short() -> None:
    # Arrange
    text = "hello world this is a test with more words here"
    llm_response = "hi"

    # Act
    result = mask_pii_llm_fallback(text, llm_response)

    # Assert
    assert result == strip_pii_regex(text)


def test_mask_pii_llm_fallback_uses_llm_response_when_good() -> None:
    # Arrange
    text = "hello world"
    llm_response = "this is a cleaned longer response indeed"

    # Act
    result = mask_pii_llm_fallback(text, llm_response)

    # Assert
    assert result == llm_response
