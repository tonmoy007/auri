"""Shared fixtures for backend API tests.

Phase 11 put a real session/role layer in front of most endpoints, so nearly
every API test now needs the same three things: an isolated database, an
ASGI client wired to it, and a staff account to authenticate as. They live
here rather than being copy-pasted per module.

Each test gets its own in-memory SQLite database and its own login-throttle
state, so no test can influence another (AGENTS.md §16.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone

import pytest_asyncio
from app.config import settings
from app.database import get_async_session
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.services import login_throttle
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

TEST_SESSION_SECRET = "test-session-secret-not-a-real-one"
TEST_PASSWORD = "correct-horse-battery"

StaffFactory = Callable[..., Awaitable[tuple[User, dict[str, str]]]]


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
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
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
