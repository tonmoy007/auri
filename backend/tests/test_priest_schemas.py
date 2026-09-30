"""Tests for the priest-mode request, response and draft contracts."""

from __future__ import annotations

import pytest
from app.priest.schemas import (
    AnswerKind,
    PriestAnswerResponse,
    PriestAskRequest,
    PriestCitation,
    PriestDraft,
    TraditionId,
)
from pydantic import ValidationError


def test_a_plain_question_is_accepted_and_trimmed() -> None:
    # Act
    request = PriestAskRequest(question="  What is the Cinvat bridge?  ")

    # Assert
    assert request.question == "What is the Cinvat bridge?"
    assert request.language == "en" and request.tradition is None


@pytest.mark.parametrize("question", ["", "  ", "ab", "x" * 1001])
def test_a_question_outside_three_to_a_thousand_characters_is_refused(
    question: str,
) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestAskRequest(question=question)


def test_the_limits_themselves_are_allowed() -> None:
    # Act / Assert
    assert PriestAskRequest(question="abc").question == "abc"
    assert len(PriestAskRequest(question="x" * 1000).question) == 1000


@pytest.mark.parametrize(
    "bad", ["hello\x00there", "bell\x07here", "esc\x1bhere", "del\x7fhere"]
)
def test_control_characters_are_refused(bad: str) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestAskRequest(question=bad)


def test_line_breaks_and_tabs_are_allowed() -> None:
    # Act / Assert
    assert PriestAskRequest(question="first line\nsecond\tpart").question.startswith(
        "first"
    )


@pytest.mark.parametrize(
    "field", ["confession_id", "transcript", "device_token_hash", "anything"]
)
def test_no_extra_field_is_ever_accepted(field: str) -> None:
    # Arrange — a confession id or text must never be sendable to the guide
    payload = {"question": "a question", field: "x"}

    # Act / Assert
    with pytest.raises(ValidationError):
        PriestAskRequest(**payload)


def test_only_english_is_accepted_for_now() -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestAskRequest(question="a question", language="bn")


def test_a_known_tradition_is_accepted_and_an_unknown_one_is_not() -> None:
    # Act / Assert
    assert (
        PriestAskRequest(question="a question", tradition="islam").tradition
        is TraditionId.islam
    )
    with pytest.raises(ValidationError):
        PriestAskRequest(question="a question", tradition="pastafarianism")


@pytest.mark.parametrize("kind", list(AnswerKind))
def test_every_kind_serialises_with_its_defaults(kind: AnswerKind) -> None:
    # Act
    response = PriestAnswerResponse(request_id="r1", kind=kind)

    # Assert
    body = response.model_dump(mode="json")
    assert body["kind"] == kind.value
    assert body["points"] == [] and body["quotes"] == [] and body["citations"] == []
    assert body["disclaimer_version"]


def test_a_citation_snippet_is_capped() -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestCitation(
            id="S1",
            note_title="t",
            heading_path=[],
            note_type="story",
            tradition_labels=[],
            snippet="x" * 281,
        )


def _draft(**overrides: object) -> dict:
    base = {
        "kind": "answer",
        "points": [{"text": "A point.", "sources": ["S1"]}],
        "quotes": [{"text": "a quotation of twelve+", "source": "S1"}],
        "reflection": "A gentle thought.",
    }
    return {**base, **overrides}


def test_a_well_formed_draft_is_accepted() -> None:
    # Act / Assert
    assert PriestDraft.model_validate(_draft()).kind == "answer"


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "crisis"},
        {"points": [{"text": "x" * 301, "sources": ["S1"]}]},
        {"points": [{"text": "ok", "sources": []}]},
        {"points": [{"text": "ok", "sources": ["S1", "S2", "S3", "S4"]}]},
        {"points": [{"text": "ok", "sources": ["source1"]}]},
        {"points": [{"text": "ok", "sources": ["S1"]}] * 5},
        {"quotes": [{"text": "too short", "source": "S1"}]},
        {"quotes": [{"text": "x" * 281, "source": "S1"}]},
        {"quotes": [{"text": "a quotation of twelve+", "source": "S1"}] * 3},
        {"reflection": "x" * 401},
    ],
)
def test_a_draft_outside_the_bounds_is_refused(overrides: dict) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestDraft.model_validate(_draft(**overrides))


def test_unknown_keys_in_a_draft_are_ignored_not_trusted() -> None:
    # Act
    draft = PriestDraft.model_validate(_draft(system_prompt="leak", kind="not_covered"))

    # Assert
    assert draft.kind == "not_covered" and not hasattr(draft, "system_prompt")


@pytest.mark.parametrize(
    "bad",
    ["c1\x85control", "bidi\u202eflip", "isolate\u2066here", "sep\u2028here", "sep\u2029here"],
)
def test_c1_controls_bidi_overrides_and_line_separators_are_refused(bad: str) -> None:
    # Act / Assert
    with pytest.raises(ValidationError):
        PriestAskRequest(question=bad)


def test_the_joiner_used_in_emoji_is_allowed() -> None:
    # Act / Assert
    assert PriestAskRequest(question="family \U0001f468‍\U0001f469 prayer").question
