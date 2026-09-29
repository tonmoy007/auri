"""Writing the organisation's reply to a confession.

An HR user writes one reply per confession, on behalf of the organisation.
The reply lives on the confession row; who wrote it is recorded only in the
append-only audit trail (``hr_reply.write``), never on the row, so nothing
the confessor is served can name a staff member.

This module keeps the same promise as ``confession_access``: it never loads
the ``Confession`` entity, so the transcript and the device hash are never
read on the reply path. It also never commits — the route's session commits
the reply and its audit row together, or rolls both back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    ConfessionNotFoundError,
    HrReplyInvalidError,
    ReplyNotPermittedError,
)
from app.models.confession import Confession, ConfessionStatus
from app.services import confession_access

MAX_HR_REPLY_LENGTH: Final = 2000

# A flagged item is still awaiting moderation and may be rejected, which
# soft-deletes it and would silently take a reply with it. Deleted rows are
# not visible at all, so they never reach this check.
REPLY_ELIGIBLE_STATUSES: tuple[ConfessionStatus, ...] = (
    ConfessionStatus.pending,
    ConfessionStatus.forwarded,
)


@dataclass(frozen=True)
class ReplyWriteResult:
    """The summary after a write, and whether the write changed anything."""

    view: confession_access.ConfessionSummaryView
    changed: bool


def normalize_reply(text: str) -> str:
    """Return *text* stripped, or raise if it cannot be stored as a reply.

    Length is counted in Python code points after stripping, so padding never
    counts against the limit. No message here ever contains the text: the
    reply is confessor-directed prose and must not leak into error bodies.

    Raises:
        HrReplyInvalidError: If the stripped text is empty, longer than
            ``MAX_HR_REPLY_LENGTH``, or contains a NUL character (which
            Postgres refuses to store).
    """
    stripped = text.strip()
    if not stripped:
        raise HrReplyInvalidError("reply must not be blank")
    if len(stripped) > MAX_HR_REPLY_LENGTH:
        raise HrReplyInvalidError(
            f"reply must be at most {MAX_HR_REPLY_LENGTH} characters"
        )
    if "\x00" in stripped:
        raise HrReplyInvalidError("reply must not contain NUL characters")
    return stripped


async def _apply_reply(
    session: AsyncSession,
    confession_id: uuid.UUID,
    reply: str,
    first_save: bool,
    now: datetime,
) -> None:
    """Run the guarded UPDATE that saves *reply*, or raise if the row is gone.

    ``hr_replied_at`` is stamped once, on the first save, and never moves.
    ``hr_reply_edited_at`` is stamped only when the text changes afterwards:
    it is the confessor's only signal that words they may already have read
    were changed.

    ``updated_at`` is set to itself so the ORM's ``onupdate`` does not fire.
    A reply must neither restart the retention clock nor change the Telegram
    bot's delivery dedupe key.

    The status guard in the WHERE clause closes the race with the confessor's
    soft delete: a row deleted after the eligibility read matches nothing.
    """
    statement = (
        update(Confession)
        .where(
            Confession.id == confession_id,
            Confession.status.in_(REPLY_ELIGIBLE_STATUSES),
        )
        .values(hr_reply=reply, updated_at=Confession.updated_at)
    )
    if first_save:
        statement = statement.values(hr_replied_at=now)
    else:
        statement = statement.values(hr_reply_edited_at=now)

    result = cast(
        CursorResult,
        await session.execute(statement.execution_options(synchronize_session=False)),
    )
    if result.rowcount == 0:
        raise ConfessionNotFoundError(f"no visible confession with id {confession_id}")


async def write_reply(
    session: AsyncSession, confession_id: uuid.UUID, text: str, now: datetime
) -> ReplyWriteResult:
    """Save *text* as the reply to a confession, or report it is unchanged.

    Saving identical stripped text writes nothing and moves no timestamp. If
    two HR users save at once the last write wins, and both are audited.

    Args:
        session: Active database session (the caller commits).
        confession_id: Confession being replied to.
        text: The reply as submitted; stripped before it is stored.
        now: Current time, injected (AGENTS.md §16.5).

    Raises:
        HrReplyInvalidError: If *text* is blank, too long, or contains NUL.
        ConfessionNotFoundError: If no visible confession has that ID, or the
            confessor deleted it while the write was in flight.
        ReplyNotPermittedError: If the confession's status does not take a
            reply yet.
    """
    reply = normalize_reply(text)
    current = await confession_access.read_summary(session, confession_id)
    if current.status not in REPLY_ELIGIBLE_STATUSES:
        raise ReplyNotPermittedError(
            f"confessions in status '{current.status.value}' cannot take a "
            "reply until moderation approves them"
        )
    if reply == current.hr_reply:
        return ReplyWriteResult(current, changed=False)

    await _apply_reply(
        session,
        confession_id,
        reply,
        first_save=current.hr_replied_at is None,
        now=now,
    )
    updated = await confession_access.read_summary(session, confession_id)
    return ReplyWriteResult(updated, changed=True)
