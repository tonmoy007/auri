"""Fold confessions that are leaving at retention into count-only rollups.

Insights reports frozen weekly counts (plan 14.1), but a confession is removed
``RETENTION_HOURS`` after its last change. The retention job calls
:func:`fold_leaving` in the same transaction, just before it deletes or empties
rows, so every confession that ever counted is added exactly once: a row is
folded only while its content is present, and the same run then removes or
empties it. Withdrawn confessions are never folded; Insights never counted them.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Final

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.confession import Confession, ConfessionStatus
from app.models.insight_count import InsightDailyCount

UNLABELLED: Final = "unlabelled"
_LABEL_CHARS: Final = 128
# Upper bounds in hours, in order; anything at or past the last is ">72h".
_DELIVERY_BUCKETS: Final = ((1, "<1h"), (6, "1-6h"), (24, "6-24h"), (72, "24-72h"))
_LONGEST_DELIVERY: Final = ">72h"

Key = tuple[date, str, str]


def delivery_bucket(elapsed: timedelta) -> str:
    """The delivery-time band for *elapsed*; bands, not exact times, are stored."""
    hours = elapsed.total_seconds() / 3600
    for limit, label in _DELIVERY_BUCKETS:
        if hours < limit:
            return label
    return _LONGEST_DELIVERY


def _utc_day(moment: datetime) -> date:
    """The UTC date of *moment*; a naive value (SQLite) is already UTC."""
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(timezone.utc).date()


def _labels(
    created: datetime,
    category: str | None,
    sentiment: str | None,
    department: str | None,
    status: ConfessionStatus,
    delivered: datetime | None,
) -> list[tuple[str, str]]:
    """One (dimension, label) pair per reported dimension for one confession."""
    pairs = [
        ("total", "all"),
        ("category", (category or UNLABELLED)[:_LABEL_CHARS]),
        ("sentiment", (sentiment or UNLABELLED)[:_LABEL_CHARS]),
        ("department", (department or UNLABELLED)[:_LABEL_CHARS]),
        ("status", status.value),
        ("delivery", "delivered" if delivered else "not_delivered"),
    ]
    if delivered is not None:
        pairs.append(("delivery_hours", delivery_bucket(delivered - created)))
    return pairs


async def _add_counts(session: AsyncSession, tally: Counter[Key]) -> None:
    """Add *tally* to the stored counts, creating rows that do not exist yet."""
    days = {day for day, _, _ in tally}
    stored = await session.execute(
        select(InsightDailyCount).where(InsightDailyCount.day.in_(days))
    )
    by_key = {(r.day, r.dimension, r.label): r for r in stored.scalars()}
    for (day, dimension, label), amount in tally.items():
        row = by_key.get((day, dimension, label))
        if row is None:
            session.add(
                InsightDailyCount(
                    day=day, dimension=dimension, label=label, count=amount
                )
            )
        else:
            row.count += amount
    await session.flush()


async def fold_leaving(session: AsyncSession, leaving: ColumnElement[bool]) -> int:
    """Add every confession matching *leaving* to the per-day counts.

    Args:
        session: The retention run's session (the caller commits).
        leaving: The predicate for rows this run will delete or empty.

    Returns:
        How many confessions were folded.
    """
    result = await session.execute(
        select(
            Confession.created_at,
            Confession.category,
            Confession.sentiment,
            Confession.recipient_dept,
            Confession.status,
            Confession.delivered_at,
        ).where(
            leaving,
            Confession.status != ConfessionStatus.deleted,
            Confession.purged_at.is_(None),
        )
    )
    tally: Counter[Key] = Counter()
    folded = 0
    for created, category, sentiment, dept, status, delivered in result.all():
        day = _utc_day(created)
        for dimension, label in _labels(
            created, category, sentiment, dept, status, delivered
        ):
            tally[(day, dimension, label)] += 1
        folded += 1
    if tally:
        await _add_counts(session, tally)
    return folded
