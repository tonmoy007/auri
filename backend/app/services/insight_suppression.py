"""Suppression for one breakdown of a week's Insights (plan 14.1).

Three rules, applied to a breakdown whose parts add up to a parent figure:

1. A part between 1 and the cohort minus one is withheld. Zero is shown: "nobody"
   describes no one.
2. If the parent is shown and exactly one part is withheld, that part would follow
   by subtraction, so the smallest shown non-zero part is withheld too (ties broken
   by label, so the same counts always hide the same cells).
3. If the parent is withheld, every non-zero part is withheld, or the parts would
   add back up to it.

Insights only reports frozen weeks, so the counts, and therefore which cells are
hidden, never change after a week is first shown.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.services.insights_service import Bucket


def suppress_partition(
    parts: Mapping[str, int], threshold: int, *, parent_shown: bool
) -> list[Bucket]:
    """Return *parts* as buckets with the three rules applied, in the given order.

    Args:
        parts: Label to count; the counts add up to the parent figure.
        threshold: The smallest count that may be shown.
        parent_shown: Whether the figure these parts add up to is shown.

    Returns:
        One bucket per part.
    """
    if not parent_shown:
        hidden = {label for label, count in parts.items() if count > 0}
        return _buckets(parts, hidden)
    hidden = {label for label, count in parts.items() if 0 < count < threshold}
    if len(hidden) == 1:
        shown = [(count, label) for label, count in parts.items() if count >= threshold]
        if shown:
            hidden.add(min(shown)[1])
    return _buckets(parts, hidden)


def _buckets(parts: Mapping[str, int], hidden: set[str]) -> list[Bucket]:
    """*parts* as buckets, withholding the labels in *hidden*."""
    return [
        Bucket(label=label, count=None, suppressed=True)
        if label in hidden
        else Bucket(label=label, count=count, suppressed=False)
        for label, count in parts.items()
    ]
