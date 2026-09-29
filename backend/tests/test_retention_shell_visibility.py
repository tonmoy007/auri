"""Tests that a reply-only shell is invisible to staff and delivery, not to its confessor.

A shell is what retention leaves of a replied confession: the reply and the
device hash, nothing describing the confession. Staff surfaces, reports and the
delivery queue must ignore it (counting blanks would skew Insights, and
re-delivering one would send an empty message), while the confessor's own
device must still be able to read the reply. Shells are made by running the
real retention job over seeded rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.models.confession import Confession, ConfessionStatus
from app.models.user import UserRole
from app.services import settings_service
from app.services.retention import run_retention
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import add_confession
from tests.conftest import SettingPatcher, StaffFactory

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
DEVICE = "device-hash-of-the-shell-owner-0001"
DELIVERY_KEY = "delivery-key-for-tests"


async def _make_shell(
    session: AsyncSession,
    delivered: bool = False,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Seed a stale replied forwarded confession, run retention, return its id."""
    confession = await add_confession(
        session,
        status=ConfessionStatus.forwarded,
        device_token_hash=DEVICE,
        department="HR",
        created_at=created_at,
        updated_at=NOW - timedelta(hours=30),
        hr_reply="we heard you",
        hr_replied_at=NOW - timedelta(hours=2),
        delivered_at=NOW - timedelta(hours=29) if delivered else None,
    )
    shell_id = confession.id
    await run_retention(session, NOW, 24, 30)
    await session.commit()
    session.expire_all()
    return shell_id


async def _live_pending(session: AsyncSession, count: int = 1) -> None:
    for index in range(count):
        await add_confession(
            session,
            device_token_hash=f"live-{index:028d}",
            created_at=NOW - timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_the_hr_list_and_read_do_not_show_a_shell(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    shell = await _make_shell(db_session)
    await _live_pending(db_session)

    # Act
    listing = (await api_client.get("/api/v1/hr/confessions", headers=headers)).json()
    single = await api_client.get(f"/api/v1/hr/confessions/{shell}", headers=headers)

    # Assert
    assert listing["total"] == 1
    assert str(shell) not in [item["id"] for item in listing["items"]]
    assert single.status_code == 404


@pytest.mark.asyncio
async def test_hr_cannot_write_or_edit_a_reply_on_a_shell(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — the confession is gone; there is nothing left to reply to
    _, headers = await make_staff(UserRole.hr)
    shell = await _make_shell(db_session)

    # Act
    response = await api_client.put(
        f"/api/v1/hr/confessions/{shell}/reply",
        json={"reply": "an edit"},
        headers=headers,
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_insights_do_not_count_a_shell(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("ANALYTICS_MIN_COHORT", 2)
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)
    _, headers = await make_staff(UserRole.hr)
    await _make_shell(db_session)
    await _live_pending(db_session, count=3)
    since = (NOW - timedelta(days=10)).isoformat()

    # Act
    response = await api_client.get(
        "/api/v1/hr/insights",
        params={"since": since, "until": NOW.isoformat()},
        headers=headers,
    )

    # Assert
    assert response.json()["total"] == {
        "label": "total",
        "count": 3,
        "suppressed": False,
    }


@pytest.mark.asyncio
async def test_an_undelivered_shell_is_not_offered_to_the_bot(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — its content is gone, so delivering it would send an empty message
    set_setting("DELIVERY_API_KEY", DELIVERY_KEY)
    shell = await _make_shell(db_session, delivered=False)

    # Act
    queue = await api_client.get(
        "/api/v1/delivery/queue", headers={"X-Delivery-Api-Key": DELIVERY_KEY}
    )

    # Assert
    assert str(shell) not in [item["id"] for item in queue.json()]


@pytest.mark.asyncio
async def test_the_delivery_overview_and_resend_ignore_a_shell(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    shell = await _make_shell(db_session, delivered=True)

    # Act
    overview = await api_client.get("/api/v1/delivery/overview", headers=headers)
    resend = await api_client.post(f"/api/v1/delivery/{shell}/resend", headers=headers)

    # Assert
    assert str(shell) not in [item["id"] for item in overview.json()]
    assert resend.status_code == 404


@pytest.mark.asyncio
async def test_the_confessor_still_reads_the_reply_on_their_emptied_confession(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    shell = await _make_shell(db_session)

    # Act
    items = (
        await api_client.get(
            "/api/v1/confessions", headers={"X-Device-Token-Hash": DEVICE}
        )
    ).json()

    # Assert
    assert [item["id"] for item in items] == [str(shell)]
    assert items[0]["hr_reply"] == "we heard you"
    assert items[0]["transcript"] == ""
    assert items[0]["ai_summary"] is None
    assert items[0]["purged_at"] is not None


@pytest.mark.asyncio
async def test_another_device_cannot_read_a_shell(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    shell = await _make_shell(db_session)

    # Act
    response = await api_client.get(
        f"/api/v1/confessions/{shell}",
        headers={"X-Device-Token-Hash": "some-other-device-hash-000000001"},
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_confessor_who_deletes_their_shell_has_it_removed_by_retention(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    shell = await _make_shell(db_session)
    await api_client.delete(
        f"/api/v1/confessions/{shell}", headers={"X-Device-Token-Hash": DEVICE}
    )
    # The DELETE stamps updated_at from the real clock, so measure the next
    # run from the stored value rather than from the frozen NOW.
    stored = (
        await db_session.execute(
            select(Confession.updated_at).where(Confession.id == shell)
        )
    ).scalar_one()
    later = stored.replace(tzinfo=timezone.utc) + timedelta(hours=25)

    # Act — the row is now 'deleted'; a later run removes it and its reply
    await run_retention(db_session, later, 24, 30)
    await db_session.commit()

    # Assert
    remaining = (await db_session.execute(select(Confession))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_a_raw_read_of_a_shell_is_not_found_rather_than_refused(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — "forbidden" would confirm something is there to be forbidden
    _, headers = await make_staff(UserRole.hr)
    shell = await _make_shell(db_session)

    # Act
    response = await api_client.post(
        f"/api/v1/hr/confessions/{shell}/raw",
        json={"justification": "a sufficiently long stated reason"},
        headers=headers,
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_insights_breakdowns_and_trend_ignore_a_shell(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange — the shell has no category or sentiment and sits in an earlier
    # week, so counting it would add an "unlabelled" bucket and an extra week
    set_setting("ANALYTICS_MIN_COHORT", 2)
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)
    _, headers = await make_staff(UserRole.hr)
    await _make_shell(db_session, created_at=NOW - timedelta(days=9))
    await _live_pending(db_session, count=3)

    # Act
    body = (
        await api_client.get(
            "/api/v1/hr/insights",
            params={
                "since": (NOW - timedelta(days=12)).isoformat(),
                "until": NOW.isoformat(),
            },
            headers=headers,
        )
    ).json()

    # Assert
    assert [b["label"] for b in body["by_category"]] == ["work"]
    assert [b["label"] for b in body["by_sentiment"]] == ["negative"]
    assert len(body["sentiment_trend"]) == 1


@pytest.mark.asyncio
async def test_the_bot_cannot_mark_a_shell_delivered(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DELIVERY_API_KEY", DELIVERY_KEY)
    shell = await _make_shell(db_session, delivered=False)

    # Act
    response = await api_client.post(
        f"/api/v1/delivery/{shell}/delivered",
        headers={"X-Delivery-Api-Key": DELIVERY_KEY},
    )

    # Assert
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_confession_that_is_still_whole_reports_no_purge_time(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    await add_confession(db_session, device_token_hash=DEVICE)

    # Act
    items = (
        await api_client.get(
            "/api/v1/confessions", headers={"X-Device-Token-Hash": DEVICE}
        )
    ).json()

    # Assert
    assert items[0]["purged_at"] is None
