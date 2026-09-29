"""Aggregate confession reporting with k-anonymity suppression.

A chart is a de-anonymisation vector the moment a bucket is small enough to
point at a person: "2 people in a 3-person team logged something negative
this week" identifies them. Every bucket below ``ANALYTICS_MIN_COHORT`` is
therefore returned as *suppressed* — no count, not a zero — and the UI is
told to say so explicitly rather than draw a gap.

Suppression is applied here, server-side, before anything is serialised.
The API never sends a number it then asks the client to hide.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confession import Confession, ConfessionStatus
from app.services.settings_service import get_config

logger = logging.getLogger(__name__)

DEFAULT_RANGE_DAYS = 30
SENTIMENTS: tuple[str, ...] = ("negative", "neutral", "positive")
UNLABELLED = "unlabelled"


def min_cohort() -> int:
    """Return the smallest bucket size that may be reported.

    Read through the live-config layer so an operator can raise it without a
    redeploy; a value below 2 is ignored, since a bucket of one *is* a
    person.
    """
    raw = get_config("ANALYTICS_MIN_COHORT", str(settings.ANALYTICS_MIN_COHORT))
    try:
        configured = int(raw)
    except ValueError:
        logger.warning("ANALYTICS_MIN_COHORT=%r is not an integer; using default", raw)
        return settings.ANALYTICS_MIN_COHORT
    return max(configured, 2)


@dataclass(frozen=True)
class Bucket:
    """One reportable number, or an explicit refusal to report it."""

    label: str
    count: int | None
    suppressed: bool


def _bucket(label: str, count: int, threshold: int) -> Bucket:
    """Return *count* for *label*, suppressed if it is a small non-zero cohort.

    Zero is always reported: "nobody" describes no one. Anything between 1
    and *threshold* - 1 is withheld.
    """
    if 0 < count < threshold:
        return Bucket(label=label, count=None, suppressed=True)
    return Bucket(label=label, count=count, suppressed=False)


def suppress_small_cohort(label: str, count: int, threshold: int) -> Bucket:
    """Apply the suppression rule to one count (shared with theme reporting).

    Zero is reported; 1 to *threshold* - 1 comes back suppressed.
    """
    return _bucket(label, count, threshold)


@dataclass(frozen=True)
class SentimentPoint:
    """Sentiment split for a single week."""

    label: str
    buckets: list[Bucket]


@dataclass(frozen=True)
class Insights:
    """Everything the Insights tab renders, already suppressed."""

    range_start: datetime
    range_end: datetime
    min_cohort: int
    total: Bucket
    volume_by_day: list[Bucket]
    volume_by_week: list[Bucket]
    by_category: list[Bucket]
    by_sentiment: list[Bucket]
    by_department: list[Bucket]
    forwarded: Bucket
    blind: Bucket
    flagged: Bucket
    flagged_rate: float | None
    delivered: Bucket
    median_hours_to_delivery: float | None
    sentiment_trend: list[SentimentPoint]


def _week_label(moment: date) -> str:
    """Return the ISO week label (``2026-W36``) *moment* falls in."""
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _day_labels(start: date, end: date) -> list[date]:
    """Return every date from *start* to *end* inclusive."""
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def _grouped_counts(
    session: AsyncSession, column, start: datetime, end: datetime
) -> dict[str, int]:
    """Return counts grouped by *column* over the visible confessions in range."""
    stmt = (
        select(column, func.count())
        .where(
            Confession.status != ConfessionStatus.deleted,
            Confession.created_at >= start,
            Confession.created_at <= end,
        )
        .group_by(column)
    )
    result = await session.execute(stmt)
    return {(value or UNLABELLED): count for value, count in result.all()}


async def _timestamps(
    session: AsyncSession, start: datetime, end: datetime
) -> list[tuple[datetime, datetime | None]]:
    """Return (created_at, delivered_at) for visible confessions in range.

    Only timestamps are selected — this function never touches confession
    content, so the volume and latency charts are computed from metadata
    alone.
    """
    stmt = select(Confession.created_at, Confession.delivered_at).where(
        Confession.status != ConfessionStatus.deleted,
        Confession.created_at >= start,
        Confession.created_at <= end,
    )
    result = await session.execute(stmt)
    return [(created, delivered) for created, delivered in result.all()]


def _volume_buckets(
    rows: list[tuple[datetime, datetime | None]],
    start: datetime,
    end: datetime,
    threshold: int,
) -> tuple[list[Bucket], list[Bucket]]:
    """Return (per-day, per-week) volume buckets across the whole range."""
    per_day = Counter(created.date() for created, _ in rows)
    days = _day_labels(start.date(), end.date())
    daily = [_bucket(day.isoformat(), per_day.get(day, 0), threshold) for day in days]

    per_week: Counter[str] = Counter()
    for day in days:
        per_week.setdefault(_week_label(day), 0)
    for created, _ in rows:
        per_week[_week_label(created.date())] += 1
    weekly = [
        _bucket(label, count, threshold) for label, count in sorted(per_week.items())
    ]
    return daily, weekly


def _median_hours_to_delivery(
    rows: list[tuple[datetime, datetime | None]], threshold: int
) -> float | None:
    """Return the median hours from submission to delivery, or ``None``.

    Suppressed when too few confessions have been delivered: a median over
    two items is two people's timing.
    """
    deltas = [
        (delivered - created).total_seconds() / 3600
        for created, delivered in rows
        if delivered is not None
    ]
    if len(deltas) < threshold:
        return None
    return round(statistics.median(deltas), 2)


def _sentiment_trend(
    per_week_sentiment: dict[str, Counter[str]], threshold: int
) -> list[SentimentPoint]:
    """Return the weekly sentiment split, each bucket suppressed on its own."""
    return [
        SentimentPoint(
            label=week,
            buckets=[
                _bucket(sentiment, counts.get(sentiment, 0), threshold)
                for sentiment in SENTIMENTS
            ],
        )
        for week, counts in sorted(per_week_sentiment.items())
    ]


async def _weekly_sentiment(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[str, Counter[str]]:
    """Return per-ISO-week sentiment counts (timestamps + label only)."""
    stmt = select(Confession.created_at, Confession.sentiment).where(
        Confession.status != ConfessionStatus.deleted,
        Confession.created_at >= start,
        Confession.created_at <= end,
    )
    result = await session.execute(stmt)
    per_week: dict[str, Counter[str]] = {}
    for created, sentiment in result.all():
        week = per_week.setdefault(_week_label(created.date()), Counter())
        week[sentiment or UNLABELLED] += 1
    return per_week


async def build_insights(
    session: AsyncSession, start: datetime, end: datetime
) -> Insights:
    """Aggregate confession metadata over ``[start, end]`` with suppression applied.

    Args:
        session: Active database session.
        start: Inclusive start of the reporting window.
        end: Inclusive end of the reporting window.

    Returns:
        A fully-suppressed :class:`Insights` payload — no caller needs to
        apply any further privacy rule.
    """
    threshold = min_cohort()

    by_category = await _grouped_counts(session, Confession.category, start, end)
    by_sentiment = await _grouped_counts(session, Confession.sentiment, start, end)
    by_department = await _grouped_counts(
        session, Confession.recipient_dept, start, end
    )
    by_status = await _grouped_counts(session, Confession.status, start, end)
    rows = await _timestamps(session, start, end)
    weekly_sentiment = await _weekly_sentiment(session, start, end)

    total_count = len(rows)
    status_counts = {
        (key.value if isinstance(key, ConfessionStatus) else key): value
        for key, value in by_status.items()
    }
    forwarded_count = status_counts.get(ConfessionStatus.forwarded.value, 0)
    flagged_count = status_counts.get(ConfessionStatus.flagged.value, 0)
    delivered_count = sum(1 for _, delivered in rows if delivered is not None)

    daily, weekly = _volume_buckets(rows, start, end, threshold)
    flagged_bucket = _bucket("flagged", flagged_count, threshold)

    return Insights(
        range_start=start,
        range_end=end,
        min_cohort=threshold,
        total=_bucket("total", total_count, threshold),
        volume_by_day=daily,
        volume_by_week=weekly,
        by_category=[
            _bucket(label, count, threshold)
            for label, count in sorted(by_category.items())
        ],
        by_sentiment=[
            _bucket(label, count, threshold)
            for label, count in sorted(by_sentiment.items())
        ],
        by_department=[
            _bucket(label, count, threshold)
            for label, count in sorted(by_department.items())
        ],
        forwarded=_bucket("forwarded", forwarded_count, threshold),
        # "Blind" = submitted but never sent to a department: the confessor
        # spoke without asking anyone to act on it.
        blind=_bucket("blind", total_count - forwarded_count, threshold),
        flagged=flagged_bucket,
        flagged_rate=(
            round(flagged_count / total_count, 4)
            if not flagged_bucket.suppressed and total_count >= threshold
            else None
        ),
        delivered=_bucket("delivered", delivered_count, threshold),
        median_hours_to_delivery=_median_hours_to_delivery(rows, threshold),
        sentiment_trend=_sentiment_trend(weekly_sentiment, threshold),
    )
