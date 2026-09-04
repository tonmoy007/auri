"""Recipient departments a confession can be forwarded to.

Replaces two copies of the same list that could silently disagree: the
backend's ``DEPARTMENTS`` env string (6.6) and the bot's
``DEPARTMENT_CHAT_IDS`` mapping (4.5). A department that existed in one but
not the other produced confessions accepted by the API and then dropped by
the bot with only a log line to show for it.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Department(Base):
    """One recipient department and where its confessions are delivered."""

    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        comment="Display name, as chosen in the mobile Forward screen",
    )
    telegram_chat_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Chat the bot delivers to; null means nothing can be delivered yet",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Inactive departments stay for history but accept no new forwards",
    )
