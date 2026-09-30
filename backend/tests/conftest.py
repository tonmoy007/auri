"""Shared fixtures for backend API tests.

Phase 11 put a real session/role layer in front of most endpoints, so nearly
every API test now needs the same three things: an isolated database, an
ASGI client wired to it, and a staff account to authenticate as. They live
here rather than being copy-pasted per module.

Each test gets its own in-memory SQLite database and its own login-throttle
state, so no test can influence another (AGENTS.md §16.3).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from app.config import Settings, settings
from app.database import get_async_session
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.services import login_throttle, settings_service
from app.services.auth_tokens import create_access_token
from app.services.user_service import create_user
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from tests import network_guard

TEST_SESSION_SECRET = "test-session-secret-not-a-real-one"
TEST_PASSWORD = "correct-horse-battery"

StaffFactory = Callable[..., Awaitable[tuple[User, dict[str, str]]]]
SettingPatcher = Callable[[str, Any], None]


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker for the rare test that really talks to a model server."""
    config.addinivalue_line(
        "markers",
        "live_llm: talks to a real model server (the vLLM box in the config); "
        "skipped unless RUN_LIVE_LLM=1 and never run in CI",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip live model tests unless they were asked for on purpose."""
    if network_guard.live_tests_enabled():
        return
    skip = pytest.mark.skip(
        reason=f"live model test: set {network_guard.LIVE_ENV}=1 to run"
    )
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def no_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every model call fail fast, as if no provider were reachable.

    Tests of the confession flow patch the model steps they care about; the ones they
    do not (counselling, sentiment) used to walk the real provider chain. Those steps
    are wrapped in fail-safes that fall back, so failing them keeps behaviour and sends
    nothing anywhere.
    """
    from app.exceptions import ProcessingError
    from app.services.llm import LLMService

    def _no_model(self: LLMService, prompt: str) -> str:
        raise ProcessingError("no model is available in tests")

    monkeypatch.setattr(LLMService, "_call_llm", _no_model)


@pytest.fixture(autouse=True)
def network_violations(request: pytest.FixtureRequest) -> Any:
    """Refuse real network connections in every test, and fail a test that tried one.

    The LLM code catches its own errors, so a blocked call alone would pass silently
    through a fail-safe; the recorded attempt fails the test at teardown instead.
    A test marked ``live_llm`` is exempt (and is skipped unless asked for).
    """
    violations: list[str] = []
    if request.node.get_closest_marker("live_llm"):
        yield violations
        return
    with network_guard.block_network(violations):
        yield violations
    if violations:
        pytest.fail(
            "test made real network connection(s) to "
            f"{sorted(set(violations))}; mock them, or mark a live test live_llm",
            pytrace=False,
        )


@pytest.fixture
def set_setting(monkeypatch) -> SettingPatcher:
    """Patch a config value everywhere it is currently held.

    ``test_config.py`` reloads ``app.config`` to exercise DATABASE_URL
    assembly, which mints a *new* ``settings`` singleton. Modules that did
    ``from app.config import settings`` at import time keep the old object
    forever, so patching only the freshly-imported one silently misses the
    object the route actually reads — a full-suite-only failure that passes
    in isolation. Patching every live instance sidesteps the ordering trap.
    """

    def _set(name: str, value: Any) -> None:
        patched: set[int] = set()
        for candidate in (settings, *_live_settings_objects()):
            if id(candidate) in patched:
                continue
            patched.add(id(candidate))
            monkeypatch.setattr(candidate, name, value)

    return _set


def _live_settings_objects() -> list[Settings]:
    """Return every ``Settings`` instance reachable as a module attribute."""
    found = []
    for module in list(sys.modules.values()):
        candidate = getattr(module, "settings", None)
        if isinstance(candidate, Settings):
            found.append(candidate)
    return found


@pytest.fixture(autouse=True)
def isolate_live_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own copy of the live (dashboard) settings layer.

    ``PUT /admin/config`` writes into a process-wide dict; without this, a test that
    sets a key (the Guide's kill switch, say) would change every test that runs after.
    """
    monkeypatch.setattr(settings_service, "_cache", dict(settings_service._cache))


@pytest.fixture(autouse=True)
def isolate_optional_endpoints(
    set_setting: SettingPatcher, request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Blank the optional remote-model settings, whatever a developer's .env holds.

    A dev machine may point the themes model at a real server with a real key;
    no test may reach it, or depend on it.
    """
    if request.node.get_closest_marker("live_llm"):
        return  # a deliberate live test reads the configuration it was asked to use
    for name, value in (
        ("THEMES_LLM_BASE_URL", ""),
        ("THEMES_LLM_MODEL", ""),
        ("THEMES_LLM_API_KEY", ""),
        ("THEMES_LLM_USE_OPENAI_API_KEY", False),
        ("THEMES_LLM_SELF_HOSTED", False),
        ("THEMES_LLM_ALLOW_INSECURE_HTTP", False),
        # Off unless a test turns it on: a secret in a developer's .env would
        # otherwise change what every device-scoped test stores.
        ("DEVICE_HASH_PEPPER", ""),
        ("DELIVERY_TRANSCRIPT_CHARS", 1000),
        # Priest mode and the crisis contacts: no test may reach a real chat server,
        # and none may depend on what a developer has set.
        ("PRIEST_MODE_ENABLED", False),
        ("PRIEST_LLM_BASE_URL", ""),
        ("PRIEST_LLM_API_KEY", ""),
        ("PRIEST_FALLBACK_BASE_URL", ""),
        ("PRIEST_FALLBACK_MODEL", ""),
        ("CRISIS_HELPLINE_NAME", ""),
        ("CRISIS_HELPLINE_NUMBER", ""),
        ("CRISIS_EAP_CONTACT", ""),
        # The Guide's files live in this test's own folder, never a developer's real
        # index or vault; the documented Ollama defaults, whatever .env says.
        ("PRIEST_INDEX_DIR", str(tmp_path / "priest-index")),
        ("PRIEST_VAULT_DIR", str(tmp_path / "priest-vault")),
        ("OLLAMA_BASE_URL", "http://localhost:11434"),
        ("OLLAMA_MODEL", "llama3.2:3b"),
    ):
        set_setting(name, value)


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory database with the full schema created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session on the same database the API client uses, for seeding rows."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def api_client(db_engine: AsyncEngine, monkeypatch) -> AsyncIterator[AsyncClient]:
    """ASGI client bound to the test database, with a real signing secret."""
    for candidate in {
        id(settings): settings,
        **{id(o): o for o in _live_settings_objects()},
    }.values():
        monkeypatch.setattr(candidate, "SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    login_throttle.reset_all()

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_async_session] = override_get_session

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    login_throttle.reset_all()


@pytest_asyncio.fixture
async def make_staff(db_session: AsyncSession) -> StaffFactory:
    """Return a factory creating a staff account plus its bearer headers."""

    async def _make(
        role: UserRole = UserRole.hr,
        email: str | None = None,
        password: str = TEST_PASSWORD,
    ) -> tuple[User, dict[str, str]]:
        user = await create_user(
            db_session, email or f"{role.value}@example.com", password, role
        )
        await db_session.commit()
        # Issued against the real clock: PyJWT validates `exp` with
        # time.time(), so a frozen issue time would mint a token that is
        # already expired by the time the request is made.
        token = create_access_token(user, datetime.now(timezone.utc))
        return user, {"Authorization": f"Bearer {token}"}

    return _make
