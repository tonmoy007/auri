"""Unit tests for app.services.user_service.

Real Argon2 hashing and a real (in-memory SQLite) session — the hashing and
credential rules *are* the domain logic under test, so nothing here is
mocked (AGENTS.md §16.4). Each test gets a fresh database (§16.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from app.exceptions import DuplicateUserError, InvalidEmailError, WeakPasswordError
from app.models.base import Base
from app.models.user import User, UserRole
from app.services.user_service import (
    authenticate_user,
    bootstrap_admin,
    count_users,
    create_user,
    get_user_by_email,
    hash_password,
    normalize_email,
    record_login,
    verify_password,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
VALID_PASSWORD = "correct-horse-battery"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as s:
        yield s

    await engine.dispose()


# ── Email normalisation ──────────────────────────────────────────────────


def test_normalize_email_lowercases_and_trims() -> None:
    # Arrange
    raw = "  HR.Lead@Example.COM  "

    # Act
    normalized = normalize_email(raw)

    # Assert
    assert normalized == "hr.lead@example.com"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "no-at-sign.example.com", "two@at@example.com", "hr@localhost"],
)
def test_normalize_email_rejects_malformed_addresses(raw: str) -> None:
    # Arrange / Act / Assert
    with pytest.raises(InvalidEmailError):
        normalize_email(raw)


# ── Password hashing ─────────────────────────────────────────────────────


def test_hash_password_produces_verifiable_argon2id_digest() -> None:
    # Arrange
    plain = VALID_PASSWORD

    # Act
    digest = hash_password(plain)

    # Assert
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, plain) is True


def test_hash_password_never_returns_the_plaintext() -> None:
    # Arrange
    plain = VALID_PASSWORD

    # Act
    digest = hash_password(plain)

    # Assert
    assert plain not in digest


def test_hash_password_salts_so_two_hashes_of_one_password_differ() -> None:
    # Arrange
    plain = VALID_PASSWORD

    # Act
    first_digest = hash_password(plain)
    second_digest = hash_password(plain)

    # Assert
    assert first_digest != second_digest


def test_hash_password_rejects_password_below_minimum_length() -> None:
    # Arrange
    too_short = "short1234!"  # 10 chars, minimum is 12

    # Act / Assert
    with pytest.raises(WeakPasswordError):
        hash_password(too_short)


def test_verify_password_rejects_wrong_password() -> None:
    # Arrange
    digest = hash_password(VALID_PASSWORD)

    # Act
    matched = verify_password(digest, "not-the-right-password")

    # Assert
    assert matched is False


def test_verify_password_rejects_a_non_argon2_hash_string() -> None:
    # Arrange — a legacy/plaintext column value must never authenticate
    digest = VALID_PASSWORD

    # Act
    matched = verify_password(digest, VALID_PASSWORD)

    # Assert
    assert matched is False


# ── Account creation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_stores_normalized_email_and_hashed_password(
    session: AsyncSession,
) -> None:
    # Arrange
    raw_email = "  HR.Lead@Example.COM "

    # Act
    created = await create_user(session, raw_email, VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Assert
    stored = (await session.execute(select(User))).scalar_one()
    assert stored.email == "hr.lead@example.com"
    assert stored.password_hash != VALID_PASSWORD
    assert stored.role == UserRole.hr
    assert stored.is_active is True
    assert stored.last_login_at is None
    assert created.id == stored.id


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email_case_insensitively(
    session: AsyncSession,
) -> None:
    # Arrange
    await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act / Assert
    with pytest.raises(DuplicateUserError):
        await create_user(session, "HR@Example.com", VALID_PASSWORD, UserRole.hr)


@pytest.mark.asyncio
async def test_create_user_rejects_weak_password_without_inserting_a_row(
    session: AsyncSession,
) -> None:
    # Arrange
    weak_password = "abc123"

    # Act
    with pytest.raises(WeakPasswordError):
        await create_user(session, "hr@example.com", weak_password, UserRole.hr)

    # Assert
    assert await count_users(session) == 0


@pytest.mark.asyncio
async def test_get_user_by_email_matches_regardless_of_input_casing(
    session: AsyncSession,
) -> None:
    # Arrange
    await create_user(
        session, "moderator@example.com", VALID_PASSWORD, UserRole.moderator
    )
    await session.commit()

    # Act
    found = await get_user_by_email(session, "  Moderator@EXAMPLE.com  ")

    # Assert
    assert found is not None
    assert found.role == UserRole.moderator


# ── Authentication ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticate_user_returns_account_for_correct_credentials(
    session: AsyncSession,
) -> None:
    # Arrange
    await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act
    authenticated = await authenticate_user(session, "HR@example.com", VALID_PASSWORD)

    # Assert
    assert authenticated is not None
    assert authenticated.email == "hr@example.com"
    assert authenticated.role == UserRole.hr


@pytest.mark.asyncio
async def test_authenticate_user_rejects_wrong_password(
    session: AsyncSession,
) -> None:
    # Arrange
    await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act
    authenticated = await authenticate_user(session, "hr@example.com", "wrong-password")

    # Assert
    assert authenticated is None


@pytest.mark.asyncio
async def test_authenticate_user_rejects_unknown_email(
    session: AsyncSession,
) -> None:
    # Arrange — no accounts at all
    # Act
    authenticated = await authenticate_user(
        session, "nobody@example.com", VALID_PASSWORD
    )

    # Assert
    assert authenticated is None


@pytest.mark.asyncio
async def test_authenticate_user_rejects_malformed_email_without_querying(
    session: AsyncSession,
) -> None:
    # Arrange
    await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act
    authenticated = await authenticate_user(session, "not-an-email", VALID_PASSWORD)

    # Assert
    assert authenticated is None


@pytest.mark.asyncio
async def test_authenticate_user_rejects_deactivated_account_with_valid_password(
    session: AsyncSession,
) -> None:
    # Arrange — revoking access must survive knowing the right password
    user = await create_user(session, "gone@example.com", VALID_PASSWORD, UserRole.hr)
    user.is_active = False
    await session.commit()

    # Act
    authenticated = await authenticate_user(session, "gone@example.com", VALID_PASSWORD)

    # Assert
    assert authenticated is None


@pytest.mark.asyncio
async def test_record_login_stamps_the_injected_time(session: AsyncSession) -> None:
    # Arrange
    user = await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act
    await record_login(session, user, NOW)
    await session.commit()

    # Assert
    stored = (await session.execute(select(User))).scalar_one()
    assert stored.last_login_at == NOW


# ── Admin bootstrap ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_first_admin_on_an_empty_database(
    session: AsyncSession,
) -> None:
    # Arrange
    bootstrap_email = "founder@example.com"

    # Act
    admin = await bootstrap_admin(session, bootstrap_email, VALID_PASSWORD)

    # Assert
    assert admin is not None
    assert admin.role == UserRole.admin
    assert admin.email == bootstrap_email
    assert await count_users(session) == 1


@pytest.mark.asyncio
async def test_bootstrap_admin_is_a_no_op_when_any_account_exists(
    session: AsyncSession,
) -> None:
    # Arrange — an admin deleted the bootstrap account and made another one;
    # a restart must not resurrect the env-configured admin
    await create_user(session, "hr@example.com", VALID_PASSWORD, UserRole.hr)
    await session.commit()

    # Act
    admin = await bootstrap_admin(session, "founder@example.com", VALID_PASSWORD)

    # Assert
    assert admin is None
    assert await count_users(session) == 1


@pytest.mark.asyncio
async def test_bootstrap_admin_skips_when_credentials_are_unset(
    session: AsyncSession,
) -> None:
    # Arrange — the default: both env vars empty
    # Act
    admin = await bootstrap_admin(session, "", "")

    # Assert
    assert admin is None
    assert await count_users(session) == 0


@pytest.mark.asyncio
async def test_bootstrapped_admin_can_authenticate_with_its_env_password(
    session: AsyncSession,
) -> None:
    # Arrange
    await bootstrap_admin(session, "founder@example.com", VALID_PASSWORD)

    # Act
    authenticated = await authenticate_user(
        session, "founder@example.com", VALID_PASSWORD
    )

    # Assert
    assert authenticated is not None
    assert authenticated.role == UserRole.admin
