"""Suppression inside one week's breakdowns (plan 14.1).

A figure below the cohort is withheld (primary). When a breakdown's total is shown
and exactly one of its parts is withheld, that part would follow by subtraction,
so the smallest shown part is withheld too (secondary). When the total itself is
withheld, so is every non-zero part, or the parts would add back up to it.
"""

from __future__ import annotations

from app.services.insight_suppression import suppress_partition

K = 5


def _shown(buckets) -> dict[str, int | None]:
    return {b.label: b.count for b in buckets}


def test_parts_below_the_cohort_are_withheld_and_zero_is_shown() -> None:
    # Arrange
    parts = {"a": 4, "b": 5, "c": 0, "d": 9}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=True)

    # Assert — a is below the cohort; b (the smallest shown part) goes with it
    assert _shown(buckets) == {"a": None, "b": None, "c": 0, "d": 9}


def test_with_no_total_shown_every_non_zero_part_is_withheld() -> None:
    # Arrange — a withheld total must not be rebuilt by adding the parts up
    parts = {"a": 6, "b": 7, "c": 0}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=False)

    # Assert
    assert _shown(buckets) == {"a": None, "b": None, "c": 0}


def test_a_lone_withheld_part_takes_the_smallest_shown_part_with_it() -> None:
    # Arrange — total 21 shown: without the second rule, a = 21 - 9 - 8 - 0 = 4
    parts = {"a": 4, "b": 9, "c": 8, "d": 0}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=True)

    # Assert
    assert _shown(buckets) == {"a": None, "b": 9, "c": None, "d": 0}


def test_two_withheld_parts_need_no_second_rule() -> None:
    # Arrange
    parts = {"a": 3, "b": 2, "c": 11}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=True)

    # Assert
    assert _shown(buckets) == {"a": None, "b": None, "c": 11}


def test_nothing_withheld_shows_every_part() -> None:
    # Arrange
    parts = {"a": 5, "b": 0, "c": 12}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=True)

    # Assert
    assert _shown(buckets) == {"a": 5, "b": 0, "c": 12}


def test_ties_for_the_smallest_shown_part_are_broken_by_label() -> None:
    # Arrange — deterministic, so the same week always hides the same cells
    parts = {"z": 7, "a": 7, "m": 2}

    # Act
    buckets = suppress_partition(parts, K, parent_shown=True)

    # Assert
    assert _shown(buckets) == {"z": 7, "a": None, "m": None}


def test_parts_keep_their_order() -> None:
    # Arrange
    parts = {"monday": 6, "tuesday": 0, "wednesday": 8}

    # Act
    labels = [b.label for b in suppress_partition(parts, K, parent_shown=True)]

    # Assert
    assert labels == ["monday", "tuesday", "wednesday"]
