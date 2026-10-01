"""Signed session tokens (JWT) for dashboard staff logins.

Two token types are issued at login: a short-lived ``access`` token sent on
every request, and a longer-lived ``refresh`` token used only to mint new
access tokens. Both carry the account's ``token_version``; logout bumps that
column so every previously-issued token for the account stops validating —
stateless JWTs with a real revocation path, without a session table.

Signing refuses to run outside ``development`` while the secret is still the
shipped placeholder: a dashboard protected by a publicly-known signing key
is worse than one that visibly fails to start.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import jwt

from app.config import settings
from app.exceptions import AuthConfigurationError, InvalidSessionTokenError
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

TokenType = Literal["access", "refresh"]

ALGORITHM: Final[str] = "HS256"
PLACEHOLDER_SECRETS: Final[frozenset[str]] = frozenset({"", "change-me-in-production"})


@dataclass(frozen=True)
class TokenClaims:
    """Validated contents of a session token."""

    user_id: uuid.UUID
    role: UserRole
    token_version: int
    token_type: TokenType


def signing_secret() -> str:
    """Return the HMAC secret for session tokens.

    Raises:
        AuthConfigurationError: If no real secret is configured and the
            environment is anything other than ``development``.
    """
    secret = settings.SESSION_TOKEN_SECRET or settings.SECRET_KEY
    if secret in PLACEHOLDER_SECRETS:
        if settings.ENVIRONMENT != "development":
            raise AuthConfigurationError(
                "SESSION_TOKEN_SECRET (or SECRET_KEY) must be set to a real "
                "value before staff sessions can be issued"
            )
        logger.warning(
            "Signing staff sessions with the placeholder secret — development only"
        )
    return secret


def _encode(user: User, token_type: TokenType, now: datetime, ttl: timedelta) -> str:
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "role": user.role.value,
        "tv": user.token_version,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, signing_secret(), algorithm=ALGORITHM)


def create_access_token(user: User, now: datetime) -> str:
    """Return a short-lived access token for *user*, issued at *now*."""
    return _encode(
        user,
        "access",
        now,
        timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
    )


def create_refresh_token(user: User, now: datetime) -> str:
    """Return a refresh token for *user*, issued at *now*."""
    return _encode(
        user,
        "refresh",
        now,
        timedelta(hours=settings.REFRESH_TOKEN_TTL_HOURS),
    )


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    """Validate *token* and return its claims.

    Args:
        token: Raw JWT string, without the ``Bearer `` prefix.
        expected_type: The token type the caller requires — an access token
            presented to the refresh endpoint (or the reverse) is rejected,
            so a leaked short-lived token cannot be traded up.

    Returns:
        The token's :class:`TokenClaims`.

    Raises:
        InvalidSessionTokenError: If the signature, expiry, structure, or
            token type does not check out.
    """
    try:
        payload = jwt.decode(token, signing_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidSessionTokenError(f"invalid session token: {exc}") from exc

    if payload.get("typ") != expected_type:
        raise InvalidSessionTokenError(
            f"expected a {expected_type} token, got {payload.get('typ')!r}"
        )

    try:
        return TokenClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            role=UserRole(payload["role"]),
            token_version=int(payload["tv"]),
            token_type=expected_type,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSessionTokenError("session token claims are malformed") from exc
