"""Anonymous user model for tracking device-level usage without PII."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
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
