"""Integration tests for the staff auth API (app.api.v1.auth) and role deps.

Nothing about auth is mocked — real Argon2 verification, real JWT signing,
real role checks against a real database (AGENTS.md §16.4).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.api.deps import require_role
from app.config import settings
from app.models.user import UserRole
from app.services.auth_tokens import create_access_token, create_refresh_token
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_PASSWORD, StaffFactory

LOGIN_PATH = "/api/v1/auth/login"


# ── Login ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_returns_tokens_and_identity_for_valid_credentials(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")

    # Act
    response = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )

    # Assert
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.ACCESS_TOKEN_TTL_MINUTES * 60
    assert body["user"]["email"] == "hr.lead@example.com"
    assert body["user"]["role"] == "hr"
    assert body["access_token"] != body["refresh_token"]


@pytest.mark.asyncio
async def test_login_is_case_insensitive_on_email(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")

    # Act
    response = await api_client.post(
        LOGIN_PATH, json={"email": "HR.Lead@Example.COM", "password": TEST_PASSWORD}
    )

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_login_stamps_last_login_at(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr, "hr.lead@example.com")

    # Act
    await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )

    # Assert
    await db_session.refresh(user)
    assert user.last_login_at is not None


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_with_401(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")

    # Act
    response = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": "wrong-password"}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_email_with_the_same_401(
    api_client: AsyncClient,
) -> None:
    # Arrange — no accounts exist; the reply must not reveal that
    # Act
    response = await api_client.post(
        LOGIN_PATH, json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    # Assert
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


@pytest.mark.asyncio
async def test_login_rejects_deactivated_account(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr, "gone@example.com")
    user.is_active = False
    await db_session.commit()

    # Act
    response = await api_client.post(
        LOGIN_PATH, json={"email": "gone@example.com", "password": TEST_PASSWORD}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_locks_out_after_the_configured_failure_count(
    api_client: AsyncClient, make_staff: StaffFactory, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)
    await make_staff(UserRole.hr, "hr.lead@example.com")
    bad_credentials = {"email": "hr.lead@example.com", "password": "wrong-password"}

    # Act
    for _ in range(3):
        await api_client.post(LOGIN_PATH, json=bad_credentials)
    locked_out = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )

    # Assert — even the *correct* password is refused once locked out
    assert locked_out.status_code == 429


@pytest.mark.asyncio
async def test_successful_login_clears_earlier_failures(
    api_client: AsyncClient, make_staff: StaffFactory, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)
    await make_staff(UserRole.hr, "hr.lead@example.com")
    good_credentials = {"email": "hr.lead@example.com", "password": TEST_PASSWORD}

    # Act
    await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": "wrong-password"}
    )
    await api_client.post(LOGIN_PATH, json=good_credentials)
    for _ in range(2):
        await api_client.post(
            LOGIN_PATH,
            json={"email": "hr.lead@example.com", "password": "wrong-password"},
        )
    still_allowed = await api_client.post(LOGIN_PATH, json=good_credentials)

    # Assert — the counter reset, so 1 + 2 failures never reached the limit
    assert still_allowed.status_code == 200


# ── Current identity ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_me_returns_the_signed_in_account(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")

    # Act
    response = await api_client.get("/api/v1/auth/me", headers=headers)

    # Assert
    assert response.status_code == 200
    assert response.json()["role"] == "moderator"


@pytest.mark.asyncio
async def test_me_rejects_a_request_with_no_token(api_client: AsyncClient) -> None:
    # Arrange / Act
    response = await api_client.get("/api/v1/auth/me")

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_a_token_signed_with_another_secret(
    api_client: AsyncClient, make_staff: StaffFactory, monkeypatch
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "a-different-secret")
    forged = create_access_token(user, datetime.now(timezone.utc))
    monkeypatch.setattr(
        settings, "SESSION_TOKEN_SECRET", "test-session-secret-not-a-real-one"
    )

    # Act
    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_a_refresh_token_used_as_an_access_token(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.hr)
    refresh_token = create_refresh_token(user, datetime.now(timezone.utc))

    # Act
    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_token}"}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_a_token_for_a_deactivated_account(
    api_client: AsyncClient, make_staff: StaffFactory, db_session: AsyncSession
) -> None:
    # Arrange
    user, headers = await make_staff(UserRole.hr)
    user.is_active = False
    await db_session.commit()

    # Act
    response = await api_client.get("/api/v1/auth/me", headers=headers)

    # Assert
    assert response.status_code == 401


# ── Refresh & logout ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_issues_a_new_session_from_a_refresh_token(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")
    login = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )

    # Act
    response = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "hr.lead@example.com"


@pytest.mark.asyncio
async def test_refresh_rejects_an_access_token(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")
    login = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )

    # Act
    response = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login.json()["access_token"]}
    )

    # Assert
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_the_tokens_it_was_called_with(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")
    login = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Act
    logout = await api_client.post("/api/v1/auth/logout", headers=headers)
    reused = await api_client.get("/api/v1/auth/me", headers=headers)

    # Assert
    assert logout.status_code == 204
    assert reused.status_code == 401


@pytest.mark.asyncio
async def test_logout_also_revokes_the_matching_refresh_token(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    await make_staff(UserRole.hr, "hr.lead@example.com")
    login = await api_client.post(
        LOGIN_PATH, json={"email": "hr.lead@example.com", "password": TEST_PASSWORD}
    )
    tokens = login.json()

    # Act
    await api_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    response = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    # Assert
    assert response.status_code == 401


# ── Role gating ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_role_allows_a_matching_role(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.moderator, "mod@example.com")
    moderator_only = require_role(UserRole.moderator)

    # Act
    allowed = await moderator_only(user)

    # Assert
    assert allowed.email == "mod@example.com"


@pytest.mark.asyncio
async def test_require_role_always_admits_an_admin(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — an admin reaches every surface without being listed per route
    user, _ = await make_staff(UserRole.admin, "admin@example.com")
    hr_only = require_role(UserRole.hr)

    # Act
    allowed = await hr_only(user)

    # Assert
    assert allowed.role == UserRole.admin


@pytest.mark.asyncio
async def test_require_role_raises_403_for_an_unlisted_role(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    user, _ = await make_staff(UserRole.moderator, "mod@example.com")
    hr_only = require_role(UserRole.hr)

    # Act / Assert
    with pytest.raises(HTTPException) as excinfo:
        await hr_only(user)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_require_role_rejects_a_role_without_access(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.moderator, "mod@example.com")

    # Act — /admin/config accepts only an admin session or the legacy key
    response = await api_client.get("/api/v1/admin/config", headers=headers)

    # Assert
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_session_reaches_admin_routes(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin, "admin@example.com")

    # Act
    response = await api_client.get("/api/v1/admin/config", headers=headers)

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_legacy_admin_api_key_still_reaches_admin_routes(
    api_client: AsyncClient, monkeypatch
) -> None:
    # Arrange — the Phase 10 shared secret must keep working (11.2 adds a
    # second auth path, it does not remove the first)
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "legacy-admin-secret")

    # Act
    response = await api_client.get(
        "/api/v1/admin/config", headers={"X-Admin-Api-Key": "legacy-admin-secret"}
    )

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_wrong_legacy_admin_api_key_is_refused(
    api_client: AsyncClient, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "legacy-admin-secret")

    # Act
    response = await api_client.get(
        "/api/v1/admin/config", headers={"X-Admin-Api-Key": "not-the-secret"}
    )

    # Assert
    assert response.status_code == 401
