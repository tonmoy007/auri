"""Tests for the structured counselor reply and the parser for a model's JSON."""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.exceptions import CounselingError
from app.schemas.counsel import (
    MAX_ACKNOWLEDGEMENT_CHARS,
    MAX_SUGGESTIONS,
    CounselReply,
    CounselTone,
    parse_counsel_reply,
)

SECRET = "zebra-project-7731"


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "acknowledgement": "It sounds like this has weighed on you for a while.",
        "reflection": "Naming it out loud is a real first step.",
        "suggestions": ["You could tell one person you trust how heavy it has felt."],
        "closing": "You have been heard here.",
        "tone": "gentle",
    }
    base.update(overrides)
    return base


def test_parses_a_plain_json_object_into_its_parts() -> None:
    # Arrange
    raw = json.dumps(payload())

    # Act
    reply = parse_counsel_reply(raw)

    # Assert
    assert (
        reply.acknowledgement == "It sounds like this has weighed on you for a while."
    )
    assert reply.suggestions == [
        "You could tell one person you trust how heavy it has felt."
    ]
    assert reply.tone is CounselTone.gentle


@pytest.mark.parametrize(
    "wrap",
    [
        lambda body: f"```json\n{body}\n```",
        lambda body: f"```\n{body}\n```",
        lambda body: f"Here is my reply:\n{body}\nHope that helps.",
        lambda body: f"  \n{body}  \n",
    ],
)
def test_tolerates_a_code_fence_and_surrounding_prose(wrap: Any) -> None:
    # Arrange
    raw = wrap(json.dumps(payload()))

    # Act
    reply = parse_counsel_reply(raw)

    # Assert
    assert reply.closing == "You have been heard here."


def test_suggestions_default_to_none_and_extra_keys_are_ignored() -> None:
    # Arrange
    body = payload(mood="sad")
    del body["suggestions"]

    # Act
    reply = parse_counsel_reply(json.dumps(body))

    # Assert
    assert reply.suggestions == []
    assert "mood" not in reply.model_dump()


def test_strips_whitespace_from_every_text_part() -> None:
    # Arrange
    body = payload(acknowledgement="  Thank you.  ", suggestions=["  Rest.  "])

    # Act
    reply = parse_counsel_reply(json.dumps(body))

    # Assert
    assert (reply.acknowledgement, reply.suggestions) == ("Thank you.", ["Rest."])


def test_render_joins_the_parts_into_one_paragraph() -> None:
    # Arrange
    reply = CounselReply.model_validate(payload())

    # Act
    text = reply.render()

    # Assert
    assert text == (
        "It sounds like this has weighed on you for a while. "
        "Naming it out loud is a real first step. "
        "You could tell one person you trust how heavy it has felt. "
        "You have been heard here."
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"acknowledgement": ""}, "acknowledgement"),
        ({"acknowledgement": "   "}, "acknowledgement"),
        ({"acknowledgement": "x" * (MAX_ACKNOWLEDGEMENT_CHARS + 1)}, "acknowledgement"),
        ({"reflection": 7}, "reflection"),
        ({"closing": None}, "closing"),
        ({"tone": "furious"}, "tone"),
        ({"suggestions": "just rest"}, "suggestions"),
        ({"suggestions": [""]}, "suggestions"),
        ({"suggestions": ["a."] * (MAX_SUGGESTIONS + 1)}, "suggestions"),
    ],
)
def test_a_part_that_breaks_the_schema_names_only_its_field(
    overrides: dict[str, Any], field: str
) -> None:
    # Arrange
    raw = json.dumps(payload(**overrides))

    # Act
    with pytest.raises(CounselingError) as caught:
        parse_counsel_reply(raw)

    # Assert
    assert str(caught.value) == (
        f"counselor reply does not fit the schema (fields: {field})"
    )


def test_a_missing_part_is_refused() -> None:
    # Arrange
    body = payload()
    del body["tone"]

    # Act
    with pytest.raises(CounselingError) as caught:
        parse_counsel_reply(json.dumps(body))

    # Assert
    assert "tone" in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        f"You were heard. {SECRET}",
        "{not json at all",
        "",
        "   ",
        "[1, 2, 3]",
        '"just a string"',
    ],
)
def test_output_that_is_not_a_json_object_is_refused_without_echoing_it(
    raw: str,
) -> None:
    # Act
    with pytest.raises(CounselingError) as caught:
        parse_counsel_reply(raw)

    # Assert
    assert SECRET not in str(caught.value)
    assert "counselor reply" in str(caught.value)


def test_a_reply_with_a_private_detail_never_leaks_through_the_error() -> None:
    # Arrange
    raw = json.dumps(payload(tone="furious", reflection=f"About {SECRET}."))

    # Act
    with pytest.raises(CounselingError) as caught:
        parse_counsel_reply(raw)

    # Assert
    assert SECRET not in str(caught.value)
