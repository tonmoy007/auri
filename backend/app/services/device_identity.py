"""Turning the code a phone sends into the value the server stores.

The phone sends a SHA-256 of a token that never leaves it. Stored as sent, that
value is a bearer credential: anyone holding a copy of the database can present
it to list, forward or withdraw the phone's confessions. With
``DEVICE_HASH_PEPPER`` set the server stores ``v2:`` plus an HMAC of the value
under that secret instead, so the database alone no longer yields a working code.

Rows written before the secret was set hold the value as sent. A phone is looked
up by both forms until :func:`upgrade_legacy_rows` rewrites its rows the next time
it comes back, so turning the secret on loses nobody's history or rate limit.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from typing import cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confession import Confession
from app.models.user import AnonymousUser

STORED_PREFIX = "v2:"


def is_hardened() -> bool:
    """Return ``True`` if codes are stored hashed (a pepper is configured)."""
    return bool(settings.DEVICE_HASH_PEPPER)


def stored_code(client_value: str) -> str:
    """Return the value stored for a phone that sent *client_value*.

    Args:
        client_value: The code the phone sent (header or request body).

    Returns:
        ``v2:`` plus a hex HMAC-SHA256 of it when a pepper is set, else *client_value*.
    """
    if not is_hardened():
        return client_value
    digest = hmac.new(
        settings.DEVICE_HASH_PEPPER.encode(), client_value.encode(), hashlib.sha256
    ).hexdigest()
    return f"{STORED_PREFIX}{digest}"


def _could_be_legacy(client_value: str) -> bool:
    """Whether *client_value* may be matched against rows stored as sent.

    A value that already looks like a stored one is never a legacy code. Without
    this, presenting a value copied out of the database would match its own row as
    "a code stored as sent", which is exactly the credential this removes.
    """
    return not client_value.startswith(STORED_PREFIX)


def lookup_codes(client_value: str) -> tuple[str, ...]:
    """Return the stored forms a phone sending *client_value* may own rows under.

    Returns:
        The hashed form first, then the value as sent (rows not yet upgraded);
        only the value as sent when no pepper is set; and never a legacy form for
        a value that looks already stored.
    """
    stored = stored_code(client_value)
    if stored == client_value:
        return (client_value,)
    if _could_be_legacy(client_value):
        return (stored, client_value)
    return (stored,)


@dataclass(frozen=True)
class UpgradeCounts:
    """How many rows an upgrade rewrote, by table."""

    confessions: int
    devices: int


async def _upgrade_one(session: AsyncSession, client_value: str) -> UpgradeCounts:
    """Rewrite the rows stored as *client_value* to its hashed form.

    ``updated_at`` is set to itself: the retention clock and the Telegram delivery
    dedupe key both run on it, and an upgrade is not a change to the confession.
    When the phone already has a hashed device record (it submitted after the secret
    was set, before an upgrade ran) the two are merged, keeping the most recent
    submission time and the total count so the rate limit is never loosened.
    """
    hashed = stored_code(client_value)

    moved = cast(
        CursorResult,
        await session.execute(
            update(Confession)
            .where(Confession.device_token_hash == client_value)
            .values(device_token_hash=hashed, updated_at=Confession.updated_at)
            .execution_options(synchronize_session=False)
        ),
    ).rowcount

    legacy = (
        await session.execute(
            select(AnonymousUser).where(AnonymousUser.device_token_hash == client_value)
        )
    ).scalar_one_or_none()
    if legacy is None:
        return UpgradeCounts(confessions=moved or 0, devices=0)

    existing = (
        await session.execute(
            select(AnonymousUser).where(AnonymousUser.device_token_hash == hashed)
        )
    ).scalar_one_or_none()
    if existing is None:
        await session.execute(
            update(AnonymousUser)
            .where(AnonymousUser.id == legacy.id)
            .values(device_token_hash=hashed, updated_at=AnonymousUser.updated_at)
            .execution_options(synchronize_session=False)
        )
    else:
        latest = max(legacy.last_confession_at, existing.last_confession_at)
        total = legacy.confession_count + existing.confession_count
        await session.execute(
            update(AnonymousUser)
            .where(AnonymousUser.id == existing.id)
            .values(
                last_confession_at=latest,
                confession_count=total,
                updated_at=AnonymousUser.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            delete(AnonymousUser)
            .where(AnonymousUser.id == legacy.id)
            .execution_options(synchronize_session=False)
        )
    return UpgradeCounts(confessions=moved or 0, devices=1)


async def upgrade_legacy_rows(session: AsyncSession, client_value: str) -> None:
    """Rewrite this phone's rows, stored as sent, to the hashed form.

    A no-op without a pepper and for a value that looks already stored.

    Args:
        session: Active database session (the caller commits).
        client_value: The code the phone sent.
    """
    if not is_hardened() or not _could_be_legacy(client_value):
        return
    await _upgrade_one(session, client_value)


async def upgrade_all_legacy_rows(session: AsyncSession) -> UpgradeCounts:
    """Rewrite every row still stored as sent, for phones that may never return.

    The as-sent value is the phone's code, so it can be hashed without the phone.
    Run after setting ``DEVICE_HASH_PEPPER``; safe to repeat.

    Args:
        session: Active database session (the caller commits).

    Returns:
        How many confession rows and device records were rewritten.
    """
    if not is_hardened():
        return UpgradeCounts(confessions=0, devices=0)
    legacy_values: set[str] = set()
    for column in (Confession.device_token_hash, AnonymousUser.device_token_hash):
        rows = await session.execute(
            select(column).where(~column.startswith(STORED_PREFIX)).distinct()
        )
        legacy_values.update(rows.scalars().all())
    confessions = devices = 0
    for value in sorted(legacy_values):
        counts = await _upgrade_one(session, value)
        confessions += counts.confessions
        devices += counts.devices
    return UpgradeCounts(confessions=confessions, devices=devices)


async def _main() -> None:
    """CLI: ``python -m app.services.device_identity`` upgrades every old row."""
    from app.database import async_session_factory

    if not is_hardened():
        raise SystemExit("DEVICE_HASH_PEPPER is not set; nothing to upgrade.")
    async with async_session_factory() as session:
        counts = await upgrade_all_legacy_rows(session)
        await session.commit()
    print(
        f"upgraded {counts.confessions} confession rows and {counts.devices} device records"
    )


if __name__ == "__main__":
    asyncio.run(_main())
