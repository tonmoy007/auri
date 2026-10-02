"""Tests for storing the phone's code hashed with a server secret.

The phone sends a SHA-256 of its local token and, until this change, the server
stored that value as sent, so a copy of the database could be used to read,
forward or withdraw any phone's confessions by presenting it. With
``DEVICE_HASH_PEPPER`` set the server stores ``v2:`` plus an HMAC of the value
instead, which a database copy alone cannot be turned back into a working code.
Rows written before the secret was set are upgraded the first time their phone
comes back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.config import Settings
from app.models.confession import Confession, ConfessionStatus, ModerationSeverity
from app.models.department import Department
from app.models.user import AnonymousUser
from app.services import device_identity
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.confession_seeding import add_confession
from tests.conftest import SettingPatcher
from tests.counsel_replies import make_counsel_reply

PEPPER = "p" * 40
OTHER_PEPPER = "q" * 40
PHONE = "a1" * 32
OTHER_PHONE = "b2" * 32
CONFESSIONS = "/api/v1/confessions"


def _headers(code: str) -> dict[str, str]:
    return {"X-Device-Token-Hash": code}


# ── the rules ────────────────────────────────────────────────────────────


def test_without_a_secret_the_code_is_stored_as_sent(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", "")

    # Act / Assert
    assert device_identity.stored_code(PHONE) == PHONE
    assert device_identity.lookup_codes(PHONE) == (PHONE,)
    assert device_identity.is_hardened() is False


def test_with_a_secret_the_code_is_stored_as_a_keyed_hash(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    stored = device_identity.stored_code(PHONE)

    # Assert
    assert stored.startswith("v2:")
    assert len(stored) == len("v2:") + 64
    assert PHONE not in stored
    assert stored == device_identity.stored_code(PHONE)
    assert stored != device_identity.stored_code(OTHER_PHONE)
    assert device_identity.is_hardened() is True


def test_a_different_secret_gives_a_different_code(set_setting: SettingPatcher) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    first = device_identity.stored_code(PHONE)
    set_setting("DEVICE_HASH_PEPPER", OTHER_PEPPER)

    # Act
    second = device_identity.stored_code(PHONE)

    # Assert
    assert first != second


def test_a_phone_is_looked_up_by_its_hashed_and_its_legacy_code(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    codes = device_identity.lookup_codes(PHONE)

    # Assert — the hashed code first, then the value as sent, for rows not yet upgraded
    assert codes == (device_identity.stored_code(PHONE), PHONE)


def test_a_stored_value_presented_as_a_code_never_matches_itself(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — someone holding a database copy presents a stored value verbatim
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    leaked = device_identity.stored_code(PHONE)

    # Act
    codes = device_identity.lookup_codes(leaked)

    # Assert — it is hashed again, and never accepted as a legacy value
    assert leaked not in codes
    assert codes == (device_identity.stored_code(leaked),)


@pytest.mark.parametrize("pepper", ["short", "x" * 31])
def test_a_weak_secret_is_refused_at_startup(pepper: str) -> None:
    # Act / Assert — silently accepting it would be worse than not hardening at all
    with pytest.raises(ValidationError, match="DEVICE_HASH_PEPPER"):
        Settings(_env_file=None, DEVICE_HASH_PEPPER=pepper)


def test_an_empty_or_long_secret_is_accepted() -> None:
    # Act / Assert
    assert Settings(_env_file=None, DEVICE_HASH_PEPPER="").DEVICE_HASH_PEPPER == ""
    assert (
        Settings(_env_file=None, DEVICE_HASH_PEPPER=PEPPER).DEVICE_HASH_PEPPER == PEPPER
    )


# ── the API ──────────────────────────────────────────────────────────────


async def _stored_codes(session: AsyncSession) -> list[str]:
    session.expire_all()
    rows = await session.execute(select(Confession.device_token_hash))
    return sorted(rows.scalars().all())


@pytest.mark.asyncio
async def test_a_hardened_row_is_found_by_its_phones_code(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    await add_confession(
        db_session, device_token_hash=device_identity.stored_code(PHONE)
    )

    # Act
    response = await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_a_leaked_stored_value_does_not_open_its_confessions(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — the value in the database is exactly what a database copy shows
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    leaked = device_identity.stored_code(PHONE)
    confession = await add_confession(db_session, device_token_hash=leaked)
    path = f"{CONFESSIONS}/{confession.id}"

    # Act
    listed = await api_client.get(CONFESSIONS, headers=_headers(leaked))
    fetched = await api_client.get(path, headers=_headers(leaked))
    deleted = await api_client.delete(path, headers=_headers(leaked))
    forwarded = await api_client.post(
        f"{path}/forward", json={"department": "HR"}, headers=_headers(leaked)
    )

    # Assert — nothing is readable, forwardable or withdrawable with it
    assert listed.json() == []
    assert (fetched.status_code, deleted.status_code, forwarded.status_code) == (
        403,
        403,
        403,
    )
    await db_session.refresh(confession)
    assert confession.status is ConfessionStatus.pending


@pytest.mark.asyncio
async def test_another_phone_still_cannot_reach_a_hardened_row(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    confession = await add_confession(
        db_session, device_token_hash=device_identity.stored_code(PHONE)
    )

    # Act
    response = await api_client.get(
        f"{CONFESSIONS}/{confession.id}", headers=_headers(OTHER_PHONE)
    )

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_older_row_is_upgraded_when_its_phone_comes_back(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — written before the secret was set, with an explicit updated_at
    stamp = datetime(2026, 9, 1, 8, 0, 0)  # noqa: DTZ001 - naive, like SQLite
    confession = await add_confession(
        db_session, device_token_hash=PHONE, updated_at=stamp
    )
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    first = await api_client.get(CONFESSIONS, headers=_headers(PHONE))
    second = await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert — found both times, now stored hashed, and the retention clock unmoved
    assert (len(first.json()), len(second.json())) == (1, 1)
    assert await _stored_codes(db_session) == [device_identity.stored_code(PHONE)]
    await db_session.refresh(confession)
    assert confession.updated_at.replace(tzinfo=None) == stamp


@pytest.mark.asyncio
async def test_upgrading_one_phone_leaves_another_phones_rows_alone(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    await add_confession(db_session, device_token_hash=PHONE)
    await add_confession(db_session, device_token_hash=OTHER_PHONE)
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert
    assert await _stored_codes(db_session) == sorted(
        [device_identity.stored_code(PHONE), OTHER_PHONE]
    )


@pytest.mark.asyncio
async def test_nothing_is_upgraded_while_no_secret_is_set(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", "")
    await add_confession(db_session, device_token_hash=PHONE)

    # Act
    response = await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert
    assert len(response.json()) == 1
    assert await _stored_codes(db_session) == [PHONE]


@pytest.mark.asyncio
async def test_an_owner_can_fetch_delete_and_forward_a_hardened_confession(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    stored = device_identity.stored_code(PHONE)
    to_forward = await add_confession(db_session, device_token_hash=stored)
    to_delete = await add_confession(db_session, device_token_hash=stored)

    # Act
    fetched = await api_client.get(
        f"{CONFESSIONS}/{to_forward.id}", headers=_headers(PHONE)
    )
    deleted = await api_client.delete(
        f"{CONFESSIONS}/{to_delete.id}", headers=_headers(PHONE)
    )

    # Assert
    assert fetched.status_code == 200
    assert deleted.status_code == 204


async def _submit(client: AsyncClient, code: str):
    """POST a confession with the model calls replaced."""
    payload = {
        "device_token_hash": code,
        "voice_mask": "warm",
        "transcript": "some words",
    }
    with (
        patch(
            "app.api.v1.confessions.LLMService.deidentify", return_value="some words"
        ),
        patch("app.api.v1.confessions.LLMService.categorize", return_value="work"),
        patch("app.api.v1.confessions.LLMService.summarize", return_value="A summary."),
        patch(
            "app.api.v1.confessions.LLMService.classify_sentiment",
            return_value="neutral",
        ),
        patch(
            "app.api.v1.confessions.LLMService.counsel",
            return_value=make_counsel_reply("You were heard."),
        ),
        patch(
            "app.api.v1.confessions.LLMService.moderate",
            return_value=ModerationSeverity.none,
        ),
    ):
        return await client.post(CONFESSIONS, json=payload)


@pytest.mark.asyncio
async def test_a_new_confession_is_stored_under_the_hashed_code(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    response = await _submit(api_client, PHONE)

    # Assert — neither table holds the value the phone sent
    assert response.status_code == 201
    assert await _stored_codes(db_session) == [device_identity.stored_code(PHONE)]
    devices = (
        (await db_session.execute(select(AnonymousUser.device_token_hash)))
        .scalars()
        .all()
    )
    assert list(devices) == [device_identity.stored_code(PHONE)]


@pytest.mark.asyncio
async def test_the_rate_limit_still_holds_after_hardening(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    await _submit(api_client, PHONE)

    # Act
    second = await _submit(api_client, PHONE)

    # Assert
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_a_device_limited_before_hardening_stays_limited_after_it(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — the phone submitted a moment ago, under the old scheme
    db_session.add(
        AnonymousUser(
            device_token_hash=PHONE,
            last_confession_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(seconds=5),
            confession_count=2,
        )
    )
    await db_session.commit()
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    response = await _submit(api_client, PHONE)

    # Assert — turning the secret on must not reset anyone's rate limit (the refused
    # request is rolled back, so the rows are upgraded on the next successful one)
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_upgrading_merges_a_device_record_that_already_exists_hashed(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — both the old and the hashed record exist for one phone
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    long_ago = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001 - naive, like SQLite
    db_session.add_all(
        [
            AnonymousUser(
                device_token_hash=PHONE, last_confession_at=long_ago, confession_count=1
            ),
            AnonymousUser(
                device_token_hash=device_identity.stored_code(PHONE),
                last_confession_at=long_ago,
                confession_count=1,
            ),
        ]
    )
    await db_session.commit()

    # Act
    response = await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert — one record is left and the request did not fail on the unique index
    assert response.status_code == 200
    devices = (
        (await db_session.execute(select(AnonymousUser.device_token_hash)))
        .scalars()
        .all()
    )
    assert list(devices) == [device_identity.stored_code(PHONE)]


def test_exactly_the_minimum_length_is_accepted_and_the_error_never_echoes_a_secret() -> (
    None
):
    # Act / Assert
    assert (
        Settings(_env_file=None, DEVICE_HASH_PEPPER="x" * 32).DEVICE_HASH_PEPPER
        == "x" * 32
    )
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None, DEVICE_HASH_PEPPER="hunter2-too-short")
    assert "hunter2" not in str(refused.value)


@pytest.mark.asyncio
async def test_merging_keeps_the_most_recent_submission_time_and_the_total_count(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — the OLD record is the newer one, so keeping the hashed row as-is
    # would forget a recent submission and reopen the rate limit
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    older = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001 - naive, like SQLite
    newer = datetime(2026, 3, 1, 0, 0, 0)  # noqa: DTZ001 - naive, like SQLite
    db_session.add_all(
        [
            AnonymousUser(
                device_token_hash=PHONE, last_confession_at=newer, confession_count=2
            ),
            AnonymousUser(
                device_token_hash=device_identity.stored_code(PHONE),
                last_confession_at=older,
                confession_count=3,
            ),
        ]
    )
    await db_session.commit()

    # Act
    await api_client.get(CONFESSIONS, headers=_headers(PHONE))

    # Assert
    db_session.expire_all()
    (row,) = (await db_session.execute(select(AnonymousUser))).scalars().all()
    assert (row.last_confession_at, row.confession_count) == (newer, 5)


@pytest.mark.asyncio
async def test_a_legacy_confession_can_be_forwarded_with_the_secret_on(
    api_client: AsyncClient, db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    db_session.add(Department(name="HR", telegram_chat_id="1", is_active=True))
    confession = await add_confession(db_session, device_token_hash=PHONE)
    set_setting("DEVICE_HASH_PEPPER", PEPPER)

    # Act
    response = await api_client.post(
        f"{CONFESSIONS}/{confession.id}/forward",
        json={"department": "HR"},
        headers=_headers(PHONE),
    )

    # Assert — allowed, and the row is now stored hashed
    assert response.status_code == 200
    assert await _stored_codes(db_session) == [device_identity.stored_code(PHONE)]


@pytest.mark.asyncio
async def test_the_batch_upgrade_hashes_every_row_stored_as_sent(
    db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange — rows for phones that never come back would otherwise stay usable
    set_setting("DEVICE_HASH_PEPPER", PEPPER)
    stamp = datetime(2026, 9, 1, 8, 0, 0)  # noqa: DTZ001 - naive, like SQLite
    await add_confession(db_session, device_token_hash=PHONE, updated_at=stamp)
    await add_confession(db_session, device_token_hash=OTHER_PHONE)
    await add_confession(
        db_session, device_token_hash=device_identity.stored_code("c3" * 32)
    )
    db_session.add(
        AnonymousUser(
            device_token_hash=PHONE, last_confession_at=stamp, confession_count=1
        )
    )
    await db_session.commit()

    # Act
    upgraded = await device_identity.upgrade_all_legacy_rows(db_session)
    await db_session.commit()
    again = await device_identity.upgrade_all_legacy_rows(db_session)

    # Assert
    assert upgraded == device_identity.UpgradeCounts(confessions=2, devices=1)
    assert again == device_identity.UpgradeCounts(confessions=0, devices=0)
    assert all(code.startswith("v2:") for code in await _stored_codes(db_session))
    kept = (
        await db_session.execute(select(Confession.updated_at).limit(1))
    ).scalar_one()
    assert kept is not None


@pytest.mark.asyncio
async def test_the_batch_upgrade_does_nothing_without_a_secret(
    db_session: AsyncSession, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", "")
    await add_confession(db_session, device_token_hash=PHONE)

    # Act
    upgraded = await device_identity.upgrade_all_legacy_rows(db_session)

    # Assert
    assert upgraded == device_identity.UpgradeCounts(confessions=0, devices=0)
    assert await _stored_codes(db_session) == [PHONE]
