"""End-to-end tests for the single sign-on routes (plan 15.10)."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from app.api.v1.auth_oidc import STATE_COOKIE, _provider_or_404
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.user import UserRole
from app.services.oidc_sessions import oidc_sessions
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_PASSWORD, SettingPatcher, StaffFactory
from tests.fake_oidc import CLIENT_ID, DASHBOARD_URL, ISSUER, REDIRECT_URI, FakeIdp

BASE = "/api/v1/auth/oidc"


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    """A fake provider wired into the routes, with clean single sign-on state."""
    fake = FakeIdp()
    provider = fake.provider()
    app.dependency_overrides[_provider_or_404] = lambda: provider
    oidc_sessions.clear()
    yield fake
    app.dependency_overrides.pop(_provider_or_404, None)
    oidc_sessions.clear()


async def _start(client: AsyncClient, idp: FakeIdp) -> str:
    """Begin a sign-in; return its state, and have the fake sign the right nonce."""
    response = await client.get(f"{BASE}/start")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    idp.claims = {"nonce": query["nonce"][0]}
    return query["state"][0]


def _fragment(response) -> dict[str, str]:  # type: ignore[no-untyped-def]
    location = response.headers["location"]
    assert location.startswith(DASHBOARD_URL.rstrip("/"))
    return {k: v[0] for k, v in parse_qs(urlsplit(location).fragment).items()}


# ── status ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_login_screen_hears_sso_is_off_by_default(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    set_setting("OIDC_ISSUER", "")
    body = (await api_client.get(BASE)).json()
    assert body == {"enabled": False, "label": None}


@pytest.mark.asyncio
async def test_the_login_screen_hears_sso_is_on_with_its_label(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    for key, value in {
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REDIRECT_URI": REDIRECT_URI,
        "OIDC_DASHBOARD_URL": DASHBOARD_URL,
        "OIDC_PROVIDER_LABEL": "Company SSO",
    }.items():
        set_setting(key, value)
    body = (await api_client.get(BASE)).json()
    assert body == {"enabled": True, "label": "Company SSO"}


@pytest.mark.asyncio
async def test_the_sign_in_routes_are_absent_while_sso_is_off(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    set_setting("OIDC_ISSUER", "")
    assert (await api_client.get(f"{BASE}/start")).status_code == 404
    response = await api_client.post(f"{BASE}/exchange", json={"code": "x"})
    assert response.status_code == 404


# ── the whole flow ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_full_sign_in_hands_the_dashboard_a_working_session(
    api_client: AsyncClient,
    make_staff: StaffFactory,
    db_session: AsyncSession,
    idp: FakeIdp,
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr, email="hr@example.com")
    state = await _start(api_client, idp)

    # Act
    callback = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "auth-code"}
    )
    handover = _fragment(callback)["oidc_code"]
    tokens = await api_client.post(f"{BASE}/exchange", json={"code": handover})

    # Assert
    assert tokens.status_code == 200
    body = tokens.json()
    assert body["user"]["email"] == "hr@example.com"
    me = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json()["id"] == str(user.id)
    # No session token in any URL
    assert body["access_token"] not in callback.headers["location"]
    # The sign-in is audited with the provider, never the email
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert [(e.action, e.detail) for e in events] == [
        ("auth.login_oidc", "provider idp.example.com")
    ]


@pytest.mark.asyncio
async def test_the_start_sets_a_private_state_cookie(
    api_client: AsyncClient, idp: FakeIdp
) -> None:
    response = await api_client.get(f"{BASE}/start")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{STATE_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth/oidc" in cookie


@pytest.mark.asyncio
async def test_a_handover_code_works_once(
    api_client: AsyncClient, make_staff: StaffFactory, idp: FakeIdp
) -> None:
    await make_staff(UserRole.hr, email="hr@example.com")
    state = await _start(api_client, idp)
    callback = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "c"}
    )
    handover = _fragment(callback)["oidc_code"]
    assert (
        await api_client.post(f"{BASE}/exchange", json={"code": handover})
    ).status_code == 200

    again = await api_client.post(f"{BASE}/exchange", json={"code": handover})

    assert again.status_code == 401


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_callback_from_another_browser_is_refused(
    api_client: AsyncClient, make_staff: StaffFactory, idp: FakeIdp
) -> None:
    # The state exists, but this browser does not hold its cookie (login CSRF)
    await make_staff(UserRole.hr, email="hr@example.com")
    state = await _start(api_client, idp)
    api_client.cookies.clear()

    response = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "c"}
    )

    assert _fragment(response) == {"oidc_error": "failed"}
    assert idp.token_requests == []


@pytest.mark.asyncio
async def test_a_replayed_callback_is_refused(
    api_client: AsyncClient, make_staff: StaffFactory, idp: FakeIdp
) -> None:
    await make_staff(UserRole.hr, email="hr@example.com")
    state = await _start(api_client, idp)
    cookie = api_client.cookies.get(STATE_COOKIE)
    await api_client.get(f"{BASE}/callback", params={"state": state, "code": "c"})
    api_client.cookies.set(STATE_COOKIE, cookie or "", path="/api/v1/auth/oidc")

    response = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "c"}
    )

    assert _fragment(response) == {"oidc_error": "failed"}


@pytest.mark.asyncio
async def test_cancelling_at_the_provider_returns_cancelled(
    api_client: AsyncClient, idp: FakeIdp
) -> None:
    state = await _start(api_client, idp)
    response = await api_client.get(
        f"{BASE}/callback", params={"state": state, "error": "access_denied"}
    )
    assert _fragment(response) == {"oidc_error": "cancelled"}


@pytest.mark.asyncio
async def test_an_identity_with_no_staff_account_gets_no_account(
    api_client: AsyncClient, db_session: AsyncSession, idp: FakeIdp
) -> None:
    state = await _start(api_client, idp)
    response = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "c"}
    )
    assert _fragment(response) == {"oidc_error": "no_account"}
    assert (await db_session.execute(select(AuditEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_forged_id_token_is_refused(
    api_client: AsyncClient, make_staff: StaffFactory, idp: FakeIdp
) -> None:
    await make_staff(UserRole.hr, email="hr@example.com")
    state = await _start(api_client, idp)
    idp.id_token = FakeIdp().sign(nonce=idp.claims["nonce"])  # another key

    response = await api_client.get(
        f"{BASE}/callback", params={"state": state, "code": "c"}
    )

    assert _fragment(response) == {"oidc_error": "failed"}


@pytest.mark.asyncio
async def test_password_login_still_works_alongside_sso(
    api_client: AsyncClient, make_staff: StaffFactory, idp: FakeIdp
) -> None:
    await make_staff(UserRole.hr, email="hr@example.com")
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "hr@example.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
