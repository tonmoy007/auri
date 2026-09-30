"""Shared API dependencies: staff sessions, role checks, legacy service keys.

Two authentication paths coexist deliberately. Named staff sign in and carry
a session token (``Authorization: Bearer …``); the bot and existing local
tooling keep using their shared service secrets. Phase 11 adds the first
path without removing the second — ``MODERATION_API_KEY``, ``DELIVERY_API_KEY``
and ``ADMIN_API_KEY`` all keep working exactly as before.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import session_dependency
from app.exceptions import InvalidSessionTokenError
from app.models.user import User, UserRole
from app.services.auth_tokens import decode_token

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "

CredentialsRequired = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract_bearer_token(authorization: str | None) -> str:
    """Return the raw token from an ``Authorization`` header, or raise 401."""
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise CredentialsRequired
    return authorization.removeprefix(_BEARER_PREFIX).strip()


async def _load_active_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Return the active account for *user_id*, or raise 401."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise CredentialsRequired
    return user


async def get_current_user(
    authorization: str | None = Header(None),
    session: AsyncSession = session_dependency,
) -> User:
    """Resolve the staff account behind the request's session token.

    Raises:
        HTTPException: 401 if the token is missing, invalid, expired,
            revoked (``token_version`` bumped by logout), or belongs to a
            deleted/deactivated account.
    """
    token = _extract_bearer_token(authorization)
    try:
        claims = decode_token(token, "access")
    except InvalidSessionTokenError as exc:
        logger.info("session token rejected: %s", exc)
        raise CredentialsRequired from exc

    user = await _load_active_user(session, claims.user_id)
    if claims.token_version != user.token_version:
        logger.info("session token rejected: superseded by logout")
        raise CredentialsRequired
    return user


def require_role(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Build a dependency allowing only the given *roles*.

    ``admin`` is always allowed: an administrator can reach every surface
    without needing to be listed on each route.
    """
    allowed = frozenset(roles) | {UserRole.admin}

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not have access to this resource",
            )
        return user

    return dependency


# Built once at import rather than per route definition, so `Depends(...)`
# receives a stable dependency object (FastAPI caches per-request results by
# identity, and a fresh closure per call would defeat that).
require_admin_role = require_role(UserRole.admin)
require_hr_role = require_role(UserRole.hr)
# The moderation queue is shared: moderators review it, HR also works it.
require_queue_role = require_role(UserRole.moderator, UserRole.hr)


def _matches_admin_api_key(candidate: str | None) -> bool:
    """Return ``True`` if *candidate* is the configured admin service key.

    Fails closed when ``ADMIN_API_KEY`` is unset, and compares in constant
    time so a wrong key leaks nothing about the right one.
    """
    if not candidate or not settings.ADMIN_API_KEY:
        return False
    return secrets.compare_digest(candidate, settings.ADMIN_API_KEY)


async def require_admin_access(
    authorization: str | None = Header(None),
    x_admin_api_key: str | None = Header(None, alias="X-Admin-Api-Key"),
    session: AsyncSession = session_dependency,
) -> User | None:
    """Allow an ``admin`` session **or** the legacy ``X-Admin-Api-Key``.

    Returns:
        The signed-in admin, or ``None`` when the legacy shared key was used
        (that path has no named actor — which is exactly why staff accounts
        exist, and why audited routes should require a session instead).
    """
    if _matches_admin_api_key(x_admin_api_key):
        return None

    user = await get_current_user(authorization=authorization, session=session)
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return user
