"""Which staff account a verified single sign-on identity belongs to (plan 15.10).

SSO never creates an account: there is still no self-signup, and an admin creates
every account first. The first SSO sign-in links the account whose email matches the
ID token's (verified) email, recording the provider identity (issuer and subject) on
it. From then on the account is found by that identity alone, so a later email
change at the provider cannot move the sign-in to someone else's account, and an
account already linked to one identity is never re-linked to another.

With ``OIDC_ROLE_CLAIM`` set, the provider decides the role at every sign-in. A role
change bumps the account's ``token_version``, ending every session issued under the
old role.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidEmailError, OidcError
from app.models.user import User, UserRole
from app.services.oidc import OidcConfig
from app.services.user_service import get_user_by_email, normalize_email

# Highest first: with several mapped values the most privileged one wins, as it would
# if an admin assigned the role by hand.
_ROLE_ORDER: Final = (UserRole.admin, UserRole.hr, UserRole.moderator)


def subject_of(config: OidcConfig, claims: dict[str, Any]) -> str:
    """The stored identity: issuer and subject, which together are unique."""
    return f"{config.issuer}|{claims['sub']}"


def _email_verified(claims: dict[str, Any]) -> bool:
    value = claims.get("email_verified")
    return value is True or (isinstance(value, str) and value.lower() == "true")


def mapped_role(config: OidcConfig, claims: dict[str, Any]) -> UserRole | None:
    """The role the provider grants through ``OIDC_ROLE_MAP``, or ``None``."""
    raw = claims.get(config.role_claim)
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    granted = {
        config.role_map[v]
        for v in values
        if isinstance(v, str) and v in config.role_map
    }
    return next((role for role in _ROLE_ORDER if role in granted), None)


async def _link_by_email(
    session: AsyncSession, config: OidcConfig, claims: dict[str, Any], subject: str
) -> User:
    if config.require_email_verified and not _email_verified(claims):
        raise OidcError("no_account", "first SSO sign-in needs a verified email")
    raw_email = claims.get("email")
    if not isinstance(raw_email, str):
        raise OidcError("no_account", "ID token carries no email")
    try:
        email = normalize_email(raw_email)
    except InvalidEmailError as exc:
        raise OidcError("no_account", "ID token email is not usable") from exc
    user = await get_user_by_email(session, email)
    if user is None:
        raise OidcError("no_account", "no staff account has this email")
    if user.oidc_subject is not None:
        raise OidcError("no_account", "account is linked to another SSO identity")
    user.oidc_subject = subject
    return user


async def resolve_account(
    session: AsyncSession, config: OidcConfig, claims: dict[str, Any]
) -> tuple[User, str | None]:
    """The active account for *claims*, and a note of any role change.

    The caller commits.

    Raises:
        OidcError: ``no_account`` when no active account can be matched, or the
            provider grants no mapped role.
    """
    subject = subject_of(config, claims)
    user = (
        await session.execute(select(User).where(User.oidc_subject == subject))
    ).scalar_one_or_none()
    if user is None:
        user = await _link_by_email(session, config, claims, subject)
    if not user.is_active:
        raise OidcError("no_account", "account is deactivated")

    role_change: str | None = None
    if config.role_claim:
        role = mapped_role(config, claims)
        if role is None:
            raise OidcError("no_account", "provider grants no mapped role")
        if role != user.role:
            role_change = f"role {user.role.value} -> {role.value}"
            user.role = role
            user.token_version += 1
    await session.flush()
    return user, role_change
