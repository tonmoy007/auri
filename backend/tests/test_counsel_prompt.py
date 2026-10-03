"""Tests for the counselor prompt file (plan 12.4): persona, anti-patterns, examples.

The few-shot examples in ``counsel.md`` teach the model the reply shape, so each one
must itself be a valid ``CounselReply`` and pass every deterministic check the
evaluation harness applies to a generated reply. Otherwise the prompt would teach
the model a reply the harness then fails.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from app.llm.prompt_loader import load_prompt
from app.schemas.counsel import CounselReply, parse_counsel_reply

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from eval.counsel_checks import (
    EvalItem,
    ScoreContext,
    score_reply,
)

_EXAMPLE_REPLY = re.compile(r"^Example reply: (\{.*\})$", re.MULTILINE)
_CTX = ScoreContext(crisis_text="unused: no example is a crisis reply")


def _example_replies() -> list[str]:
    return _EXAMPLE_REPLY.findall(load_prompt("counsel").body)


def test_counsel_prompt_is_version_two() -> None:
    # Assert
    assert load_prompt("counsel").version == "2.0"


def test_persona_is_a_company_assistant_not_a_priest() -> None:
    # Arrange
    body = load_prompt("counsel").body.lower()

    # Assert
    assert "priest" not in body
    assert "confession" not in body.replace("example confession", "")
    assert "company" in body
    assert "plain" in body


@pytest.mark.parametrize(
    "anti_pattern",
    [
        "toxic positivity",
        "talk to them",
        "corporate boilerplate",
    ],
)
def test_prompt_names_each_anti_pattern(anti_pattern: str) -> None:
    # Assert
    assert anti_pattern in load_prompt("counsel").body.lower()


def test_prompt_does_not_name_resources_the_company_may_not_have() -> None:
    # Arrange
    body = load_prompt("counsel").body.lower()

    # Assert
    assert "do not name" in body


def test_there_is_one_example_each_for_venting_praise_and_anger() -> None:
    # Arrange
    tones = [json.loads(reply)["tone"] for reply in _example_replies()]

    # Assert
    assert len(tones) == 3
    assert "celebratory" in tones


def test_every_example_is_a_valid_reply() -> None:
    # Act
    parsed = [parse_counsel_reply(raw) for raw in _example_replies()]

    # Assert
    assert all(isinstance(reply, CounselReply) for reply in parsed)


def test_every_example_passes_the_harness_checks() -> None:
    # Arrange
    item = EvalItem(
        id="example",
        category="other",
        text="unused",
        expected_route="counsel",
        must_not_contain=(),
        notes="prompt example",
    )

    # Act
    results = [
        score_reply(
            item,
            {"route": "counsel", "reply": parse_counsel_reply(raw).render()},
            _CTX,
        )
        for raw in _example_replies()
    ]

    # Assert
    for statuses in results:
        assert "fail" not in statuses.values(), statuses


def test_prompt_still_demands_the_json_contract() -> None:
    # Arrange
    body = load_prompt("counsel").body

    # Assert
    for key in ("acknowledgement", "reflection", "suggestions", "closing", "tone"):
        assert f'"{key}"' in body
    assert "Output only the JSON object" in body
