"""Staff authentication — login, token refresh, logout, and current identity."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CredentialsRequired, get_current_user
from app.config import settings
from app.database import session_dependency
from app.exceptions import InvalidSessionTokenError
from app.models.user import User, UserRole
from app.services import login_throttle
from app.services.auth_tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.user_service import authenticate_user, record_login

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

ClockDependency = Callable[[], datetime]


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function (see confessions.py)."""
    return lambda: datetime.now(timezone.utc)


# ── Schemas ──────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Credentials submitted by the dashboard login screen."""

    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    """A refresh token being traded for a new access token."""

    refresh_token: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    """Public representation of a staff account."""

    id: uuid.UUID
    email: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Issued session tokens plus the identity they belong to."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/login", response_model=TokenResponse, summary="Sign in a staff account")
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = session_dependency,
    clock: ClockDependency = Depends(get_clock),
) -> TokenResponse:
    """Exchange email and password for an access + refresh token pair.

    Every rejection returns the same ``401`` regardless of cause (unknown
    email, wrong password, deactivated account), so the endpoint cannot be
    used to discover who has an account. Repeated failures for one email
    lock that email out for ``LOGIN_ATTEMPT_WINDOW_SECONDS``.
    """
    now = clock()
    throttle_key = body.email.strip().lower()

    if login_throttle.is_locked_out(
        throttle_key,
        now,
        settings.LOGIN_MAX_ATTEMPTS,
        settings.LOGIN_ATTEMPT_WINDOW_SECONDS,
    ):
        logger.warning(
            "login locked out after repeated failures", extra={"path": "/auth/login"}
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts; try again later",
        )

    user = await authenticate_user(session, body.email, body.password)
    if user is None:
        login_throttle.record_failure(
            throttle_key, now, settings.LOGIN_ATTEMPT_WINDOW_SECONDS
        )
        logger.info(
            "failed staff login from %s", request.client.host if request.client else "?"
        )
        raise CredentialsRequired

    login_throttle.clear(throttle_key)
    await record_login(session, user, now)
    await session.commit()

    return _issue_tokens(user, now)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Trade a refresh token for a new session",
)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = session_dependency,
    clock: ClockDependency = Depends(get_clock),
) -> TokenResponse:
    """Issue a fresh token pair from a valid, unrevoked refresh token."""
    try:
        claims = decode_token(body.refresh_token, "refresh")
    except InvalidSessionTokenError as exc:
        logger.info("refresh token rejected: %s", exc)
        raise CredentialsRequired from exc

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active or claims.token_version != user.token_version:
        raise CredentialsRequired

    return _issue_tokens(user, clock())


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every session token issued to the caller",
)
async def logout(
    user: User = Depends(get_current_user),
    session: AsyncSession = session_dependency,
) -> None:
    """Bump the account's ``token_version``, invalidating its issued tokens.

    Revocation is account-wide rather than per-device: signing out ends
    every active session for that person, which is the safer default for a
    surface that reads confession content.
    """
    user.token_version += 1
    await session.flush()


@router.get("/me", response_model=UserResponse, summary="Return the signed-in account")
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Return the identity behind the presented session token."""
    return user


def _issue_tokens(user: User, now: datetime) -> TokenResponse:
    """Build a token pair and identity payload for *user* at *now*."""
    return TokenResponse(
        access_token=create_access_token(user, now),
        refresh_token=create_refresh_token(user, now),
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )
