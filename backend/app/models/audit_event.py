"""Append-only record of what staff did with confession content.

Employees are told the booth is anonymous. That promise is only as good as
the organisation's ability to show who read what — so every staff read or
mutation of confession content writes a row here, and nothing in the API
can update or delete one.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditAction(str, enum.Enum):
    """What the actor did.

    Stored as a plain string rather than a database enum: new audited
    actions land with later tasks, and adding one should not require an
    ``ALTER TYPE`` migration on a table that must never be rewritten.
    """

    confession_list = "confession.list"
    confession_read = "confession.read"
    moderation_approve = "moderation.approve"
    moderation_reject = "moderation.reject"
    crisis_acknowledge = "crisis.acknowledge"
    hr_reply_write = "hr_reply.write"
    delivery_retry = "delivery.retry"
    department_write = "department.write"
    insights_read = "insights.read"


class ContentTier(str, enum.Enum):
    """How much of a confession the actor was served."""

    summary = "summary"
    raw = "raw"


class AuditEvent(Base):
    """One staff action against confession content."""

    __tablename__ = "audit_events"

    __table_args__ = (
        Index("ix_audit_events_actor_user_id", "actor_user_id"),
        Index("ix_audit_events_target_confession_id", "target_confession_id"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Staff account that performed the action; RESTRICT so history outlives account cleanup",
    )
    action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="An AuditAction value, e.g. 'confession.read'",
    )
    target_confession_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Confession acted on, when the action targets one. No FK: the row must survive the confession's retention purge",
    )
    content_tier: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="A ContentTier value when confession content was served",
    )
    justification: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reason the actor gave for a raw-transcript read",
    )
    source_ip: Mapped[str | None] = mapped_column(
        String(45),  # fits a full IPv6 literal
        nullable=True,
        comment="Client address the action arrived from, when known",
    )
