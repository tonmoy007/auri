"""Tests for tiered HR confession access and its audit trail.

The access rules are domain logic, so nothing here is mocked: real queries,
real role checks, real audit writes (AGENTS.md §16.4).
"""

from __future__ import annotations

import uuid

import pytest
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from app.services import confession_access
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

HR_PATH = "/api/v1/hr/confessions"
GOOD_REASON = "moderator escalated this for a welfare check"


async def _add_confession(
    session: AsyncSession,
    status: ConfessionStatus = ConfessionStatus.pending,
    transcript: str = "the original words a person spoke",
    category: str = "work",
    sentiment: str = "negative",
    department: str | None = None,
) -> Confession:
    confession = Confession(
        device_token_hash="a" * 32,
        voice_mask="warm",
        transcript=transcript,
        ai_summary="a de-identified summary",
        category=category,
        sentiment=sentiment,
        pii_stripped=True,
        status=status,
        recipient_dept=department,
    )
    session.add(confession)
    await session.commit()
    return confession


# ── Summary tier ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_list_never_returns_the_transcript(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_confession(db_session, transcript="a very specific private detail")

    # Act
    response = await api_client.get(HR_PATH, headers=headers)

    # Assert
    assert response.status_code == 200
    assert "a very specific private detail" not in response.text
    assert "transcript" not in response.json()["items"][0]


@pytest.mark.asyncio
async def test_summary_list_returns_summary_category_and_sentiment(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_confession(db_session)

    # Act
    item = (await api_client.get(HR_PATH, headers=headers)).json()["items"][0]

    # Assert
    assert item["ai_summary"] == "a de-identified summary"
    assert item["category"] == "work"
    assert item["sentiment"] == "negative"


@pytest.mark.asyncio
async def test_summary_list_excludes_confessions_the_confessor_deleted(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — a withdrawn confession must not resurface in HR reporting
    _, headers = await make_staff(UserRole.hr)
    await _add_confession(db_session, status=ConfessionStatus.deleted)

    # Act
    body = (await api_client.get(HR_PATH, headers=headers)).json()

    # Assert
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_summary_list_filters_by_department(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    await _add_confession(db_session, department="HR")
    await _add_confession(db_session, department="Engineering")

    # Act
    body = (await api_client.get(f"{HR_PATH}?department=HR", headers=headers)).json()

    # Assert
    assert body["total"] == 1
    assert body["items"][0]["recipient_dept"] == "HR"


@pytest.mark.asyncio
async def test_summary_read_writes_a_summary_tier_audit_event(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(db_session)

    # Act
    await api_client.get(f"{HR_PATH}/{confession.id}", headers=headers)

    # Assert
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    read_event = next(e for e in events if e.target_confession_id == confession.id)
    assert read_event.content_tier == "summary"
    assert read_event.actor_user_id == actor.id


@pytest.mark.asyncio
async def test_summary_read_404s_for_an_unknown_confession(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(f"{HR_PATH}/{uuid.uuid4()}", headers=headers)

    # Assert
    assert response.status_code == 404


# ── Raw tier ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_read_returns_the_transcript_for_a_flagged_confession(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(
        db_session, status=ConfessionStatus.flagged, transcript="the original words"
    )

    # Act
    response = await api_client.post(
        f"{HR_PATH}/{confession.id}/raw",
        headers=headers,
        json={"justification": GOOD_REASON},
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["transcript"] == "the original words"


@pytest.mark.asyncio
async def test_raw_read_is_refused_for_an_ordinary_pending_confession(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — only escalated items expose their original text
    _, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(db_session, status=ConfessionStatus.pending)

    # Act
    response = await api_client.post(
        f"{HR_PATH}/{confession.id}/raw",
        headers=headers,
        json={"justification": GOOD_REASON},
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_raw_read_is_refused_without_a_meaningful_justification(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    response = await api_client.post(
        f"{HR_PATH}/{confession.id}/raw", headers=headers, json={"justification": "ok"}
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_refused_raw_read_writes_no_audit_event_and_leaks_nothing(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(
        db_session, status=ConfessionStatus.pending, transcript="private words"
    )

    # Act
    response = await api_client.post(
        f"{HR_PATH}/{confession.id}/raw",
        headers=headers,
        json={"justification": GOOD_REASON},
    )

    # Assert
    assert "private words" not in response.text
    raw_events = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.content_tier == "raw")
            )
        )
        .scalars()
        .all()
    )
    assert raw_events == []


@pytest.mark.asyncio
async def test_raw_read_records_the_justification_verbatim(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.hr)
    confession = await _add_confession(db_session, status=ConfessionStatus.flagged)

    # Act
    await api_client.post(
        f"{HR_PATH}/{confession.id}/raw",
        headers=headers,
        json={"justification": GOOD_REASON},
    )

    # Assert
    event = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.content_tier == "raw")
            )
        )
        .scalars()
        .one()
    )
    assert event.justification == GOOD_REASON
    assert event.actor_user_id == actor.id
    assert event.target_confession_id == confession.id


# ── Role gating ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moderator_cannot_reach_hr_confession_access(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — moderators work the queue, not the HR reporting surface
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")

    # Act
    response = await api_client.get(HR_PATH, headers=headers)

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_hr_access_requires_a_session(api_client: AsyncClient) -> None:
    # Arrange / Act
    response = await api_client.get(HR_PATH)

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_legacy_admin_api_key_cannot_read_confession_content(
    api_client: AsyncClient, monkeypatch
) -> None:
    # Arrange — the shared secret has no named actor, so it can never be
    # the subject of an audit row; content access must require a session
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_API_KEY", "legacy-admin-secret")

    # Act
    response = await api_client.get(
        HR_PATH, headers={"X-Admin-Api-Key": "legacy-admin-secret"}
    )

    # Assert
    assert response.status_code == 401


# ── Service-level enforcement ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_query_does_not_fetch_the_transcript_column(
    db_session: AsyncSession,
) -> None:
    # Arrange — enforcement is in the query, not the response model
    await _add_confession(db_session, transcript="never selected")

    # Act
    views, _ = await confession_access.list_summaries(db_session)

    # Assert
    assert not hasattr(views[0], "transcript")
