"""Tests that an audit row is durable before any content is released.

``get_async_session`` commits *after* the ``yield``. That exit now runs before the
response is sent (the dependency is function-scoped; ``test_session_scope`` pins
that), but the audit row is still committed by ``audit_service.record`` itself, so
it is durable at the moment the handler releases content and does not depend on
the exit code running. Every audited route is held to three properties, each driven
by the same scenario table so no call site can be left out:

* **durable** - run against a session that never commits on exit; the audit row
  (and the change it accounts for) is only visible afterwards if the route
  committed it itself;
* **fails closed** - if that commit fails the request fails, and it fails *at the
  audit row's commit*, not somewhere unrelated;
* **last database call** - nothing touches the database after the audit commit,
  so a later failure cannot leave a change committed behind an error response.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.database import async_session_factory, get_async_session
from app.main import app
from app.models.audit_event import AuditAction, AuditEvent
from app.models.confession import Confession, ConfessionStatus
from app.models.department import Department
from app.models.user import UserRole
from app.services import audit_service, department_service, login_throttle
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.confession_seeding import add_confession
from tests.conftest import TEST_SESSION_SECRET, SettingPatcher, StaffFactory

PRIVATE_WORDS = "the original words the audit exists to account for"
JUSTIFICATION = "a welfare check on a flagged item"

Headers = dict[str, str]
Request = Callable[[AsyncClient, Headers, AsyncSession], Awaitable[Response]]


@dataclass
class SessionLog:
    """What the spy session saw: the calls made, and whether commits were refused."""

    calls: list[str]


# ── Clients ──────────────────────────────────────────────────────────────


def _install(session_dependency) -> None:
    app.dependency_overrides[get_async_session] = session_dependency


BOT_KEY = "bot-secret"
BOT_HEADERS = {"X-Moderation-Api-Key": BOT_KEY}


@pytest_asyncio.fixture
async def durable_client(
    db_engine: AsyncEngine, set_setting: SettingPatcher
) -> AsyncIterator[AsyncClient]:
    """An API client whose request session never commits when the request ends."""
    set_setting("SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    set_setting("MODERATION_API_KEY", BOT_KEY)
    login_throttle.reset_all()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def session_that_only_rolls_back() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.rollback()

    _install(session_that_only_rolls_back)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    login_throttle.reset_all()


@pytest_asyncio.fixture
async def failing_commit_client(
    db_engine: AsyncEngine, set_setting: SettingPatcher
) -> AsyncIterator[tuple[AsyncClient, list[bool]]]:
    """A client whose commit fails, recording whether an audit row was pending each time.

    The second value is one entry per commit attempt: ``True`` if an audit row was
    waiting to be committed. It lets a test prove the 500 came from the audit
    commit and not from somewhere unrelated.
    """
    set_setting("SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    set_setting("MODERATION_API_KEY", BOT_KEY)
    login_throttle.reset_all()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    attempts: list[bool] = []

    async def session_whose_commit_fails() -> AsyncIterator[AsyncSession]:
        async with factory() as session:

            async def failing_commit() -> None:
                attempts.append(any(isinstance(o, AuditEvent) for o in session.new))
                raise RuntimeError("database unavailable")

            session.commit = failing_commit  # type: ignore[method-assign]
            yield session
            await session.rollback()

    _install(session_whose_commit_fails)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, attempts
    app.dependency_overrides.clear()
    login_throttle.reset_all()


@pytest_asyncio.fixture
async def spying_client(
    db_engine: AsyncEngine, set_setting: SettingPatcher
) -> AsyncIterator[tuple[AsyncClient, SessionLog]]:
    """A client whose session logs every database call, marking the audit commit."""
    set_setting("SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    set_setting("MODERATION_API_KEY", BOT_KEY)
    login_throttle.reset_all()
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    log = SessionLog(calls=[])

    def spied(session: AsyncSession, name: str):
        original = getattr(session, name)

        async def wrapper(*args, **kwargs):
            log.calls.append(name)
            return await original(*args, **kwargs)

        return wrapper

    async def session_with_spy() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            for name in ("execute", "flush", "refresh", "scalar", "scalars"):
                setattr(session, name, spied(session, name))
            real_commit = session.commit

            async def commit() -> None:
                is_audit = any(isinstance(o, AuditEvent) for o in session.new)
                log.calls.append("commit:audit" if is_audit else "commit")
                await real_commit()

            session.commit = commit  # type: ignore[method-assign]
            yield session
            await session.rollback()

    _install(session_with_spy)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, log
    app.dependency_overrides.clear()
    login_throttle.reset_all()


async def _audit_rows(engine: AsyncEngine) -> list[tuple[str, str | None]]:
    """(action, tier) of every audit row, read through a fresh session."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at)
        rows = (await session.execute(stmt)).scalars().all()
        return [(row.action, row.content_tier) for row in rows]


async def _fresh(engine: AsyncEngine, statement):
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        return (await session.execute(statement)).scalars().all()


# ── The scenarios: one per audited route ─────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    """One audited route: who calls it, what it does, and the row it must leave."""

    role: UserRole
    audit: tuple[str, str | None]
    request: Request
    # Whether the audit commit is the route's first commit (themes commits earlier,
    # in its service, so a failing commit there proves nothing about the audit row).
    audit_is_first_commit: bool = True
    # What success looks like: the SSO callback answers with a redirect.
    ok_statuses: tuple[int, ...] = (200, 201, 202, 204)


async def _summary_list(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    await add_confession(db)
    return await client.get("/api/v1/hr/confessions", headers=headers)


async def _summary_read(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    confession = await add_confession(db)
    return await client.get(f"/api/v1/hr/confessions/{confession.id}", headers=headers)


async def _raw_read(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    confession = await add_confession(
        db, status=ConfessionStatus.flagged, transcript=PRIVATE_WORDS
    )
    return await client.post(
        f"/api/v1/hr/confessions/{confession.id}/raw",
        json={"justification": JUSTIFICATION},
        headers=headers,
    )


async def _reply(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    confession = await add_confession(db)
    return await client.put(
        f"/api/v1/hr/confessions/{confession.id}/reply",
        json={"reply": "we heard you"},
        headers=headers,
    )


async def _insights(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    return await client.get("/api/v1/hr/insights", headers=headers)


async def _themes(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    return await client.get("/api/v1/hr/themes", headers=headers)


async def _queue(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    await add_confession(db, status=ConfessionStatus.flagged, transcript=PRIVATE_WORDS)
    return await client.get("/api/v1/moderation/queue", headers=headers)


async def _approve(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    confession = await add_confession(db, status=ConfessionStatus.flagged)
    return await client.post(
        f"/api/v1/moderation/{confession.id}/approve", headers=headers
    )


async def _reject(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    confession = await add_confession(db, status=ConfessionStatus.flagged)
    return await client.post(
        f"/api/v1/moderation/{confession.id}/reject", headers=headers
    )


async def _bot_approve(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    # The bot's shared key names no person, so the staff headers are not used
    confession = await add_confession(db, status=ConfessionStatus.flagged)
    return await client.post(
        f"/api/v1/moderation/{confession.id}/approve", headers=BOT_HEADERS
    )


async def _bot_reject(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    confession = await add_confession(db, status=ConfessionStatus.flagged)
    return await client.post(
        f"/api/v1/moderation/{confession.id}/reject", headers=BOT_HEADERS
    )


async def _acknowledge(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    confession = await add_confession(
        db, status=ConfessionStatus.flagged, severity="crisis"
    )
    return await client.post(
        f"/api/v1/moderation/{confession.id}/acknowledge", headers=headers
    )


async def _resend(client: AsyncClient, headers: Headers, db: AsyncSession) -> Response:
    confession = await add_confession(
        db,
        status=ConfessionStatus.forwarded,
        department="HR",
        delivered_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
    )
    return await client.post(
        f"/api/v1/delivery/{confession.id}/resend", headers=headers
    )


async def _priest_reindex(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    # The builder launcher and the index directory are replaced: only the audit row is
    # under test here, and nothing may be started.
    from app.api.v1.priest_admin import get_builder_launcher

    with (
        tempfile.TemporaryDirectory() as root,
        patch("app.api.v1.priest_admin._index_dir", return_value=Path(root)),
    ):
        app.dependency_overrides[get_builder_launcher] = lambda: lambda: 4242
        return await client.post("/api/v1/admin/priest/reindex", headers=headers)


async def _priest_activate(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    with (
        tempfile.TemporaryDirectory() as root,
        patch("app.api.v1.priest_admin._index_dir", return_value=Path(root)),
        patch(
            "app.api.v1.priest_admin.index_store.list_versions",
            return_value=["20260930T100000Z-abcdef12"],
        ),
        patch("app.api.v1.priest_admin.index_store.activate"),
    ):
        return await client.post(
            "/api/v1/admin/priest/activate",
            json={"version": "20260930T100000Z-abcdef12"},
            headers=headers,
        )


async def _department_create(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    return await client.post(
        "/api/v1/departments/directory", json={"name": "Facilities"}, headers=headers
    )


async def _seed_department(db: AsyncSession) -> None:
    await department_service.create_department(db, "Facilities", None)
    await db.commit()


async def _department_update(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    await _seed_department(db)
    return await client.put(
        "/api/v1/departments/directory/Facilities",
        json={"telegram_chat_id": "-100123", "is_active": False},
        headers=headers,
    )


async def _department_delete(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    await _seed_department(db)
    return await client.delete(
        "/api/v1/departments/directory/Facilities", headers=headers
    )


async def _config_set(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    return await client.put(
        "/api/v1/admin/config",
        json={"key": "PRIEST_TOP_K", "value": "6"},
        headers=headers,
    )


async def _config_reset(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    return await client.delete("/api/v1/admin/config/PRIEST_TOP_K", headers=headers)


async def _sso_login(
    client: AsyncClient, headers: Headers, db: AsyncSession
) -> Response:
    """A whole SSO sign-in for the hr account, through a fake provider (plan 15.10)."""
    from urllib.parse import parse_qs, urlsplit

    from app.api.v1.auth_oidc import _provider_or_404
    from app.services.oidc_sessions import oidc_sessions

    from tests.fake_oidc import FakeIdp

    idp = FakeIdp()
    provider = idp.provider()
    app.dependency_overrides[_provider_or_404] = lambda: provider
    oidc_sessions.clear()
    try:
        start = await client.get("/api/v1/auth/oidc/start")
        query = parse_qs(urlsplit(start.headers["location"]).query)
        idp.claims = {"nonce": query["nonce"][0], "email": "hr@example.com"}
        return await client.get(
            "/api/v1/auth/oidc/callback",
            params={"state": query["state"][0], "code": "c"},
        )
    finally:
        app.dependency_overrides.pop(_provider_or_404, None)
        oidc_sessions.clear()


HR = UserRole.hr
SCENARIOS: dict[str, Scenario] = {
    "summary-list": Scenario(HR, ("confession.list", "summary"), _summary_list),
    "summary-read": Scenario(HR, ("confession.read", "summary"), _summary_read),
    "raw-read": Scenario(HR, ("confession.read", "raw"), _raw_read),
    "reply": Scenario(HR, ("hr_reply.write", "summary"), _reply),
    "insights": Scenario(HR, ("insights.read", None), _insights),
    "themes": Scenario(
        HR, ("themes.read", "summary"), _themes, audit_is_first_commit=False
    ),
    "queue": Scenario(UserRole.moderator, ("confession.list", "raw"), _queue),
    "approve": Scenario(UserRole.moderator, ("moderation.approve", "raw"), _approve),
    "reject": Scenario(UserRole.moderator, ("moderation.reject", "raw"), _reject),
    "bot-approve": Scenario(
        UserRole.moderator, ("moderation.approve", "raw"), _bot_approve
    ),
    "bot-reject": Scenario(
        UserRole.moderator, ("moderation.reject", "raw"), _bot_reject
    ),
    "acknowledge": Scenario(HR, ("crisis.acknowledge", "raw"), _acknowledge),
    "resend": Scenario(HR, ("delivery.retry", None), _resend),
    "department-create": Scenario(
        UserRole.admin, ("department.write", None), _department_create
    ),
    "department-update": Scenario(
        UserRole.admin, ("department.write", None), _department_update
    ),
    "department-delete": Scenario(
        UserRole.admin, ("department.write", None), _department_delete
    ),
    "priest-reindex": Scenario(
        UserRole.admin, ("priest.reindex", None), _priest_reindex
    ),
    "priest-activate": Scenario(
        UserRole.admin, ("priest.activate", None), _priest_activate
    ),
    # the setting and its audit row are staged together and committed once
    "config-set": Scenario(UserRole.admin, ("config.write", None), _config_set),
    "config-reset": Scenario(UserRole.admin, ("config.write", None), _config_reset),
    "sso-login": Scenario(
        HR, ("auth.login_oidc", None), _sso_login, ok_statuses=(302,)
    ),
}


def test_every_audit_call_site_has_a_scenario() -> None:
    # Arrange — 18 record() call sites; approve and reject share one helper per caller
    # (staff session, bot key), and config set and reset share one helper, so the
    # scenarios cover all of them. A new site should add a scenario here.
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    sites = sum(
        len(re.findall(r"audit_service\.record\(", path.read_text()))
        for path in root.rglob("*.py")
    )

    # Act / Assert
    assert sites == 18
    assert len(SCENARIOS) >= 18


# ── The audit service itself ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_commits_so_the_row_survives_a_later_rollback(
    db_engine: AsyncEngine, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    staff, _ = await make_staff(UserRole.hr)

    # Act
    await audit_service.record(db_session, staff, AuditAction.insights_read)
    await db_session.rollback()

    # Assert
    assert await _audit_rows(db_engine) == [("insights.read", None)]


@pytest.mark.asyncio
async def test_record_raises_if_the_commit_fails_rather_than_swallowing_it(
    db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    staff, _ = await make_staff(UserRole.hr)

    async def failing_commit() -> None:
        raise RuntimeError("database unavailable")

    db_session.commit = failing_commit  # type: ignore[method-assign]

    # Act / Assert
    with pytest.raises(RuntimeError, match="database unavailable"):
        await audit_service.record(db_session, staff, AuditAction.insights_read)


def test_the_production_session_keeps_objects_usable_after_a_commit() -> None:
    # Arrange — record() commits mid-request, and many handlers still read ORM
    # objects afterwards; that only works with expire_on_commit=False

    # Act
    expires = async_session_factory.kw["expire_on_commit"]

    # Assert
    assert expires is False


# ── Durable: the row exists though the request session never commits ─────


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_each_audited_route_leaves_a_durable_row(
    name: str,
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    scenario = SCENARIOS[name]
    _, headers = await make_staff(scenario.role)

    # Act
    response = await scenario.request(durable_client, headers, db_session)

    # Assert
    assert response.status_code in scenario.ok_statuses
    assert await _audit_rows(db_engine) == [scenario.audit]


# ── The change and its audit row land together ───────────────────────────


@pytest.mark.asyncio
async def test_a_reply_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(db_session)
    confession_id = confession.id

    # Act
    await durable_client.put(
        f"/api/v1/hr/confessions/{confession_id}/reply",
        json={"reply": "we heard you"},
        headers=headers,
    )

    # Assert
    stored = await _fresh(
        db_engine, select(Confession.hr_reply).where(Confession.id == confession_id)
    )
    assert stored == ["we heard you"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "expected_status"),
    [("approve", ConfessionStatus.pending), ("reject", ConfessionStatus.deleted)],
)
async def test_a_moderation_decision_is_stored_with_its_audit_row(
    verb: str,
    expected_status: ConfessionStatus,
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator)
    confession = await add_confession(db_session, status=ConfessionStatus.flagged)
    confession_id = confession.id

    # Act
    await durable_client.post(
        f"/api/v1/moderation/{confession_id}/{verb}", headers=headers
    )

    # Assert
    stored = (
        await _fresh(
            db_engine, select(Confession).where(Confession.id == confession_id)
        )
    )[0]
    assert stored.status is expected_status
    assert stored.reviewed_by is not None


@pytest.mark.asyncio
async def test_a_crisis_acknowledgement_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(
        db_session, status=ConfessionStatus.flagged, severity="crisis"
    )
    confession_id = confession.id

    # Act
    await durable_client.post(
        f"/api/v1/moderation/{confession_id}/acknowledge", headers=headers
    )

    # Assert
    stored = (
        await _fresh(
            db_engine, select(Confession).where(Confession.id == confession_id)
        )
    )[0]
    assert stored.acknowledged_by is not None


@pytest.mark.asyncio
async def test_a_resend_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)
    confession = await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        department="HR",
        delivered_at=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
    )
    confession_id = confession.id

    # Act
    await durable_client.post(
        f"/api/v1/delivery/{confession_id}/resend", headers=headers
    )

    # Assert
    stored = await _fresh(
        db_engine, select(Confession.delivered_at).where(Confession.id == confession_id)
    )
    assert stored == [None]


@pytest.mark.asyncio
async def test_a_department_creation_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    await durable_client.post(
        "/api/v1/departments/directory", json={"name": "Facilities"}, headers=headers
    )

    # Assert
    assert await _fresh(db_engine, select(Department.name)) == ["Facilities"]


@pytest.mark.asyncio
async def test_a_department_update_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await _seed_department(db_session)

    # Act
    await durable_client.put(
        "/api/v1/departments/directory/Facilities",
        json={"telegram_chat_id": "-100123", "is_active": False},
        headers=headers,
    )

    # Assert — values that differ from what creation gave, so an update lost to
    # the exit commit would show
    stored = (await _fresh(db_engine, select(Department)))[0]
    assert (stored.telegram_chat_id, stored.is_active) == ("-100123", False)


@pytest.mark.asyncio
async def test_a_department_deletion_is_stored_with_its_audit_row(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    await _seed_department(db_session)

    # Act
    await durable_client.delete(
        "/api/v1/departments/directory/Facilities", headers=headers
    )

    # Assert
    assert await _fresh(db_engine, select(Department.name)) == []


# ── No record, no content ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", [n for n, s in SCENARIOS.items() if s.audit_is_first_commit]
)
async def test_if_the_audit_row_cannot_be_committed_the_request_fails(
    name: str,
    failing_commit_client: tuple[AsyncClient, list[bool]],
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange — the database refuses the commit that would make the row durable
    client, attempts = failing_commit_client
    scenario = SCENARIOS[name]
    _, headers = await make_staff(scenario.role)

    # Act
    response = await scenario.request(client, headers, db_session)

    # Assert — a 500, and it came from committing the audit row itself
    assert response.status_code == 500
    assert attempts == [True]
    assert PRIVATE_WORDS not in response.text


# ── The audit commit is the last database call ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_nothing_touches_the_database_after_the_audit_commit(
    name: str,
    spying_client: tuple[AsyncClient, SessionLog],
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange — a failure after the commit would leave the change committed behind
    # an error response, where it used to be rolled back
    client, log = spying_client
    scenario = SCENARIOS[name]
    _, headers = await make_staff(scenario.role)

    # Act
    response = await scenario.request(client, headers, db_session)

    # Assert
    assert response.status_code in scenario.ok_statuses
    assert log.calls.count("commit:audit") == 1
    after_audit = log.calls[log.calls.index("commit:audit") + 1 :]
    assert after_audit == []


@pytest.mark.asyncio
async def test_a_config_write_whose_audit_row_fails_is_not_left_applied(
    durable_client: AsyncClient,
    db_engine: AsyncEngine,
    make_staff: StaffFactory,
) -> None:
    # Arrange — the setting and its audit row are one commit: if the audit cannot be
    # written, the change (the kill switch, an address) must not be live unaudited
    from app.models.app_setting import AppSetting
    from app.services import settings_service

    _, headers = await make_staff(UserRole.admin)
    settings_service._cache.pop("PRIEST_TOP_K", None)

    # Act
    with (
        patch(
            "app.api.v1.admin.audit_service.record",
            side_effect=RuntimeError("audit store unavailable"),
        ),
        pytest.raises(RuntimeError, match="audit store unavailable"),
    ):
        await durable_client.put(
            "/api/v1/admin/config",
            json={"key": "PRIEST_TOP_K", "value": "9"},
            headers=headers,
        )

    # Assert
    assert await _fresh(db_engine, select(AppSetting.key)) == []
    assert "PRIEST_TOP_K" not in settings_service._cache
