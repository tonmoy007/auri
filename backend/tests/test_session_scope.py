"""Tests that a request's database work is committed before its response is sent.

``get_async_session`` commits when its dependency exits. By default FastAPI runs
that exit code only after the response has gone out, so a commit that fails
there cannot change what the caller already received: a confessor could be told
their confession was saved while it was lost, and a bot-key decision could be
acknowledged without ever being stored. Declaring the dependency with
``scope="function"`` makes it exit right after the handler, before the response.

These tests run the real ``get_async_session`` (only its session factory is
swapped) so they exercise the actual ordering rather than an override.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from app import database
from app.database import get_async_session
from app.main import app
from app.models.confession import Confession, ConfessionStatus
from app.services import login_throttle
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.confession_seeding import add_confession
from tests.conftest import TEST_SESSION_SECRET, SettingPatcher

DEVICE = "device-hash-of-the-confessor-0001"
BOT_KEY = "moderation-key-for-tests"
DELIVERY_KEY = "delivery-key-for-tests"


@dataclass
class Timeline:
    """What happened during one request, in order."""

    events: list[str] = field(default_factory=list)
    sessions_opened: int = 0


def _session_dependencies(dependant: Dependant) -> list[Dependant]:
    found = [dependant] if dependant.call is get_async_session else []
    for child in dependant.dependencies:
        found.extend(_session_dependencies(child))
    return found


def test_every_session_dependency_is_function_scoped() -> None:
    # Arrange — a dependency left at the default scope commits after the response
    declarations = [
        declaration
        for route in app.routes
        if isinstance(route, APIRoute)
        for declaration in _session_dependencies(route.dependant)
    ]

    # Act
    unscoped = [d for d in declarations if d.scope != "function"]

    # Assert
    assert len(declarations) >= 30
    assert unscoped == []


@pytest_asyncio.fixture
async def timed_client(
    db_engine: AsyncEngine, set_setting: SettingPatcher, monkeypatch
) -> AsyncIterator[tuple[AsyncClient, Timeline, Callable[[bool], None]]]:
    """A client running the real ``get_async_session`` against the test database.

    Returns the client, a timeline of commits and response starts, and a switch
    that makes every commit fail (as if the database were refusing writes).
    """
    set_setting("SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    set_setting("MODERATION_API_KEY", BOT_KEY)
    set_setting("DELIVERY_API_KEY", DELIVERY_KEY)
    login_throttle.reset_all()
    base = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    timeline = Timeline()
    fail = {"commits": False}

    class TimedSession(AsyncSession):
        async def commit(self) -> None:
            if fail["commits"]:
                timeline.events.append("commit-failed")
                raise RuntimeError("database unavailable")
            timeline.events.append("commit")
            await super().commit()

    factory = async_sessionmaker(
        bind=db_engine, class_=TimedSession, expire_on_commit=False
    )

    def counting_factory() -> AsyncSession:
        timeline.sessions_opened += 1
        return factory()

    monkeypatch.setattr(database, "async_session_factory", counting_factory)
    assert base is not None  # the plain factory is only used by the tests' own reads

    async def recording_app(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        async def recording_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                timeline.events.append("response-start")
            await send(message)

        await app(scope, receive, recording_send)

    transport = ASGITransport(app=recording_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, timeline, lambda value: fail.update(commits=value)
    login_throttle.reset_all()


async def _status_of(engine: AsyncEngine, confession_id) -> ConfessionStatus:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        stmt = select(Confession.status).where(Confession.id == confession_id)
        return (await session.execute(stmt)).scalar_one()


# ── One session per request ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_request_opens_exactly_one_session(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_session: AsyncSession,
    make_staff,
) -> None:
    # Arrange — a route with an auth sub-dependency that also needs the session;
    # two scopes would give it two sessions and two transactions
    client, timeline, _ = timed_client
    from app.models.user import UserRole

    _, headers = await make_staff(UserRole.hr)
    await add_confession(db_session)

    # Act
    response = await client.get("/api/v1/hr/confessions", headers=headers)

    # Assert
    assert response.status_code == 200
    assert timeline.sessions_opened == 1


# ── The commit happens before the response ───────────────────────────────


@pytest.mark.asyncio
async def test_a_confessors_delete_is_committed_before_the_response_starts(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    # Arrange
    client, timeline, _ = timed_client
    confession = await add_confession(db_session, device_token_hash=DEVICE)
    confession_id = confession.id

    # Act
    response = await client.delete(
        f"/api/v1/confessions/{confession_id}", headers={"X-Device-Token-Hash": DEVICE}
    )

    # Assert
    assert response.status_code == 204
    assert timeline.events.index("commit") < timeline.events.index("response-start")
    assert await _status_of(db_engine, confession_id) is ConfessionStatus.deleted


@pytest.mark.asyncio
async def test_a_bot_key_decision_is_committed_before_the_response_starts(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    # Arrange — the bot path writes no audit row, so nothing else commits it early
    client, timeline, _ = timed_client
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    confession_id = confession.id

    # Act
    response = await client.post(
        f"/api/v1/moderation/{confession_id}/approve",
        headers={"X-Moderation-Api-Key": BOT_KEY},
    )

    # Assert
    assert response.status_code == 200
    assert timeline.events.index("commit") < timeline.events.index("response-start")
    assert await _status_of(db_engine, confession_id) is ConfessionStatus.pending


@pytest.mark.asyncio
async def test_marking_a_confession_delivered_is_committed_before_the_response_starts(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_session: AsyncSession,
) -> None:
    # Arrange
    client, timeline, _ = timed_client
    confession = await add_confession(
        db_session, status=ConfessionStatus.forwarded, department="HR"
    )

    # Act
    response = await client.post(
        f"/api/v1/delivery/{confession.id}/delivered",
        headers={"X-Delivery-Api-Key": DELIVERY_KEY},
    )

    # Assert
    assert response.status_code == 200
    assert timeline.events.index("commit") < timeline.events.index("response-start")


# ── A failed commit is an error, not a silent loss ───────────────────────


@pytest.mark.asyncio
async def test_a_failed_commit_on_a_confessors_delete_is_a_500_not_a_204(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    # Arrange — the confessor must not be told it worked when it did not
    client, timeline, fail_commits = timed_client
    confession = await add_confession(db_session, device_token_hash=DEVICE)
    confession_id = confession.id
    fail_commits(True)

    # Act
    response = await client.delete(
        f"/api/v1/confessions/{confession_id}", headers={"X-Device-Token-Hash": DEVICE}
    )

    # Assert
    assert response.status_code == 500
    assert timeline.events.index("commit-failed") < timeline.events.index(
        "response-start"
    )
    assert await _status_of(db_engine, confession_id) is ConfessionStatus.pending


@pytest.mark.asyncio
async def test_a_failed_commit_on_a_bot_key_decision_is_a_500(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    # Arrange
    client, _, fail_commits = timed_client
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    confession_id = confession.id
    fail_commits(True)

    # Act
    response = await client.post(
        f"/api/v1/moderation/{confession_id}/reject",
        headers={"X-Moderation-Api-Key": BOT_KEY},
    )

    # Assert
    assert response.status_code == 500
    assert await _status_of(db_engine, confession_id) is ConfessionStatus.flagged


@pytest.mark.asyncio
async def test_a_handler_error_still_rolls_back_and_reports_the_error(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_session: AsyncSession,
) -> None:
    # Arrange — a 404 raised by the handler must not be turned into a commit
    client, timeline, _ = timed_client
    await add_confession(db_session, device_token_hash=DEVICE)

    # Act
    response = await client.delete(
        "/api/v1/confessions/00000000-0000-0000-0000-000000000000",
        headers={"X-Device-Token-Hash": DEVICE},
    )

    # Assert
    assert response.status_code == 404
    assert "commit" not in timeline.events


@pytest.mark.asyncio
async def test_an_exception_after_a_write_rolls_the_write_back(
    timed_client: tuple[AsyncClient, Timeline, Callable[[bool], None]],
    db_engine: AsyncEngine,
) -> None:
    # Arrange — the handler flushed a change, then failed: the real dependency
    # must discard it rather than commit half a request
    _, timeline, _ = timed_client
    generator = get_async_session()
    session = await generator.__anext__()
    session.add(
        Confession(
            device_token_hash="x" * 32,
            voice_mask="warm",
            transcript="never kept",
            pii_stripped=True,
            status=ConfessionStatus.pending,
        )
    )
    await session.flush()

    # Act
    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("handler failed"))

    # Assert
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as reader:
        kept = (await reader.execute(select(Confession))).scalars().all()
    assert kept == []
    assert "commit" not in timeline.events
