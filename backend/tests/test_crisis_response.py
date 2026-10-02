"""Tests for the fixed crisis reply (12.7).

Generated text is never trusted to produce a phone number or a safety message, so a
crisis item gets a template with contacts from configuration, or a compiled-in
message if none are set.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from app.models.confession import ModerationSeverity
from app.services import crisis_response
from httpx import AsyncClient

from tests.conftest import SettingPatcher
from tests.counsel_replies import make_counsel_reply

HELPLINE = "Lifeline"
NUMBER = "+880 1234-567890"
EAP = "Employee Assistance: 0800 555 0100"


def _configure(set_setting: SettingPatcher, **values: str) -> None:
    for name in (
        "CRISIS_HELPLINE_NAME",
        "CRISIS_HELPLINE_NUMBER",
        "CRISIS_EAP_CONTACT",
    ):
        set_setting(name, values.get(name, ""))


def test_with_nothing_configured_the_compiled_in_message_is_used(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(set_setting)

    # Act
    reply = crisis_response.render()

    # Assert
    assert reply.contacts == []
    assert "emergency number" in reply.text
    assert reply.text == crisis_response.COMPILED_IN_MESSAGE


def test_configured_contacts_appear_in_the_reply_and_as_structured_data(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _configure(
        set_setting,
        CRISIS_HELPLINE_NAME=HELPLINE,
        CRISIS_HELPLINE_NUMBER=NUMBER,
        CRISIS_EAP_CONTACT=EAP,
    )

    # Act
    reply = crisis_response.render()

    # Assert
    assert HELPLINE in reply.text and NUMBER in reply.text and EAP in reply.text
    assert [(c.label, c.detail) for c in reply.contacts] == [
        (HELPLINE, NUMBER),
        ("Employee assistance", EAP),
    ]
    assert reply.contacts[0].dial == "+8801234567890"
    assert reply.contacts[1].dial is None


def test_the_reply_is_byte_identical_every_time(set_setting: SettingPatcher) -> None:
    # Arrange
    _configure(
        set_setting, CRISIS_HELPLINE_NAME=HELPLINE, CRISIS_HELPLINE_NUMBER=NUMBER
    )

    # Act / Assert — nothing generated, nothing random
    assert crisis_response.render().text == crisis_response.render().text


def test_a_closing_line_is_appended_verbatim(set_setting: SettingPatcher) -> None:
    # Arrange
    _configure(set_setting)

    # Act
    reply = crisis_response.render("Nobody at the company can see this chat.")

    # Assert
    assert reply.text.endswith("Nobody at the company can see this chat.")


@pytest.mark.parametrize(
    "bad", ["call <b>now</b>", "555-CALL-ME", "1" * 40, "1; DROP", ""]
)
def test_a_number_that_is_not_dialable_is_not_published(
    bad: str, set_setting: SettingPatcher
) -> None:
    # Arrange — a value that could carry markup or a payload instead of a number
    _configure(set_setting, CRISIS_HELPLINE_NAME=HELPLINE, CRISIS_HELPLINE_NUMBER=bad)

    # Act
    reply = crisis_response.render()

    # Assert
    assert bad not in reply.text or bad == ""
    assert all(contact.dial is None for contact in reply.contacts)


@pytest.mark.asyncio
async def test_a_crisis_confession_gets_the_template_and_never_asks_the_model(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(
        set_setting, CRISIS_HELPLINE_NAME=HELPLINE, CRISIS_HELPLINE_NUMBER=NUMBER
    )
    payload = {
        "device_token_hash": "c" * 32,
        "voice_mask": "warm",
        "transcript": "words",
    }

    # Act
    with (
        patch("app.api.v1.confessions.LLMService.deidentify", return_value="words"),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="S"),
        patch(
            "app.api.v1.confessions.LLMService.classify_sentiment",
            return_value="negative",
        ),
        patch(
            "app.api.v1.confessions.LLMService.moderate",
            return_value=ModerationSeverity.crisis,
        ),
        patch(
            "app.api.v1.confessions.LLMService.counsel",
            return_value="call 999-FAKE-NUMBER",
        ) as counsel,
    ):
        response = await api_client.post("/api/v1/confessions", json=payload)

    # Assert
    assert response.status_code == 201
    text = response.json()["counselor_response"]
    assert NUMBER in text and "FAKE" not in text
    counsel.assert_not_called()


@pytest.mark.asyncio
async def test_a_non_crisis_confession_still_gets_a_generated_reply(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    _configure(set_setting)
    payload = {
        "device_token_hash": "d" * 32,
        "voice_mask": "warm",
        "transcript": "words",
    }

    # Act
    with (
        patch("app.api.v1.confessions.LLMService.deidentify", return_value="words"),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="other"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="S"),
        patch(
            "app.api.v1.confessions.LLMService.classify_sentiment",
            return_value="neutral",
        ),
        patch(
            "app.api.v1.confessions.LLMService.moderate",
            return_value=ModerationSeverity.none,
        ),
        patch(
            "app.api.v1.confessions.LLMService.counsel",
            return_value=make_counsel_reply("You were heard."),
        ),
    ):
        response = await api_client.post("/api/v1/confessions", json=payload)

    # Assert
    assert (
        response.json()["counselor_response"]
        == make_counsel_reply("You were heard.").render()
    )
