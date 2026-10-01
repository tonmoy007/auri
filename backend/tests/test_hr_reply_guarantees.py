"""Storage-level guarantees of the HR reply path.

Two promises cannot be proven from what the service returns, so they are
pinned here against the real SQLite database (AGENTS.md §16.4):

* The reply and summary queries never SELECT ``transcript`` or
  ``device_token_hash``. The summary view is built from explicit fields, so
  its shape says nothing about what the query fetched; the SQL itself is
  recorded and inspected instead.
* The service never commits. The route's session commits the reply and its
  audit row together, so a reply must vanish with a rolled-back transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from app.api.v1 import hr
from app.main import app
from app.models.audit_event import AuditAction, AuditEvent, ContentTier
from app.models.confession import Confession, ConfessionStatus
from app.models.user import User, UserRole
from app.services import confession_access, hr_reply_service
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.confession_seeding import add_confession
from tests.conftest import StaffFactory

T1 = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=1)
CONFESSOR_ONLY_COLUMNS = ("transcript", "device_token_hash")


@contextmanager
def _captured_sql(engine: AsyncEngine) -> Iterator[list[str]]:
    """Collect every statement *engine* sends to the driver, then detach."""
    statements: list[str] = []

    def record_statement(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)


def _statements_naming_confessor_columns(statements: list[str]) -> list[str]:
    """Return the statements that mention the transcript or device hash."""
    return [
        statement
        for statement in statements
        if any(column in statement for column in CONFESSOR_ONLY_COLUMNS)
    ]


# ── Queries never fetch the confessor's content ──────────────────────────


@pytest.mark.asyncio
async def test_reply_writes_never_select_the_transcript_or_device_hash(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Arrange — expunge so no read can be answered from the identity map
    confession = await add_confession(db_session)
    confession_id = confession.id
    db_session.expunge_all()

    # Act
    with _captured_sql(db_engine) as statements:
        await hr_reply_service.write_reply(db_session, confession_id, "first", T1)
        await hr_reply_service.write_reply(db_session, confession_id, "second", T2)
        await hr_reply_service.write_reply(db_session, confession_id, "second", T2)

    # Assert
    verbs = {statement.lstrip().split()[0] for statement in statements}
    assert _statements_naming_confessor_columns(statements) == []
    assert verbs == {"SELECT", "UPDATE"}


@pytest.mark.asyncio
async def test_summary_listing_never_selects_the_transcript_or_device_hash(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Arrange
    await add_confession(db_session)
    await add_confession(db_session, hr_reply="one", hr_replied_at=T1)
    db_session.expunge_all()

    # Act
    with _captured_sql(db_engine) as statements:
        views, total = await confession_access.list_summaries(db_session)

    # Assert
    assert _statements_naming_confessor_columns(statements) == []
    assert (len(views), total) == (2, 2)


@pytest.mark.asyncio
async def test_the_recorder_sees_the_transcript_when_a_raw_read_selects_it(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    # Arrange — a control: without it the two tests above could pass by
    # recording nothing at all
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    confession_id = confession.id
    db_session.expunge_all()

    # Act
    with _captured_sql(db_engine) as statements:
        await confession_access.read_raw(
            db_session, confession_id, "welfare follow-up review"
        )

    # Assert
    offenders = _statements_naming_confessor_columns(statements)
    assert len(offenders) == 1
    assert "confessions.transcript" in offenders[0]


# ── The service leaves the commit to its caller ──────────────────────────


@pytest.mark.asyncio
async def test_a_reply_disappears_when_the_callers_transaction_rolls_back(
    db_session: AsyncSession,
) -> None:
    # Arrange — capture the id first: a rollback expires every loaded object
    confession = await add_confession(db_session)
    confession_id = confession.id
    await hr_reply_service.write_reply(db_session, confession_id, "we heard you", T1)

    # Act
    await db_session.rollback()

    # Assert
    stored = (
        await db_session.execute(
            select(
                Confession.hr_reply,
                Confession.hr_replied_at,
                Confession.hr_reply_edited_at,
            ).where(Confession.id == confession_id)
        )
    ).one()
    assert tuple(stored) == (None, None, None)


@pytest.mark.asyncio
async def test_reply_is_not_stored_when_its_audit_row_cannot_be_written(
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — the audit store failing is an outage the test database cannot
    # produce on its own, so the write is made to fail at that boundary
    async def failing_record(
        session: AsyncSession,
        actor: User,
        action: AuditAction,
        target_confession_id: uuid.UUID | None = None,
        content_tier: ContentTier | None = None,
        justification: str | None = None,
        source_ip: str | None = None,
    ) -> AuditEvent:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(hr.audit_service, "record", failing_record)
    app.dependency_overrides[hr.get_clock] = lambda: lambda: T1
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)
    confession_id = confession.id

    # Act
    response = await api_client.put(
        f"/api/v1/hr/confessions/{confession_id}/reply",
        headers=headers,
        json={"reply": "we heard you"},
    )

    # Assert
    stored = await db_session.scalar(
        select(Confession.hr_reply).where(Confession.id == confession_id)
    )
    assert response.status_code == 500
    assert stored is None
