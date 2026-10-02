"""Tests for matching a single sign-on identity to a staff account (plan 15.10)."""

from __future__ import annotations

from typing import Any

import pytest
from app.exceptions import OidcError
from app.models.user import User, UserRole
from app.services.oidc_accounts import mapped_role, resolve_account
from app.services.user_service import create_user
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fake_oidc import ISSUER, make_config

PASSWORD = "correct horse battery staple"


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": "user-123",
        "email": "hr@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


async def _account(
    session: AsyncSession, email: str = "hr@example.com", role: UserRole = UserRole.hr
) -> User:
    user = await create_user(session, email, PASSWORD, role)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_the_first_sign_in_links_the_account_with_the_verified_email(
    db_session: AsyncSession,
) -> None:
    user = await _account(db_session)

    found, role_change = await resolve_account(db_session, make_config(), _claims())

    assert found.id == user.id
    assert found.oidc_subject == f"{ISSUER}|user-123"
    assert role_change is None


@pytest.mark.asyncio
async def test_later_sign_ins_follow_the_identity_not_the_email(
    db_session: AsyncSession,
) -> None:
    # Arrange — linked, then the person's email changes at the provider to another
    # staff member's address
    user = await _account(db_session)
    await _account(db_session, email="admin@example.com", role=UserRole.admin)
    await resolve_account(db_session, make_config(), _claims())

    # Act
    found, _ = await resolve_account(
        db_session, make_config(), _claims(email="admin@example.com")
    )

    # Assert — still the original account
    assert found.id == user.id


@pytest.mark.asyncio
async def test_an_unverified_email_cannot_link(db_session: AsyncSession) -> None:
    await _account(db_session)
    with pytest.raises(OidcError) as caught:
        await resolve_account(db_session, make_config(), _claims(email_verified=False))
    assert caught.value.code == "no_account"


@pytest.mark.asyncio
async def test_a_provider_without_email_verified_can_be_trusted_by_setting(
    db_session: AsyncSession,
) -> None:
    user = await _account(db_session)
    claims = _claims()
    del claims["email_verified"]

    found, _ = await resolve_account(
        db_session, make_config(require_email_verified=False), claims
    )

    assert found.id == user.id


@pytest.mark.asyncio
async def test_sso_never_creates_an_account(db_session: AsyncSession) -> None:
    with pytest.raises(OidcError) as caught:
        await resolve_account(db_session, make_config(), _claims())
    assert caught.value.code == "no_account"


@pytest.mark.asyncio
async def test_an_account_linked_to_another_identity_is_not_relinked(
    db_session: AsyncSession,
) -> None:
    await _account(db_session)
    await resolve_account(db_session, make_config(), _claims())

    with pytest.raises(OidcError):
        await resolve_account(db_session, make_config(), _claims(sub="someone-else"))


@pytest.mark.asyncio
async def test_a_deactivated_account_is_refused(db_session: AsyncSession) -> None:
    user = await _account(db_session)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(OidcError) as caught:
        await resolve_account(db_session, make_config(), _claims())
    assert caught.value.code == "no_account"


def test_the_most_privileged_mapped_role_wins() -> None:
    config = make_config(
        role_claim="groups",
        role_map={"g-mod": UserRole.moderator, "g-admin": UserRole.admin},
    )
    assert mapped_role(config, {"groups": ["g-mod", "g-admin", "x"]}) == UserRole.admin
    assert mapped_role(config, {"groups": "g-mod"}) == UserRole.moderator
    assert mapped_role(config, {"groups": ["x"]}) is None
    assert mapped_role(config, {}) is None


@pytest.mark.asyncio
async def test_the_provider_role_replaces_the_account_role_and_ends_old_sessions(
    db_session: AsyncSession,
) -> None:
    user = await _account(db_session, role=UserRole.hr)
    version = user.token_version
    config = make_config(role_claim="groups", role_map={"g-admin": UserRole.admin})

    found, role_change = await resolve_account(
        db_session, config, _claims(groups=["g-admin"])
    )

    assert found.role == UserRole.admin
    assert found.token_version == version + 1
    assert role_change == "role hr -> admin"


@pytest.mark.asyncio
async def test_no_mapped_role_means_no_sign_in(db_session: AsyncSession) -> None:
    await _account(db_session)
    config = make_config(role_claim="groups", role_map={"g-admin": UserRole.admin})
    with pytest.raises(OidcError) as caught:
        await resolve_account(db_session, config, _claims(groups=["everyone"]))
    assert caught.value.code == "no_account"


@pytest.mark.asyncio
async def test_without_a_role_claim_the_auri_role_is_kept(
    db_session: AsyncSession,
) -> None:
    user = await _account(db_session, role=UserRole.moderator)
    version = user.token_version

    found, role_change = await resolve_account(
        db_session, make_config(), _claims(groups=["g-admin"])
    )

    assert found.role == UserRole.moderator
    assert found.token_version == version
    assert role_change is None
