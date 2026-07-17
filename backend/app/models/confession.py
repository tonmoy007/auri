"""Confession database model."""

from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConfessionStatus(str, enum.Enum):
    """Valid states for a confession's lifecycle."""

    pending = "pending"
    forwarded = "forwarded"
    deleted = "deleted"
    flagged = "flagged"


class Confession(Base):
    """An anonymous confession submitted by a device user.

    ``device_token_hash`` and ``transcript`` are stored as plain
    ``String``/``Text`` columns — no column-level encryption is applied.
    """

    __tablename__ = "confessions"

    __table_args__ = (
        Index("ix_confessions_device_token_hash", "device_token_hash"),
        Index("ix_confessions_status", "status"),
        Index("ix_confessions_created_at", "created_at"),
    )

    device_token_hash: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="SHA-256 hash of the device's anonymous token",
    )
    voice_mask: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="warm",
        comment="Voice modulation mask applied (warm, robotic, ethereal, deep, random)",
    )
    transcript: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Whisper-generated transcript of the confession audio",
    )
    ai_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="LLM-generated de-identified summary forwarded to recipient",
    )
    category: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Categorisation label produced by LLM (e.g. 'health', 'faith', 'relationships')",
    )
    pii_stripped: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether PII scrubbing has been applied to the transcript",
    )
    status: Mapped[ConfessionStatus] = mapped_column(
        Enum(ConfessionStatus, name="confession_status", create_type=True),
        nullable=False,
        default=ConfessionStatus.pending,
        comment="Current lifecycle status of the confession",
    )
    recipient_dept: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Target department for forwarded confessions (e.g. 'pastoral', 'counseling')",
    )
