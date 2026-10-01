"""Staff account lifecycle: creation, credential verification, admin bootstrap.

Passwords are hashed with **Argon2id** (``argon2-cffi``) — a memory-hard KDF
chosen over bcrypt because it has no 72-byte silent truncation and tunable
memory cost. Only the digest is ever persisted; the plaintext exists for the
duration of the call and nowhere else.

Authentication fails **closed** and identically for every failure mode
(unknown email, wrong password, deactivated account) so the response cannot
be used to enumerate who has an account.
"""

from __future__ import annotations

import logging
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DuplicateUserError, InvalidEmailError, WeakPasswordError
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
MAX_EMAIL_LENGTH = 320  # RFC 3696 erratum: 64-char local part + @ + 255-char domain

_hasher = PasswordHasher()

# Lazily-built digest used to burn the same CPU on an unknown email as on a
# real one — without it, a fast "no such user" reply leaks which addresses
# have accounts. Built on first miss so importing this module stays cheap.
_timing_equalisation_hash: str | None = None


def normalize_email(raw_email: str) -> str:
    """Return *raw_email* trimmed and lower-cased, or raise if unusable.

    Args:
        raw_email: Address as typed by an admin or read from the environment.

    Returns:
        The canonical form stored in ``users.email``.

    Raises:
        InvalidEmailError: If the address is empty, over-long, contains
            whitespace, or has no single ``@`` separating two non-empty parts.
    """
    candidate = raw_email.strip().lower()
    if not candidate or len(candidate) > MAX_EMAIL_LENGTH:
        raise InvalidEmailError("email must be 1–320 characters")
    if any(character.isspace() for character in candidate):
        raise InvalidEmailError("email must not contain whitespace")

    local_part, separator, domain = candidate.partition("@")
    if not separator or not local_part or "@" in domain or "." not in domain:
        raise InvalidEmailError(f"malformed email address: {candidate!r}")
    return candidate


def hash_password(plain_password: str) -> str:
    """Return an Argon2id digest of *plain_password*.

    Raises:
        WeakPasswordError: If the password is shorter than
            :data:`MIN_PASSWORD_LENGTH`.
    """
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    return _hasher.hash(plain_password)


def verify_password(password_hash: str, plain_password: str) -> bool:
    """Return ``True`` if *plain_password* matches *password_hash*."""
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerificationError, InvalidHashError) as exc:
        logger.debug("password verification failed: %s", type(exc).__name__)
        return False


def _burn_verification_time() -> None:
    """Verify against a throwaway digest so misses cost what hits cost."""
    global _timing_equalisation_hash
    if _timing_equalisation_hash is None:
        _timing_equalisation_hash = _hasher.hash("no-account-with-this-email")
    verify_password(_timing_equalisation_hash, "wrong-password")


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the staff account registered to *email*, or ``None``."""
    result = await session.execute(
        select(User).where(User.email == email.strip().lower())
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession, email: str, plain_password: str, role: UserRole
) -> User:
    """Create a staff account and flush it (the caller commits).

    Args:
        session: Active database session.
        email: Login address; normalised before storage.
        plain_password: Password to hash — never persisted as given.
        role: Access level granted to the account.

    Returns:
        The persisted :class:`User`.

    Raises:
        InvalidEmailError: If *email* is unusable.
        WeakPasswordError: If *plain_password* is too short.
        DuplicateUserError: If the email is already registered.
    """
    normalized_email = normalize_email(email)
    password_hash = hash_password(plain_password)

    if await get_user_by_email(session, normalized_email) is not None:
        raise DuplicateUserError(f"email already registered: {normalized_email}")

    user = User(
        email=normalized_email,
        password_hash=password_hash,
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    logger.info("created staff account with role %s", role.value)
    return user


async def authenticate_user(
    session: AsyncSession, email: str, plain_password: str
) -> User | None:
    """Return the account matching the credentials, or ``None``.

    Unknown email, wrong password, and deactivated account are all reported
    the same way and take comparable time — callers must not distinguish
    them to the client either.
    """
    try:
        normalized_email = normalize_email(email)
    except InvalidEmailError:
        _burn_verification_time()
        return None

    user = await get_user_by_email(session, normalized_email)
    if user is None:
        _burn_verification_time()
        return None
    if not verify_password(user.password_hash, plain_password):
        return None
    if not user.is_active:
        logger.warning("login rejected: account is deactivated")
        return None
    return user


async def record_login(session: AsyncSession, user: User, now: datetime) -> None:
    """Stamp *user*'s successful login at *now* (the caller commits).

    *now* is injected rather than read from the clock so login bookkeeping
    is deterministically testable (AGENTS.md §16.5).
    """
    user.last_login_at = now
    await session.flush()


async def count_users(session: AsyncSession) -> int:
    """Return the total number of staff accounts."""
    return await session.scalar(select(func.count()).select_from(User)) or 0


async def bootstrap_admin(
    session: AsyncSession, email: str, plain_password: str
) -> User | None:
    """Create the first ``admin`` account from environment credentials.

    Runs at startup and is a no-op once any staff account exists, so
    restarting the app never resurrects or overwrites an account an admin
    deliberately removed or renamed.

    Args:
        session: Active database session (committed here on creation).
        email: ``ADMIN_BOOTSTRAP_EMAIL`` — empty disables bootstrap.
        plain_password: ``ADMIN_BOOTSTRAP_PASSWORD`` — empty disables bootstrap.

    Returns:
        The created admin, or ``None`` when bootstrap was skipped.
    """
    if not email.strip() or not plain_password:
        logger.info("Admin bootstrap skipped: no bootstrap credentials configured")
        return None

    existing_count = await count_users(session)
    if existing_count:
        logger.info(
            "Admin bootstrap skipped: %d staff account(s) already exist",
            existing_count,
        )
        return None

    admin = await create_user(session, email, plain_password, UserRole.admin)
    await session.commit()
    logger.info("Bootstrapped the first admin staff account")
    return admin
