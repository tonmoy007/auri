"""User models: anonymous confessors (device-level) and named staff accounts.

The two are deliberately unrelated. :class:`AnonymousUser` tracks a device
token hash and holds no PII; :class:`User` is a named dashboard operator
(HR, moderator, admin) who signs in with an email and password. A staff
account is never linked to a confession's author — that link does not exist
anywhere in the schema, which is what makes the booth anonymous.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnonymousUser(Base):
    """Tracks an anonymous device by its token hash.

    No personally-identifiable information is ever stored.  The
    ``device_token_hash`` is a SHA-256 digest of a device-local UUID
    that the user can rotate at any time.
    """

    __tablename__ = "anonymous_users"

    device_token_hash: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hash of the device's anonymous token",
    )
    last_confession_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp of the user's most recent confession",
    )
    confession_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Running total of confessions submitted by this device",
    )


class UserRole(str, enum.Enum):
    """Access level of a staff account, checked by the role dependency."""

    admin = "admin"
    hr = "hr"
    moderator = "moderator"


class User(Base):
    """A named staff account that signs in to the dashboard.

    Replaces the single shared ``ADMIN_API_KEY`` for human operators: each
    person gets their own row, so access can be revoked individually and
    every confession-content read is attributable to a real actor.

    There is no self-signup — accounts are created by an admin, and the
    first admin is bootstrapped from the environment at startup (see
    ``app.services.user_service.bootstrap_admin``).
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
        comment="Login identifier, stored lower-cased and trimmed",
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Argon2id digest — the plaintext password is never stored",
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=True),
        nullable=False,
        comment="Access level: admin (everything), hr, or moderator (queue only)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Deactivated accounts fail authentication without being deleted",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the account's most recent successful login",
    )
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Bumped on logout — every session token carrying an older value is rejected",
    )
