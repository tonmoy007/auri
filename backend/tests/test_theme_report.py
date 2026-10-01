"""Tests for theme ranking and its k-anonymity suppression.

A theme is a small group with a name, so the rule is tested at its exact
boundary: N-1 current-period members is withheld *with its label*, N is shown
(AGENTS.md §16.2).
"""

from __future__ import annotations

import pytest
from app.services.theme_clustering import SummaryItem, ThemeGroup, Window
from app.services.theme_report import SentimentStatus, negative_share, summarise_themes

THRESHOLD = 3


def _items(
    count: int, window: Window = Window.current, sentiment: str | None = "neutral"
) -> list[SummaryItem]:
    return [
        SummaryItem(text=f"s{n}", sentiment=sentiment, category=None, window=window)
        for n in range(count)
    ]


def _group(label: str, start: int, count: int) -> ThemeGroup:
    return ThemeGroup(label=label, members=tuple(range(start, start + count)))


def test_theme_with_threshold_members_is_shown() -> None:
    # Arrange
    items = _items(THRESHOLD)

    # Act
    summary = summarise_themes(items, [_group("Pay", 0, THRESHOLD)], THRESHOLD)

    # Assert
    assert [(t.label, t.confessions) for t in summary.themes] == [("Pay", THRESHOLD)]
    assert summary.hidden_themes == 0


def test_theme_one_below_the_threshold_is_withheld_with_its_label() -> None:
    # Arrange
    items = _items(THRESHOLD - 1)

    # Act
    summary = summarise_themes(
        items, [_group("Sensitive", 0, THRESHOLD - 1)], THRESHOLD
    )

    # Assert
    assert summary.themes == []
    assert summary.hidden_themes == 1


def test_group_with_no_current_period_members_is_dropped_not_counted_as_hidden() -> (
    None
):
    # Arrange
    items = _items(5, window=Window.previous)

    # Act
    summary = summarise_themes(items, [_group("Faded", 0, 5)], THRESHOLD)

    # Assert
    assert summary.themes == []
    assert summary.hidden_themes == 0


def test_small_previous_period_is_suppressed_and_so_is_the_sentiment_change() -> None:
    # Arrange — 3 now, 2 before: the earlier count identifies people
    items = _items(3) + _items(2, window=Window.previous)

    # Act
    theme = summarise_themes(items, [_group("Pay", 0, 5)], THRESHOLD).themes[0]

    # Assert
    assert theme.previous_period.count is None
    assert theme.previous_period.suppressed is True
    assert theme.sentiment_status is SentimentStatus.suppressed
    assert theme.sentiment_change is None


def test_no_previous_period_members_reports_zero_and_new() -> None:
    # Arrange
    items = _items(3)

    # Act
    theme = summarise_themes(items, [_group("Pay", 0, 3)], THRESHOLD).themes[0]

    # Assert
    assert theme.previous_period.count == 0
    assert theme.previous_period.suppressed is False
    assert theme.sentiment_status is SentimentStatus.no_previous_period


def test_sentiment_change_is_current_negative_share_minus_previous() -> None:
    # Arrange — 6 of 9 negative now, 3 of 9 before (both counts clear the cohort)
    current = _items(6, sentiment="negative") + _items(3, sentiment="positive")
    previous = _items(3, Window.previous, "negative") + _items(
        6, Window.previous, "positive"
    )

    # Act
    theme = summarise_themes(
        current + previous, [_group("Pay", 0, 18)], THRESHOLD
    ).themes[0]

    # Assert
    assert theme.negative_share == 0.67
    assert theme.previous_negative_share == 0.33
    assert theme.sentiment_change == 0.34
    assert theme.sentiment_status is SentimentStatus.ok


def test_share_is_withheld_when_too_few_members_carry_a_sentiment() -> None:
    # Arrange — three people, but only two were ever labelled
    items = _items(2, sentiment="negative") + _items(1, sentiment=None)

    # Act
    share = negative_share(items, THRESHOLD)

    # Assert
    assert share is None


def test_themes_rank_by_volume_then_by_worsening_sentiment() -> None:
    # Arrange — B and C tie on volume; C's sentiment worsened, B's did not
    big = _items(5)
    b_now = _items(3, sentiment="neutral")
    b_before = _items(3, Window.previous, "neutral")
    c_now = _items(3, sentiment="negative")
    c_before = _items(3, Window.previous, "positive")
    items = big + b_now + b_before + c_now + c_before
    groups = [
        _group("B", 5, 6),
        _group("A", 0, 5),
        _group("C", 11, 6),
    ]

    # Act
    themes = summarise_themes(items, groups, THRESHOLD).themes

    # Assert
    assert [(t.rank, t.label) for t in themes] == [(1, "A"), (2, "C"), (3, "B")]


@pytest.mark.parametrize(
    ("negative", "other"),
    [(1, 4), (2, 3), (4, 1), (3, 2)],
)
def test_share_is_withheld_when_the_negative_count_or_its_complement_is_small(
    negative: int, other: int
) -> None:
    # Arrange — share x total would hand back exactly 1 or 2 people
    items = _items(negative, sentiment="negative") + _items(other, sentiment="positive")

    # Act
    share = negative_share(items, THRESHOLD)

    # Assert
    assert share is None


@pytest.mark.parametrize(
    ("negative", "other", "expected"), [(3, 3, 0.5), (0, 5, 0.0), (5, 0, 1.0)]
)
def test_share_is_shown_when_each_side_is_zero_or_at_least_the_cohort(
    negative: int, other: int, expected: float
) -> None:
    # Arrange — zero is reported as zero, exactly as /hr/insights does
    items = _items(negative, sentiment="negative") + _items(other, sentiment="positive")

    # Act
    share = negative_share(items, THRESHOLD)

    # Assert
    assert share == expected


def test_equal_volume_and_sentiment_rank_by_label() -> None:
    # Arrange — listed Z first so input order cannot produce the right answer
    items = _items(6)
    groups = [_group("Zebra", 0, 3), _group("Apple", 3, 3)]

    # Act
    themes = summarise_themes(items, groups, THRESHOLD).themes

    # Assert
    assert [t.label for t in themes] == ["Apple", "Zebra"]
