"""Tests for how the themes report falls back when the local model is not usable.

The model boundary is faked (AGENTS.md §16.4); grouping, suppression, the
category fallback and the retry policy run for real.
"""

from __future__ import annotations

import json

import pytest
from app.models.user import UserRole
from app.services import theme_service
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory
from tests.theme_seeding import (
    NOW,
    FakeModel,
    add_confessions,
    get_themes,
    pinned_clock_and_cohort,  # noqa: F401 - autouse fixture
)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "not json at all", '{"themes": []}'])
async def test_an_unusable_model_answer_falls_back_to_categories_and_says_so(
    reply: str,
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", category="compensation")
    await add_confessions(db_session, 3, "manager", category="management")
    model = FakeModel(lambda _content: reply)

    # Act
    with model.patch():
        response = await get_themes(api_client, headers)

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["method"] == "category"
    assert "category" in body["notice"]
    assert [(t["label"], t["confessions"]) for t in body["themes"]] == [
        ("compensation", 4),
        ("management", 3),
    ]


@pytest.mark.asyncio
async def test_model_groups_that_are_all_too_small_fall_back_to_categories(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the model splits the confessions into groups of two, below the
    # cohort of three, so none of its themes may be shown
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 5, "pay", category="compensation")
    reply = json.dumps(
        {
            "themes": [
                {"label": "Tiny A", "items": [1, 2]},
                {"label": "Tiny B", "items": [3, 4]},
            ]
        }
    )
    model = FakeModel(lambda _content: reply)

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["method"] == "category"
    assert "too small" in body["notice"]
    assert [(t["label"], t["confessions"]) for t in body["themes"]] == [
        ("compensation", 5)
    ]
    assert "Tiny" not in json.dumps(body)


@pytest.mark.asyncio
async def test_an_unparseable_first_answer_is_retried_once_and_the_retry_is_used(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — small local models sometimes emit malformed JSON
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    answers = iter(
        ["{oops", json.dumps({"themes": [{"label": "Pay", "items": [1, 2, 3]}]})]
    )
    model = FakeModel(lambda _content: next(answers))

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert len(model.calls) == 2
    assert body["method"] == "model"
    assert [(t["label"], t["confessions"]) for t in body["themes"]] == [("Pay", 3)]


@pytest.mark.asyncio
async def test_no_answer_at_all_is_not_retried(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — an empty reply means the model is not running
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    model = FakeModel(lambda _content: "")

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert len(model.calls) == 1
    assert body["method"] == "category"


@pytest.mark.asyncio
async def test_two_unparseable_answers_fall_back_after_exactly_two_attempts(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    model = FakeModel(lambda _content: "still not json")

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert len(model.calls) == 2
    assert body["method"] == "category"


@pytest.mark.asyncio
async def test_the_database_transaction_is_over_before_the_model_is_called(
    db_session: AsyncSession,
) -> None:
    # Arrange — a model can take minutes; the connection must not be held idle
    await add_confessions(db_session, 3, "pay")
    seen: list[bool] = []

    class RecordingModel:
        def complete(self, instruction: str, content: str) -> str:
            seen.append(db_session.in_transaction())
            return ""

    # Act
    await theme_service.generate_report(db_session, 7, NOW, llm=RecordingModel())  # type: ignore[arg-type]

    # Assert
    assert seen == [False]


@pytest.mark.asyncio
async def test_the_model_is_pinned_to_local_ollama(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the auto chain could reach Gemini or OpenAI; summaries must not leave
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    model = FakeModel()

    # Act
    with model.patch():
        await get_themes(api_client, headers)

    # Assert
    assert [provider for provider, _, _ in model.calls] == ["ollama"]
