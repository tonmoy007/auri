"""Tests for the crisis escalation path.

The point of splitting moderation severity is that a self-harm disclosure
must not queue behind a policy flag, and must never be auto-delivered to a
department chat before a person has seen it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.models.audit_event import AuditEvent
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.user import UserRole
from app.services.llm import LLMService
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import StaffFactory

QUEUE_PATH = "/api/v1/moderation/queue"
DELIVERY_QUEUE_PATH = "/api/v1/delivery/queue"
OLDER = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


async def _add(
    session: AsyncSession,
    severity: ModerationSeverity,
    status: ConfessionStatus = ConfessionStatus.flagged,
    created_at: datetime = OLDER,
    department: str | None = None,
    acknowledged_at: datetime | None = None,
) -> Confession:
    confession = Confession(
        device_token_hash="a" * 32,
        voice_mask="warm",
        transcript="what someone said",
        ai_summary="a summary",
        category="work",
        sentiment="negative",
        pii_stripped=True,
        status=status,
        severity=severity.value,
        recipient_dept=department,
        created_at=created_at,
        acknowledged_at=acknowledged_at,
    )
    session.add(confession)
    await session.commit()
    return confession


# ── Severity classification ──────────────────────────────────────────────


def test_moderate_returns_the_classified_severity() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act
    with patch.object(LLMService, "_call_openai", return_value=" Crisis \n"):
        severity = service.moderate("i want to hurt myself")

    # Assert
    assert severity is ModerationSeverity.crisis


def test_moderate_fails_closed_to_policy_not_crisis() -> None:
    # Arrange — an unparseable answer must hold the item for review, but
    # crying crisis on every model hiccup would train reviewers to ignore
    # the banner
    service = LLMService(provider="openai")

    # Act
    with patch.object(LLMService, "_call_openai", return_value="i'm not sure"):
        severity = service.moderate("some text")

    # Assert
    assert severity is ModerationSeverity.policy


def test_moderate_recognises_a_clean_confession() -> None:
    # Arrange
    service = LLMService(provider="openai")

    # Act
    with patch.object(LLMService, "_call_openai", return_value="none"):
        severity = service.moderate("the coffee machine is broken again")

    # Assert
    assert severity is ModerationSeverity.none


# ── Queue ordering ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crisis_items_sort_ahead_of_older_policy_items(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the policy item is older, so age alone would put it first
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")
    await _add(db_session, ModerationSeverity.policy, created_at=OLDER)
    crisis = await _add(
        db_session, ModerationSeverity.crisis, created_at=OLDER + timedelta(days=2)
    )

    # Act
    queue = (await api_client.get(QUEUE_PATH, headers=headers)).json()

    # Assert
    assert queue[0]["id"] == str(crisis.id)
    assert queue[0]["severity"] == "crisis"


# ── Acknowledgement ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acknowledging_a_crisis_item_records_actor_and_time(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    actor, headers = await make_staff(UserRole.moderator, "mod@example.com")
    crisis = await _add(db_session, ModerationSeverity.crisis)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{crisis.id}/acknowledge", headers=headers
    )

    # Assert
    await db_session.refresh(crisis)
    assert response.status_code == 200
    assert crisis.acknowledged_by == actor.id
    assert crisis.acknowledged_at is not None


@pytest.mark.asyncio
async def test_acknowledgement_is_audited(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")
    crisis = await _add(db_session, ModerationSeverity.crisis)

    # Act
    await api_client.post(
        f"/api/v1/moderation/{crisis.id}/acknowledge", headers=headers
    )

    # Assert
    event = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "crisis.acknowledge")
            )
        )
        .scalars()
        .one()
    )
    assert event.target_confession_id == crisis.id


@pytest.mark.asyncio
async def test_the_anonymous_bot_key_cannot_acknowledge(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — acknowledgement answers "has a human seen this?", so it
    # requires a human, not a shared secret
    set_setting("MODERATION_API_KEY", "bot-secret")
    crisis = await _add(db_session, ModerationSeverity.crisis)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{crisis.id}/acknowledge",
        headers={"X-Moderation-Api-Key": "bot-secret"},
    )

    # Assert
    await db_session.refresh(crisis)
    assert response.status_code == 403
    assert crisis.acknowledged_at is None


@pytest.mark.asyncio
async def test_acknowledging_a_non_crisis_item_is_refused(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")
    policy_item = await _add(db_session, ModerationSeverity.policy)

    # Act
    response = await api_client.post(
        f"/api/v1/moderation/{policy_item.id}/acknowledge", headers=headers
    )

    # Assert
    assert response.status_code == 409


# ── No auto-delivery of crisis items ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unacknowledged_crisis_item_never_enters_the_delivery_queue(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — handing "I want to hurt myself" to a department chat
    # unattended is the exact failure this guard exists to stop
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    await _add(
        db_session,
        ModerationSeverity.crisis,
        status=ConfessionStatus.forwarded,
        department="HR",
    )

    # Act
    response = await api_client.get(
        DELIVERY_QUEUE_PATH, headers={"X-Delivery-Api-Key": "delivery-secret"}
    )

    # Assert
    assert response.json() == []


@pytest.mark.asyncio
async def test_acknowledged_crisis_item_becomes_deliverable(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    await _add(
        db_session,
        ModerationSeverity.crisis,
        status=ConfessionStatus.forwarded,
        department="HR",
        acknowledged_at=OLDER,
    )

    # Act
    response = await api_client.get(
        DELIVERY_QUEUE_PATH, headers={"X-Delivery-Api-Key": "delivery-secret"}
    )

    # Assert
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_ordinary_items_are_unaffected_by_the_crisis_guard(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    await _add(
        db_session,
        ModerationSeverity.none,
        status=ConfessionStatus.forwarded,
        department="HR",
    )

    # Act
    response = await api_client.get(
        DELIVERY_QUEUE_PATH, headers={"X-Delivery-Api-Key": "delivery-secret"}
    )

    # Assert
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_marking_an_unacknowledged_crisis_item_delivered_is_refused(
    api_client: AsyncClient, db_session: AsyncSession, set_setting
) -> None:
    # Arrange — the guard must hold on the write path too, not just the list
    set_setting("DELIVERY_API_KEY", "delivery-secret")
    crisis = await _add(
        db_session,
        ModerationSeverity.crisis,
        status=ConfessionStatus.forwarded,
        department="HR",
    )

    # Act
    response = await api_client.post(
        f"/api/v1/delivery/{crisis.id}/delivered",
        headers={"X-Delivery-Api-Key": "delivery-secret"},
    )

    # Assert
    assert response.status_code == 404


# ── Raw access for crisis items ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approved_crisis_item_stays_raw_readable(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — a welfare follow-up should not be blocked because the item
    # was released back into the normal flow
    _, headers = await make_staff(UserRole.hr)
    crisis = await _add(
        db_session, ModerationSeverity.crisis, status=ConfessionStatus.pending
    )

    # Act
    response = await api_client.post(
        f"/api/v1/hr/confessions/{crisis.id}/raw",
        headers=headers,
        json={"justification": "following up on a crisis disclosure"},
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["transcript"] == "what someone said"
