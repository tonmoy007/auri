"""Tests for GET /api/v1/hr/themes.

Only the model boundary is mocked (AGENTS.md §16.4): the query, suppression,
ranking, fallback and digest all run for real against a real database.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

import pytest
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SettingPatcher, StaffFactory
from tests.theme_seeding import (
    MIN_COHORT,
    NOW,
    PATH,
    PRIVATE,
    FakeModel,
    add_confessions,
    get_themes,
    pinned_clock_and_cohort,  # noqa: F401 - autouse fixture
)


@pytest.mark.asyncio
async def test_hr_sees_ranked_themes_with_counts(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 5, "pay")
    await add_confessions(db_session, 3, "manager")
    model = FakeModel()

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["method"] == "model"
    assert [(t["rank"], t["label"], t["confessions"]) for t in body["themes"]] == [
        (1, "Theme pay", 5),
        (2, "Theme manager", 3),
    ]
    assert body["analysed"] == {"label": "analysed", "count": 8, "suppressed": False}


@pytest.mark.asyncio
async def test_a_theme_below_the_cohort_is_withheld_with_its_label_everywhere(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — "office" has MIN_COHORT - 1 members
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay")
    await add_confessions(db_session, MIN_COHORT - 1, "office")

    # Act
    with FakeModel().patch():
        response = await get_themes(api_client, headers)

    # Assert
    body = response.json()
    assert body["hidden_themes"] == 1
    assert "Theme office" not in response.text
    assert "office" not in body["digest_markdown"] + body["digest_csv"]


@pytest.mark.asyncio
async def test_only_summaries_reach_the_model_never_a_transcript_or_device_hash(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    model = FakeModel()

    # Act
    with model.patch():
        await get_themes(api_client, headers)

    # Assert
    _, _, content = model.calls[0]
    assert "concern about pay number 0" in content
    assert PRIVATE not in content
    assert "pay000" not in content


@pytest.mark.asyncio
async def test_too_few_confessions_report_nothing_and_never_call_the_model(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, MIN_COHORT - 1, "pay")
    model = FakeModel()

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["method"] == "none"
    assert body["themes"] == []
    assert body["analysed"]["suppressed"] is True
    assert model.calls == []


@pytest.mark.asyncio
async def test_withdrawn_confessions_are_not_analysed(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    await add_confessions(db_session, 10, "manager", status=ConfessionStatus.deleted)
    model = FakeModel()

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["analysed"]["count"] == 3
    assert "manager" not in model.calls[0][2]


@pytest.mark.asyncio
async def test_previous_period_gives_a_count_and_a_sentiment_change(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — 4 of 7 negative now; 3 of 6 last week
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", sentiment="negative", days_ago=1)
    await add_confessions(db_session, 3, "pay", sentiment="positive", days_ago=1)
    await add_confessions(db_session, 3, "pay", sentiment="negative", days_ago=10)
    await add_confessions(db_session, 3, "pay", sentiment="positive", days_ago=10)

    # Act
    with FakeModel().patch():
        theme = (await get_themes(api_client, headers)).json()["themes"][0]

    # Assert
    assert theme["previous_period"] == {
        "label": "previous",
        "count": 6,
        "suppressed": False,
    }
    assert (theme["negative_share"], theme["previous_negative_share"]) == (0.57, 0.5)
    assert (theme["sentiment_change"], theme["sentiment_status"]) == (0.07, "ok")


@pytest.mark.asyncio
async def test_a_share_that_would_reveal_one_negative_person_is_withheld(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — 5 confessions, exactly 1 negative: 0.2 x 5 would name that one
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 4, "pay", sentiment="neutral")
    await add_confessions(db_session, 1, "pay", sentiment="negative")

    # Act
    with FakeModel().patch():
        response = await get_themes(api_client, headers)

    # Assert
    theme = response.json()["themes"][0]
    assert theme["confessions"] == 5
    assert theme["negative_share"] is None
    assert theme["previous_negative_share"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days_ago", "read_once", "analysed"),
    [(0, True, 6), (7, True, 3), (14, False, 3), (15, False, 3)],
    ids=["at-now", "exactly-at-start", "exactly-at-previous-start", "older-than-both"],
)
async def test_period_edges_read_each_confession_once_or_not_at_all(
    days_ago: float,
    read_once: bool,
    analysed: int,
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange — three "pay" confessions on an edge, three "manager" ones today so
    # the model is called. (start, now] is current; (start - days, start] is previous.
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay", days_ago=days_ago)
    await add_confessions(db_session, 3, "manager", days_ago=1)
    model = FakeModel()

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    pay_lines = [line for line in model.calls[0][2].split("\n") if "pay" in line]
    assert len(pay_lines) == (3 if read_once else 0)
    assert body["analysed"]["count"] == analysed


@pytest.mark.asyncio
async def test_exactly_the_cap_is_not_reported_as_partial(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 60, "pay")

    # Act
    with FakeModel().patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["truncated"] is False
    assert body["analysed"]["count"] == 60


@pytest.mark.asyncio
async def test_blank_summaries_are_skipped_without_using_up_the_cap(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — 5 newest are blank, then 60 real ones: all 60 must still be read
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 60, "pay", days_ago=2)
    for index in range(5):
        db_session.add(
            Confession(
                device_token_hash=f"blank{index:027d}",
                voice_mask="warm",
                transcript=PRIVATE,
                ai_summary="   ",
                category="work",
                sentiment="neutral",
                pii_stripped=True,
                status=ConfessionStatus.pending,
                created_at=NOW - timedelta(hours=1 + index),
            )
        )
    await db_session.commit()

    # Act
    with FakeModel().patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["analysed"]["count"] == 60
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_a_view_over_the_cap_says_it_is_partial(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 61, "pay")
    model = FakeModel()

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert body["truncated"] is True
    assert body["analysed"]["count"] == 60
    assert len(model.calls[0][2].split("\n")) == 60


@pytest.mark.asyncio
async def test_a_hostile_label_is_defused_in_the_downloadable_csv(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 3, "pay")
    hostile = "=IMPORTXML(A, B)"
    reply = json.dumps({"themes": [{"label": hostile, "items": [1, 2, 3]}]})
    model = FakeModel(lambda _content: reply)

    # Act
    with model.patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    csv_label = list(csv.reader(io.StringIO(body["digest_csv"])))[1][1]
    assert csv_label == "'" + hostile
    assert "=IMPORTXML(" not in body["digest_markdown"]


@pytest.mark.asyncio
async def test_the_digest_matches_the_themes_in_the_same_response(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confessions(db_session, 5, "pay")

    # Act
    with FakeModel().patch():
        body = (await get_themes(api_client, headers)).json()

    # Assert
    assert "| 1 | Theme pay | 5 |" in body["digest_markdown"]
    assert body["digest_csv"].splitlines()[1].startswith("1,Theme pay,5,")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected"),
    [(UserRole.hr, 200), (UserRole.admin, 200), (UserRole.moderator, 403)],
)
async def test_role_matrix(
    role: UserRole,
    expected: int,
    api_client: AsyncClient,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(role)

    # Act
    with FakeModel().patch():
        response = await get_themes(api_client, headers)

    # Assert
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_no_session_and_the_legacy_admin_key_are_refused(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ADMIN_API_KEY", "legacy-key-for-tests")

    # Act
    anonymous = await api_client.get(PATH)
    legacy = await api_client.get(
        PATH, headers={"X-Admin-Api-Key": "legacy-key-for-tests"}
    )

    # Assert
    assert (anonymous.status_code, legacy.status_code) == (401, 401)


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 31, -1])
async def test_days_outside_the_supported_range_is_rejected(
    days: int, api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await get_themes(api_client, headers, days=days)

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_successful_read_is_audited_and_a_refused_one_is_not(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    hr, hr_headers = await make_staff(UserRole.hr)
    _, mod_headers = await make_staff(UserRole.moderator)

    # Act
    with FakeModel().patch():
        await get_themes(api_client, mod_headers)
        await get_themes(api_client, hr_headers)

    # Assert
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert [(e.action, e.actor_user_id, e.content_tier) for e in events] == [
        ("themes.read", hr.id, "summary")
    ]
