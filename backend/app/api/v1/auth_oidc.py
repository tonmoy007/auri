"""Staff single sign-on through an OpenID Connect provider (plan 15.10).

``GET /auth/oidc`` tells the login screen whether to offer the button.
``GET /auth/oidc/start`` sends the browser to the provider; the provider sends it
back to ``GET /auth/oidc/callback``, which verifies the sign-in, finds the account
and returns the browser to the dashboard with a one-time code in the URL fragment.
The dashboard trades that code at ``POST /auth/oidc/exchange`` for the same session
tokens a password login issues. Session tokens never appear in a URL.

The ``state`` is also set in an HttpOnly cookie scoped to these routes and must
match on the callback, so a sign-in started in another browser cannot be completed
in this one. Every failure returns the browser to the dashboard with one of a fixed
set of codes (``failed``, ``no_account``, ``cancelled``); the log gets the reason,
never a name or email.
"""

from __future__ import annotations

import logging
import secrets
from typing import Final
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CredentialsRequired
from app.api.v1.auth import ClockDependency, TokenResponse, get_clock, issue_tokens
from app.database import session_dependency
from app.exceptions import OidcError
from app.models.audit_event import AuditAction
from app.models.user import User
from app.services import audit_service
from app.services.oidc import OidcProvider, get_provider, load_config, pkce_pair
from app.services.oidc_accounts import resolve_account
from app.services.oidc_sessions import ATTEMPT_TTL_SECONDS, oidc_sessions
from app.services.user_service import record_login

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

STATE_COOKIE: Final = "auri_oidc_state"
COOKIE_PATH: Final = "/api/v1/auth/oidc"


class OidcStatusResponse(BaseModel):
    """Whether single sign-on is offered, and the button's wording."""

    enabled: bool
    label: str | None


class ExchangeRequest(BaseModel):
    """The one-time code the callback put in the dashboard URL."""

    code: str = Field(..., min_length=1, max_length=256)


def _provider_or_404() -> OidcProvider:
    """The configured provider; 404 when SSO is off, 503 when misconfigured."""
    try:
        provider = get_provider()
    except OidcError as exc:
        logger.error("single sign-on is misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Single sign-on is misconfigured",
        ) from None
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return provider


def _to_dashboard(provider: OidcProvider, fragment: str) -> RedirectResponse:
    """Send the browser to the dashboard with *fragment*, clearing the state cookie."""
    base = provider.config.dashboard_url.split("#", 1)[0]
    response = RedirectResponse(f"{base}#{fragment}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    return response


@router.get("", response_model=OidcStatusResponse, summary="Is single sign-on offered")
async def oidc_status() -> OidcStatusResponse:
    """Whether the login screen should offer single sign-on."""
    try:
        config = load_config()
    except OidcError as exc:
        logger.error("single sign-on is misconfigured: %s", exc)
        config = None
    if config is None:
        return OidcStatusResponse(enabled=False, label=None)
    return OidcStatusResponse(enabled=True, label=config.label)


@router.get("/start", summary="Begin a single sign-on")
async def oidc_start(
    provider: OidcProvider = Depends(_provider_or_404),
    clock: ClockDependency = Depends(get_clock),
) -> RedirectResponse:
    """Redirect to the provider's sign-in page."""
    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(32)
    state = oidc_sessions.open_attempt(nonce, verifier, clock().timestamp())
    try:
        url = await provider.authorization_url(state, nonce, challenge)
    except OidcError as exc:
        logger.warning("single sign-on could not start: %s", exc)
        return _to_dashboard(provider, "oidc_error=failed")
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=int(ATTEMPT_TTL_SECONDS),
        path=COOKIE_PATH,
        httponly=True,
        secure=urlsplit(provider.config.redirect_uri).scheme == "https",
        samesite="lax",
    )
    return response


@router.get("/callback", summary="Finish a single sign-on")
async def oidc_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    provider: OidcProvider = Depends(_provider_or_404),
    session: AsyncSession = session_dependency,
    clock: ClockDependency = Depends(get_clock),
) -> RedirectResponse:
    """Verify the sign-in and hand the session to the dashboard."""
    now = clock()
    attempt = oidc_sessions.take_attempt(state, now.timestamp()) if state else None
    if error:
        # The person cancelled, or the provider refused; the attempt is spent.
        return _to_dashboard(provider, "oidc_error=cancelled")
    cookie = request.cookies.get(STATE_COOKIE, "")
    if (
        attempt is None
        or not code
        or not state
        or not secrets.compare_digest(cookie, state)
    ):
        logger.warning("single sign-on callback with an unknown or foreign state")
        return _to_dashboard(provider, "oidc_error=failed")
    try:
        id_token = await provider.exchange_code(code, attempt.verifier)
        claims = await provider.verify_id_token(id_token, attempt.nonce)
        user, role_change = await resolve_account(session, provider.config, claims)
    except OidcError as exc:
        logger.warning("single sign-on refused (%s): %s", exc.code, exc)
        await session.rollback()
        return _to_dashboard(provider, f"oidc_error={exc.code}")

    await record_login(session, user, now)
    handover = oidc_sessions.open_handover(user.id, now.timestamp())
    detail = f"provider {urlsplit(provider.config.issuer).hostname}"
    if role_change:
        detail = f"{detail}; {role_change}"
    await audit_service.record(
        session,
        user,
        AuditAction.auth_login_oidc,
        source_ip=audit_service.client_ip(request),
        detail=detail,
    )
    return _to_dashboard(provider, f"oidc_code={handover}")


@router.post(
    "/exchange",
    response_model=TokenResponse,
    summary="Trade a single sign-on handover code for a session",
)
async def oidc_exchange(
    body: ExchangeRequest,
    _provider: OidcProvider = Depends(_provider_or_404),
    session: AsyncSession = session_dependency,
    clock: ClockDependency = Depends(get_clock),
) -> TokenResponse:
    """Issue session tokens for the account a callback handed over, once."""
    now = clock()
    user_id = oidc_sessions.take_handover(body.code, now.timestamp())
    user = await session.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise CredentialsRequired
    return issue_tokens(user, now)
