"""Append-only record of each retention job run.

The retention job is a cron entry outside the API process, so nothing in the
product could say whether it had ever run. Each run leaves one row here, which
is what lets the Privacy panel show HR that the promise "we delete after N
hours" is actually being kept, and warn when it is not.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RetentionRun(Base):
    """One execution of the retention job and what it did."""

    __tablename__ = "retention_runs"

    __table_args__ = (Index("ix_retention_runs_ran_at", "ran_at"),)

    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the run happened, from the job's own injected clock",
    )
    retention_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="The confession window this run enforced, so the log can prove the published one",
    )
    reply_retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="The reply window this run enforced",
    )
    deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Confessions hard-deleted outright"
    )
    emptied_to_shell: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Replied confessions emptied to a reply-only shell",
    )
    expired_replies: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Replied confessions deleted for an expired reply",
    )
