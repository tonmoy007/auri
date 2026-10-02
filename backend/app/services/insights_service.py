"""The small-cohort rule shared by every aggregate HR sees.

A chart is a de-anonymisation vector the moment a bucket is small enough to
point at a person: "2 people in a 3-person team logged something negative
this week" identifies them. Every bucket below ``ANALYTICS_MIN_COHORT`` is
therefore returned as *suppressed* — no count, not a zero — and the UI is
told to say so explicitly rather than draw a gap.

Suppression is applied server-side, before anything is serialised. The API never
sends a number it then asks the client to hide. Insights itself is built from the
count-only rollups in ``insight_weeks``; Themes and the Privacy panel use the same
rule from here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.services.settings_service import get_config

logger = logging.getLogger(__name__)

SENTIMENTS: tuple[str, ...] = ("negative", "neutral", "positive")


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
