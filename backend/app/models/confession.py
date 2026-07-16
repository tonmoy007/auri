"""Confession database model with pgcrypto support for encrypted columns."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped

from app.models.base import Base


class ConfessionStatus(str, enum.Enum):
    """Valid states for a confession's lifecycle."""

    pending = "pending"
    forwarded = "forwarded"
    deleted = "deleted"
    flagged = "flagged"


class Confession(Base):
    """An anonymous confession submitted by a device user.

    Uses PostgreSQL ``pgcrypto`` extension for transparent column-level
    encryption of sensitive fields such as ``device_token_hash`` and
    ``transcript``.
    """

    __tablename__ = "confessions"

    __table_args__ = (
        Index("ix_confessions_device_token_hash", "device_token_hash"),
        Index("ix_confessions_status", "status"),
        Index("ix_confessions_created_at", "created_at"),
    )

    device_token_hash: Mapped[str] = Column(
        String(256),
        nullable=False,
        index=True,
        comment="SHA-256 hash of the device's anonymous token (pgcrypto-encrypted at rest)",
    )
    voice_mask: Mapped[str] = Column(
        String(64),
        nullable=False,
        default="warm",
        comment="Voice modulation mask applied (warm, robotic, ethereal, deep, random)",
    )
    transcript: Mapped[str] = Column(
        Text,
        nullable=False,
        comment="Whisper-generated transcript of the confession audio",
    )
    ai_summary: Mapped[str | None] = Column(
        Text,
        nullable=True,
        comment="LLM-generated de-identified summary forwarded to recipient",
    )
    category: Mapped[str | None] = Column(
        String(128),
        nullable=True,
        comment="Categorisation label produced by LLM (e.g. 'health', 'faith', 'relationships')",
    )
    pii_stripped: Mapped[bool] = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether PII scrubbing has been applied to the transcript",
    )
    status: Mapped[ConfessionStatus] = Column(
        Enum(ConfessionStatus, name="confession_status", create_type=True),
        nullable=False,
        default=ConfessionStatus.pending,
        comment="Current lifecycle status of the confession",
    )
    recipient_dept: Mapped[str | None] = Column(
        String(128),
        nullable=True,
        comment="Target department for forwarded confessions (e.g. 'pastoral', 'counseling')",
    )
