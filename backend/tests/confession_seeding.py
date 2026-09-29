"""Seed confession rows directly for the HR reply test modules.

The reply tests need rows in very specific states (already replied, held for
moderation, stamped with a reviewer, an explicit ``updated_at``), which no
public route can produce on demand. Writing them straight to the database
keeps each test's arrangement explicit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.confession import Confession, ConfessionStatus
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_DEVICE_HASH = "device-hash-of-the-confessor-0001"
DEFAULT_TRANSCRIPT = "the original words a person spoke"


async def add_confession(
    session: AsyncSession,
    status: ConfessionStatus = ConfessionStatus.pending,
    transcript: str = DEFAULT_TRANSCRIPT,
    device_token_hash: str = DEFAULT_DEVICE_HASH,
    severity: str = "none",
    department: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    hr_reply: str | None = None,
    hr_replied_at: datetime | None = None,
    hr_reply_edited_at: datetime | None = None,
) -> Confession:
    """Insert and commit one confession; ``None`` columns take their defaults."""
    confession = Confession(
        device_token_hash=device_token_hash,
        voice_mask="warm",
        transcript=transcript,
        ai_summary="a de-identified summary",
        category="work",
        sentiment="negative",
        pii_stripped=True,
        status=status,
        severity=severity,
        recipient_dept=department,
        hr_reply=hr_reply,
        hr_replied_at=hr_replied_at,
        hr_reply_edited_at=hr_reply_edited_at,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(confession)
    await session.commit()
    return confession


async def stamp_moderation(
    session: AsyncSession, confession: Confession, staff_id: uuid.UUID, at: datetime
) -> None:
    """Record *staff_id* as both reviewer and acknowledger, as moderation would."""
    confession.reviewed_by = staff_id
    confession.reviewed_at = at
    confession.acknowledged_by = staff_id
    confession.acknowledged_at = at
    await session.commit()
