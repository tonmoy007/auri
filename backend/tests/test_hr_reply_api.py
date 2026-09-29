"""API tests for writing and listing HR replies (PUT /hr/confessions/{id}/reply).

Real routes, real role checks, real audit writes (AGENTS.md §16.4). Time is
frozen through the ``hr.get_clock`` dependency (AGENTS.md §16.5); the
``api_client`` fixture clears the override on teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.api.v1 import hr
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import (
    DEFAULT_DEVICE_HASH,
    DEFAULT_TRANSCRIPT,
    add_confession,
)
from tests.conftest import StaffFactory

HR_PATH = "/api/v1/hr/confessions"
REPLY_ACTION = "hr_reply.write"
T1 = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=1)
REPLY_TEXT = "Thank you for telling us. We are looking into it."
# httpx's ASGITransport reports this as the client address of every request.
TEST_CLIENT_HOST = "127.0.0.1"


def _reply_path(confession_id: uuid.UUID) -> str:
    return f"{HR_PATH}/{confession_id}/reply"


def _freeze_clock(moment: datetime) -> None:
    app.dependency_overrides[hr.get_clock] = lambda: lambda: moment


def _parse(value: str | None) -> datetime | None:
    """Parse a JSON timestamp to naive UTC, matching what SQLite returns."""
    return datetime.fromisoformat(value).replace(tzinfo=None) if value else None


async def _audit_rows(session: AsyncSession, action: str | None = None) -> list:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    return list((await session.execute(stmt)).scalars().all())


async def _stored_reply(session: AsyncSession, confession_id: uuid.UUID):
    return await session.scalar(
        select(Confession.hr_reply).where(Confession.id == confession_id)
    )


# ── Who may write ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_status"),
    [(UserRole.hr, 200), (UserRole.admin, 200), (UserRole.moderator, 403)],
)
async def test_reply_write_is_limited_to_hr_and_admin_sessions(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    role: UserRole,
    expected_status: int,
) -> None:
    # Arrange
    _, headers = await make_staff(role)
    confession = await add_confession(db_session)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": REPLY_TEXT}
    )

    # Assert
    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_reply_write_requires_a_session(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    confession = await add_confession(db_session)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), json={"reply": REPLY_TEXT}
    )

    # Assert
    assert response.status_code == 401
    assert await _stored_reply(db_session, confession.id) is None


@pytest.mark.asyncio
async def test_legacy_admin_api_key_cannot_write_a_reply(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — a shared secret has no named author to put in the audit trail
    set_setting("ADMIN_API_KEY", "legacy-admin-secret")
    confession = await add_confession(db_session)

    # Act
    response = await api_client.put(
        _reply_path(confession.id),
        headers={"X-Admin-Api-Key": "legacy-admin-secret"},
        json={"reply": REPLY_TEXT},
    )

    # Assert
    assert response.status_code == 401
    assert await _stored_reply(db_session, confession.id) is None


# ── What comes back ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_response_shows_the_write_and_nothing_about_the_confessor(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session, severity="crisis")
    _freeze_clock(T1)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": f"  {REPLY_TEXT} "}
    )

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["id"] == str(confession.id)
    assert body["hr_reply"] == REPLY_TEXT
    assert _parse(body["hr_replied_at"]) == T1.replace(tzinfo=None)
    assert body["hr_reply_edited_at"] is None
    assert body["severity"] == "crisis"
    assert body["ai_summary"] == "a de-identified summary"
    for forbidden in (
        "transcript",
        "device_token_hash",
        "hr_reply_by",
        "reviewed_by",
        "acknowledged_by",
    ):
        assert forbidden not in body
    assert DEFAULT_TRANSCRIPT not in response.text
    assert DEFAULT_DEVICE_HASH not in response.text


# ── Audit trail ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_write_records_who_wrote_it_and_never_what(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    author, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)

    # Act
    await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": REPLY_TEXT}
    )

    # Assert
    (event,) = await _audit_rows(db_session)
    assert event.action == REPLY_ACTION
    assert event.actor_user_id == author.id
    assert event.target_confession_id == confession.id
    assert event.content_tier == "summary"
    assert event.justification is None
    assert event.source_ip == TEST_CLIENT_HOST
    for column in AuditEvent.__table__.columns:
        assert REPLY_TEXT not in str(getattr(event, column.key))


@pytest.mark.asyncio
async def test_overwrite_by_a_second_author_is_audited_under_that_author(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    first, first_headers = await make_staff(UserRole.hr, "first-hr@example.com")
    second, second_headers = await make_staff(UserRole.hr, "second-hr@example.com")
    confession = await add_confession(db_session)
    path = _reply_path(confession.id)
    await api_client.put(path, headers=first_headers, json={"reply": "first words"})

    # Act
    await api_client.put(path, headers=second_headers, json={"reply": "second words"})

    # Assert
    events = await _audit_rows(db_session, REPLY_ACTION)
    assert {event.actor_user_id for event in events} == {first.id, second.id}
    assert len(events) == 2
    assert await _stored_reply(db_session, confession.id) == "second words"


@pytest.mark.asyncio
async def test_identical_resave_is_audited_as_a_read_and_moves_no_timestamp(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)
    path = _reply_path(confession.id)
    _freeze_clock(T1)
    await api_client.put(path, headers=headers, json={"reply": REPLY_TEXT})
    _freeze_clock(T2)

    # Act
    response = await api_client.put(path, headers=headers, json={"reply": REPLY_TEXT})

    # Assert
    body = response.json()
    reads = await _audit_rows(db_session, "confession.read")
    assert response.status_code == 200
    assert len(await _audit_rows(db_session, REPLY_ACTION)) == 1
    assert [(read.content_tier, read.target_confession_id) for read in reads] == [
        ("summary", confession.id)
    ]
    assert _parse(body["hr_replied_at"]) == T1.replace(tzinfo=None)
    assert body["hr_reply_edited_at"] is None


@pytest.mark.asyncio
async def test_changed_text_reports_the_edit_time_and_keeps_the_first_reply_time(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)
    path = _reply_path(confession.id)
    _freeze_clock(T1)
    await api_client.put(path, headers=headers, json={"reply": "first words"})
    _freeze_clock(T2)

    # Act
    response = await api_client.put(path, headers=headers, json={"reply": "second"})

    # Assert
    body = response.json()
    assert body["hr_reply"] == "second"
    assert _parse(body["hr_replied_at"]) == T1.replace(tzinfo=None)
    assert _parse(body["hr_reply_edited_at"]) == T2.replace(tzinfo=None)


# ── Rejections write nothing ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"reply": ""},
        {"reply": "   "},
        {"reply": "a" * 2001},
        {"reply": "a\u0000b"},
        {},
    ],
    ids=["empty", "blank", "too-long", "nul", "missing"],
)
async def test_invalid_reply_is_rejected_and_leaves_the_stored_reply_alone(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    body: dict[str, str],
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(
        db_session, hr_reply="existing reply", hr_replied_at=T1
    )

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json=body
    )

    # Assert
    assert response.status_code == 422
    assert await _audit_rows(db_session) == []
    assert await _stored_reply(db_session, confession.id) == "existing reply"


@pytest.mark.asyncio
async def test_service_rejection_never_echoes_the_submitted_text(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)

    # Act
    response = await api_client.put(
        _reply_path(confession.id),
        headers=headers,
        json={"reply": "leaky-marker\u0000tail"},
    )

    # Assert
    assert response.status_code == 422
    assert response.json() == {"detail": "reply must not contain NUL characters"}
    assert "leaky-marker" not in response.text


@pytest.mark.asyncio
async def test_blank_reply_gets_the_fixed_blank_message(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": " \n\t "}
    )

    # Assert
    assert response.status_code == 422
    assert response.json() == {"detail": "reply must not be blank"}


@pytest.mark.asyncio
async def test_unknown_confession_gets_404_and_no_write_audit(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.put(
        _reply_path(uuid.uuid4()), headers=headers, json={"reply": REPLY_TEXT}
    )

    # Assert
    assert response.status_code == 404
    assert await _audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_deleted_confession_gets_404_and_no_reply(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session, status=ConfessionStatus.deleted)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": REPLY_TEXT}
    )

    # Assert
    assert response.status_code == 404
    assert await _audit_rows(db_session, REPLY_ACTION) == []
    assert await _stored_reply(db_session, confession.id) is None


@pytest.mark.asyncio
async def test_flagged_confession_gets_409_and_no_reply(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — a held item may still be rejected, which would erase the reply
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    response = await api_client.put(
        _reply_path(confession.id), headers=headers, json={"reply": REPLY_TEXT}
    )

    # Assert
    assert response.status_code == 409
    assert REPLY_TEXT not in response.text
    assert await _audit_rows(db_session) == []
    assert await _stored_reply(db_session, confession.id) is None


# ── Listing by reply state ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_labels"),
    [
        ("?replied=true", {"replied"}),
        ("?replied=false", {"open_one", "open_two"}),
        ("", {"replied", "open_one", "open_two"}),
    ],
    ids=["replied", "needs-reply", "all"],
)
async def test_list_filters_by_reply_state(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    query: str,
    expected_labels: set[str],
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    seeded = {
        "replied": await add_confession(db_session, hr_reply="x", hr_replied_at=T1),
        "open_one": await add_confession(db_session),
        "open_two": await add_confession(db_session, status=ConfessionStatus.forwarded),
    }
    expected_ids = {str(seeded[label].id) for label in expected_labels}

    # Act
    response = await api_client.get(f"{HR_PATH}{query}", headers=headers)

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == len(expected_ids)
    assert {item["id"] for item in body["items"]} == expected_ids


@pytest.mark.asyncio
async def test_list_items_carry_the_reply_and_severity_but_no_private_fields(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await add_confession(
        db_session,
        severity="harassment",
        hr_reply=REPLY_TEXT,
        hr_replied_at=T1,
    )

    # Act
    response = await api_client.get(HR_PATH, headers=headers)

    # Assert
    item = response.json()["items"][0]
    assert item["hr_reply"] == REPLY_TEXT
    assert item["severity"] == "harassment"
    assert "transcript" not in item
    assert "device_token_hash" not in item
    assert DEFAULT_DEVICE_HASH not in response.text
