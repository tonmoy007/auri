"""Anonymity pins for the HR reply flow.

Two directions have to hold at once: the confessor's payload must not name
any staff member, and the reply flow must not show HR anything about the
confessor beyond the summary tier. These tests pin the schema shape, the
route wiring and the real payloads end to end (AGENTS.md §16.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from app.api.v1.confessions import ConfessionResponse, ConfessorConfessionResponse
from app.main import app
from app.models.confession import Confession, ConfessionStatus
from app.models.department import Department
from app.models.user import User, UserRole
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import add_confession, stamp_moderation
from tests.conftest import StaffFactory

CONFESSIONS_PATH = "/api/v1/confessions"
HR_PATH = "/api/v1/hr/confessions"
DEVICE_A = "device-a-hash-of-the-confessor-01"
DEVICE_B = "device-b-hash-of-somebody-else-02"
REPLY_TEXT = "Thank you for telling us. We are looking into it."
MOMENT = datetime(2026, 9, 29, 9, 0, 0, tzinfo=timezone.utc)

EXPECTED_CONFESSOR_FIELDS = {
    "id",
    "voice_mask",
    "transcript",
    "ai_summary",
    "category",
    "pii_stripped",
    "status",
    "recipient_dept",
    "delivered_at",
    "severity",
    "acknowledged_at",
    "reviewed_at",
    "counselor_response",
    "created_at",
    "updated_at",
    "hr_reply",
    "hr_replied_at",
    "hr_reply_edited_at",
}
HR_REPLY_FIELDS = {"hr_reply", "hr_replied_at", "hr_reply_edited_at"}
STAFF_ONLY_KEYS = ("hr_reply_by", "reviewed_by", "acknowledged_by")


@dataclass(frozen=True)
class RepliedConfession:
    """A confession its own reviewer has replied to, plus who that reviewer is."""

    confession: Confession
    author: User


@pytest_asyncio.fixture
async def replied_by_reviewer(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> RepliedConfession:
    """A pending confession the replying HR user also approved and acknowledged.

    That is the strongest fixture for author anonymity: the author's id is
    already on the row in two other columns before the reply lands.
    """
    author, headers = await make_staff(UserRole.hr, "the-author@example.com")
    confession = await add_confession(db_session, device_token_hash=DEVICE_A)
    await stamp_moderation(db_session, confession, author.id, MOMENT)
    response = await api_client.put(
        f"{HR_PATH}/{confession.id}/reply", headers=headers, json={"reply": REPLY_TEXT}
    )
    assert response.status_code == 200
    return RepliedConfession(confession=confession, author=author)


def _naive_utc(iso_timestamp: str) -> datetime:
    """Parse a JSON timestamp to naive UTC, matching what SQLite returns."""
    return datetime.fromisoformat(iso_timestamp).replace(tzinfo=None)


def _confessor_routes() -> dict[tuple[str, str], APIRoute]:
    """Index the confessor's routes by (method, path)."""
    wanted = {
        ("POST", CONFESSIONS_PATH),
        ("GET", CONFESSIONS_PATH),
        ("GET", f"{CONFESSIONS_PATH}/{{confession_id}}"),
        ("POST", f"{CONFESSIONS_PATH}/{{confession_id}}/forward"),
    }
    return {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if (method, route.path) in wanted
    }


# ── Schema shape ─────────────────────────────────────────────────────────


def test_confessor_payload_has_exactly_the_agreed_fields() -> None:
    # Arrange
    expected = EXPECTED_CONFESSOR_FIELDS

    # Act
    actual = set(ConfessorConfessionResponse.model_fields)

    # Assert
    assert actual == expected


def test_staff_confession_response_gains_no_reply_field() -> None:
    # Arrange / Act
    leaked = set(ConfessionResponse.model_fields) & HR_REPLY_FIELDS

    # Assert
    assert leaked == set()


def test_all_four_confessor_routes_use_the_confessor_response_model() -> None:
    # Arrange
    list_key = ("GET", CONFESSIONS_PATH)

    # Act
    routes = _confessor_routes()

    # Assert
    assert len(routes) == 4
    for key, route in routes.items():
        expected_model = (
            list[ConfessorConfessionResponse]
            if key == list_key
            else ConfessorConfessionResponse
        )
        assert route.response_model == expected_model


# ── Author anonymity ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confessor_list_shows_the_reply_and_no_staff_identity(
    api_client: AsyncClient, replied_by_reviewer: RepliedConfession
) -> None:
    # Arrange
    author = replied_by_reviewer.author

    # Act
    response = await api_client.get(
        CONFESSIONS_PATH, headers={"X-Device-Token-Hash": DEVICE_A}
    )

    # Assert
    item = response.json()[0]
    assert response.status_code == 200
    assert item["hr_reply"] == REPLY_TEXT
    assert str(author.id) not in response.text
    assert author.email not in response.text
    for key in STAFF_ONLY_KEYS:
        assert key not in item


@pytest.mark.asyncio
async def test_confessor_detail_shows_the_reply_and_no_staff_identity(
    api_client: AsyncClient, replied_by_reviewer: RepliedConfession
) -> None:
    # Arrange
    author = replied_by_reviewer.author
    path = f"{CONFESSIONS_PATH}/{replied_by_reviewer.confession.id}"

    # Act
    response = await api_client.get(path, headers={"X-Device-Token-Hash": DEVICE_A})

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["hr_reply"] == REPLY_TEXT
    assert str(author.id) not in response.text
    assert author.email not in response.text
    for key in STAFF_ONLY_KEYS:
        assert key not in body


@pytest.mark.asyncio
async def test_confessor_payload_keeps_the_review_timestamps_without_the_reviewer(
    api_client: AsyncClient, replied_by_reviewer: RepliedConfession
) -> None:
    # Arrange
    path = f"{CONFESSIONS_PATH}/{replied_by_reviewer.confession.id}"

    # Act
    body = (
        await api_client.get(path, headers={"X-Device-Token-Hash": DEVICE_A})
    ).json()

    # Assert
    seeded_moment = MOMENT.replace(tzinfo=None)
    assert set(body) == EXPECTED_CONFESSOR_FIELDS
    assert _naive_utc(body["reviewed_at"]) == seeded_moment
    assert _naive_utc(body["acknowledged_at"]) == seeded_moment


# ── Device scope ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_another_device_never_sees_the_reply_in_its_list(
    api_client: AsyncClient, replied_by_reviewer: RepliedConfession
) -> None:
    # Arrange
    headers = {"X-Device-Token-Hash": DEVICE_B}

    # Act
    response = await api_client.get(CONFESSIONS_PATH, headers=headers)

    # Assert
    listed_ids = {item["id"] for item in response.json()}
    assert response.status_code == 200
    assert str(replied_by_reviewer.confession.id) not in listed_ids
    assert REPLY_TEXT not in response.text


@pytest.mark.asyncio
async def test_another_device_is_refused_the_reply_on_the_detail_route(
    api_client: AsyncClient, replied_by_reviewer: RepliedConfession
) -> None:
    # Arrange
    path = f"{CONFESSIONS_PATH}/{replied_by_reviewer.confession.id}"

    # Act
    response = await api_client.get(path, headers={"X-Device-Token-Hash": DEVICE_B})

    # Assert
    assert response.status_code == 403
    assert REPLY_TEXT not in response.text


# ── Staff paths ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delivery_queue_never_carries_the_reply(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting,
) -> None:
    # Arrange — the bot path writes no audit row, so it must not see replies
    set_setting("DELIVERY_API_KEY", "test-delivery-secret")
    db_session.add(Department(name="HR", telegram_chat_id="1", is_active=True))
    await db_session.commit()
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        department="HR",
        device_token_hash=DEVICE_A,
    )
    await api_client.put(
        f"{HR_PATH}/{confession.id}/reply", headers=headers, json={"reply": REPLY_TEXT}
    )

    # Act
    response = await api_client.get(
        "/api/v1/delivery/queue", headers={"X-Delivery-Api-Key": "test-delivery-secret"}
    )

    # Assert
    items = response.json()
    assert response.status_code == 200
    assert [item["id"] for item in items] == [str(confession.id)]
    assert "hr_reply" not in items[0]
    assert REPLY_TEXT not in response.text
