"""Insights for one month, as fixed weeks read from the count-only rollups.

Weeks are month-aligned (days 1-7, 8-14, 15-21, then 22 to the month's end), so
every week sits inside exactly one month and no two reports overlap partly: HR
cannot subtract one from another to isolate a few days (privacy review row 17).
No month total is shown beside its weeks, because a total plus all but one week
would reveal that week. A week is reported only once it is frozen, that is, it
has ended and every confession made in it has been folded into
``insight_daily_counts`` by the retention job; from then on its figures, and so
which cells are withheld, never change.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.confession import Confession, ConfessionStatus
from app.models.insight_count import InsightDailyCount
from app.services.insight_rollup import DELIVERY_BANDS, UNLABELLED
from app.services.insight_suppression import suppress_partition
from app.services.insights_service import (
    SENTIMENTS,
    Bucket,
    min_cohort,
    suppress_small_cohort,
)

_WEEK_STARTS: Final = (1, 8, 15, 22)
_STATUSES: Final = tuple(
    s.value for s in ConfessionStatus if s is not ConfessionStatus.deleted
)
_DELIVERY: Final = ("delivered", "not_delivered")

Counts = dict[str, dict[str, int]]


@dataclass(frozen=True)
class WeekInsights:
    """One fixed week; every figure ``None``/empty until the week is frozen."""

    label: str
    start: date
    end: date
    frozen: bool
    total: Bucket | None
    by_day: list[Bucket]
    by_category: list[Bucket]
    by_sentiment: list[Bucket]
    by_department: list[Bucket]
    by_status: list[Bucket]
    delivery: list[Bucket]
    delivery_time: list[Bucket]


@dataclass(frozen=True)
class MonthInsights:
    """A month as its fixed weeks, already suppressed."""

    month: str
    min_cohort: int
    weeks: list[WeekInsights]


def month_weeks(year: int, month: int) -> list[tuple[str, date, date]]:
    """The month's four fixed weeks as (label, first day, last day)."""
    last = calendar.monthrange(year, month)[1]
    ends = (7, 14, 21, last)
    return [
        (f"W{n}", date(year, month, start), date(year, month, end))
        for n, (start, end) in enumerate(zip(_WEEK_STARTS, ends, strict=True), 1)
    ]


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """The UTC instants [start 00:00, the day after end 00:00)."""
    opening = datetime.combine(start, time.min, tzinfo=timezone.utc)
    return opening, datetime.combine(
        end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )


async def _is_frozen(
    session: AsyncSession, start: date, end: date, now: datetime
) -> bool:
    """Ended, and no confession of the week is still stored unfolded."""
    if now.astimezone(timezone.utc).date() <= end:
        return False
    opening, closing = _bounds(start, end)
    live = await session.scalar(
        select(func.count()).where(
            Confession.created_at >= opening,
            Confession.created_at < closing,
            Confession.status != ConfessionStatus.deleted,
            Confession.purged_at.is_(None),
        )
    )
    return not live


async def _week_counts(
    session: AsyncSession, start: date, end: date
) -> tuple[Counts, dict[date, int]]:
    """Per-dimension label totals for the week, and the per-day totals."""
    rows = await session.execute(
        select(
            InsightDailyCount.day,
            InsightDailyCount.dimension,
            InsightDailyCount.label,
            InsightDailyCount.count,
        ).where(InsightDailyCount.day >= start, InsightDailyCount.day <= end)
    )
    by_dimension: Counts = defaultdict(lambda: defaultdict(int))
    per_day: dict[date, int] = defaultdict(int)
    for day, dimension, label, count in rows.all():
        if dimension == "total":
            per_day[day] += count
        else:
            by_dimension[dimension][label] += count
    return by_dimension, per_day


def _parts(counts: dict[str, int], fixed: tuple[str, ...] = ()) -> dict[str, int]:
    """*counts* with every *fixed* label present (zero if absent), fixed ones first."""
    parts = {label: counts.get(label, 0) for label in fixed}
    for label in sorted(counts):
        parts.setdefault(label, counts[label])
    return parts


def _frozen_week(
    label: str, start: date, end: date, counts: Counts, per_day: dict[date, int], k: int
) -> WeekInsights:
    """Apply the suppression rules to one frozen week's counts."""
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    total = suppress_small_cohort("total", sum(per_day.values()), k)
    shown = not total.suppressed

    def split(dimension: str, fixed: tuple[str, ...] = ()) -> list[Bucket]:
        return suppress_partition(
            _parts(counts.get(dimension, {}), fixed), k, parent_shown=shown
        )

    delivery = split("delivery", _DELIVERY)
    delivered_shown = not next(b for b in delivery if b.label == "delivered").suppressed
    return WeekInsights(
        label=label,
        start=start,
        end=end,
        frozen=True,
        total=total,
        by_day=suppress_partition(
            {d.isoformat(): per_day.get(d, 0) for d in days}, k, parent_shown=shown
        ),
        by_category=split("category"),
        by_sentiment=split("sentiment", (*SENTIMENTS, UNLABELLED)),
        by_department=split("department"),
        by_status=split("status", _STATUSES),
        delivery=delivery,
        delivery_time=suppress_partition(
            _parts(counts.get("delivery_hours", {}), DELIVERY_BANDS),
            k,
            parent_shown=delivered_shown,
        ),
    )


async def build_month(
    session: AsyncSession, year: int, month: int, now: datetime
) -> MonthInsights:
    """Insights for *year*-*month*: its fixed weeks, frozen ones with figures.

    Args:
        session: Active database session.
        year: The calendar year.
        month: The calendar month, 1-12.
        now: Current time, injected.

    Returns:
        The month's weeks, every figure already suppressed.
    """
    k = min_cohort()
    weeks: list[WeekInsights] = []
    for label, start, end in month_weeks(year, month):
        if not await _is_frozen(session, start, end, now):
            weeks.append(
                WeekInsights(label, start, end, False, None, [], [], [], [], [], [], [])
            )
            continue
        counts, per_day = await _week_counts(session, start, end)
        weeks.append(_frozen_week(label, start, end, counts, per_day, k))
    return MonthInsights(month=f"{year:04d}-{month:02d}", min_cohort=k, weeks=weeks)
