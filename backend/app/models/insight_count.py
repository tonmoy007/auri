"""Per-day counts of confessions that left at retention (plan 14.12).

Insights reports frozen weekly figures, but confessions are removed after
``RETENTION_HOURS``. Just before the retention job deletes or empties a row it
adds the row's metadata to these counts: the day it was made, then one label per
dimension (category, sentiment, department, status, delivered or not, and how
long delivery took). Nothing else is kept: no text, no ids, no device hash, no
time of day. Small counts are suppressed when they are read, never stored
differently, so the stored figure is exact and the rule can change later.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InsightDailyCount(Base):
    """How many confessions made on ``day`` carried ``label`` for ``dimension``."""

    __tablename__ = "insight_daily_counts"

    __table_args__ = (
        UniqueConstraint("day", "dimension", "label", name="uq_insight_daily_counts"),
    )

    day: Mapped[date] = mapped_column(
        Date, nullable=False, comment="UTC date the confessions were made"
    )
    dimension: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="total, category, sentiment, department, status, delivery or delivery_hours",
    )
    label: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="The value counted, e.g. a category name"
    )
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Confessions folded in so far; never decreases"
    )
