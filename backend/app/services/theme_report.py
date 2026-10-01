"""Ranking themes and applying k-anonymity suppression to them.

A theme is a *small group with a name*. "Three people complained about the
Lagos office reorg" points at those three people far more sharply than a chart
bar does, so the rule here is stricter than for plain counts: a theme whose
current-period cohort is below ``ANALYTICS_MIN_COHORT`` is withheld entirely —
its label included — and only the *number* of withheld themes is reported.

Every figure is computed here from stored rows, never taken from the model:
the model proposes groupings, this module decides what may be shown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from app.services.insights_service import SENTIMENTS, Bucket, suppress_small_cohort
from app.services.theme_clustering import SummaryItem, ThemeGroup, Window

NEGATIVE = "negative"


class SentimentStatus(str, Enum):
    """Why a theme does or does not carry a sentiment comparison."""

    ok = "ok"
    suppressed = "suppressed"
    no_previous_period = "no_previous_period"


@dataclass(frozen=True)
class Theme:
    """One reportable recurring theme, already suppressed."""

    rank: int
    label: str
    confessions: int
    previous_period: Bucket
    negative_share: float | None
    previous_negative_share: float | None
    sentiment_change: float | None
    sentiment_status: SentimentStatus


@dataclass(frozen=True)
class ThemeSummary:
    """The visible, ranked themes plus how many were withheld."""

    themes: list[Theme]
    hidden_themes: int


@dataclass(frozen=True)
class ThemeReport:
    """Everything the Themes tab and the digest render."""

    range_start: datetime
    range_end: datetime
    days: int
    min_cohort: int
    method: str
    notice: str | None
    analysed: Bucket
    truncated: bool
    themes: list[Theme]
    hidden_themes: int


def negative_share(items: Sequence[SummaryItem], threshold: int) -> float | None:
    """Return the negative fraction of *items*, or ``None`` if it would identify someone.

    Only items with a recognised sentiment count. The share is withheld when
    fewer than *threshold* carry one, and also when the negative count or its
    complement is a small non-zero cohort: ``share x total`` gives the exact
    count back, so publishing 0.2 over five people states that exactly one is
    negative — a bucket ``/hr/insights`` would have suppressed.

    Args:
        items: The theme's members in one period.
        threshold: The minimum cohort (``ANALYTICS_MIN_COHORT``).

    Returns:
        The share rounded to two places, or ``None`` if withheld.
    """
    labelled = [item.sentiment for item in items if item.sentiment in SENTIMENTS]
    if len(labelled) < threshold:
        return None
    negatives = labelled.count(NEGATIVE)
    counts = (negatives, len(labelled) - negatives)
    if any(
        suppress_small_cohort("share", count, threshold).suppressed for count in counts
    ):
        return None
    return round(negatives / len(labelled), 2)


def _sentiment_change(
    current: float | None, previous: float | None, previous_count: int
) -> tuple[float | None, SentimentStatus]:
    """Return the negative-share change and why it is (not) available."""
    if previous_count == 0:
        return None, SentimentStatus.no_previous_period
    if current is None or previous is None:
        return None, SentimentStatus.suppressed
    return round(current - previous, 2), SentimentStatus.ok


def _members_by_window(
    items: Sequence[SummaryItem], group: ThemeGroup
) -> tuple[list[SummaryItem], list[SummaryItem]]:
    """Split a group's members into (current-period, previous-period) items."""
    members = [items[index] for index in group.members]
    return (
        [item for item in members if item.window is Window.current],
        [item for item in members if item.window is Window.previous],
    )


def _theme_for(
    group: ThemeGroup, items: Sequence[SummaryItem], threshold: int
) -> Theme | None:
    """Build the (unranked) theme for *group*, or ``None`` if it is not reportable.

    A group with no current-period members has nothing to say about now; a
    group below the cohort threshold is withheld by the caller.
    """
    current, previous = _members_by_window(items, group)
    if len(current) < threshold:
        return None
    current_share = negative_share(current, threshold)
    previous_share = negative_share(previous, threshold)
    change, status = _sentiment_change(current_share, previous_share, len(previous))
    return Theme(
        rank=0,
        label=group.label,
        confessions=len(current),
        previous_period=suppress_small_cohort("previous", len(previous), threshold),
        negative_share=current_share,
        previous_negative_share=previous_share,
        sentiment_change=change,
        sentiment_status=status,
    )


def _rank_key(theme: Theme) -> tuple[int, float, str]:
    """Order by volume, then by how much sentiment worsened, then by label."""
    worsened = theme.sentiment_change if theme.sentiment_change is not None else 0.0
    return (-theme.confessions, -worsened, theme.label)


def summarise_themes(
    items: Sequence[SummaryItem], groups: Sequence[ThemeGroup], threshold: int
) -> ThemeSummary:
    """Rank the reportable themes and count the withheld ones.

    Args:
        items: Every summary considered, current period and previous.
        groups: The proposed groupings, as indices into *items*.
        threshold: The minimum cohort (``ANALYTICS_MIN_COHORT``).

    Returns:
        Themes with at least *threshold* current-period confessions, ranked
        by volume then sentiment worsening, plus the count of groups that had
        current-period members but too few to show. A group with no
        current-period members is dropped silently.
    """
    themes: list[Theme] = []
    hidden = 0
    for group in groups:
        current, _ = _members_by_window(items, group)
        if not current:
            continue
        theme = _theme_for(group, items, threshold)
        if theme is None:
            hidden += 1
        else:
            themes.append(theme)

    ranked = [
        replace(theme, rank=position)
        for position, theme in enumerate(sorted(themes, key=_rank_key), start=1)
    ]
    return ThemeSummary(themes=ranked, hidden_themes=hidden)
