"""Building the recurring-themes report from stored summaries.

The flow is: read de-identified summaries for the last ``days`` and the
``days`` before them, ask the *local* model to group them, then hand the
grouping to ``theme_report`` which computes every figure and applies the
cohort rule. If the local model is missing or answers with something unusable
the report falls back to grouping by the category stored at submit time and
says so — it never invents themes and never fails the whole page over a model
hiccup.

Privacy properties held here:

* Only ``ai_summary``, ``sentiment`` and ``category`` are selected (and
  ``created_at`` is filtered on, not read). The transcript is never read.
* The model is pinned to Ollama, so summaries do not leave the machine; the
  ``auto`` chain (which can reach Gemini/OpenAI) is deliberately not used.
* If the current period is too small to show any theme, the model is not
  called at all.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ThemeClusteringError
from app.models.confession import Confession, ConfessionStatus
from app.services import insights_service, theme_clustering
from app.services.llm import LLMService
from app.services.theme_clustering import SummaryItem, ThemeGroup, Window
from app.services.theme_report import ThemeReport, ThemeSummary, summarise_themes

logger = logging.getLogger(__name__)

METHOD_MODEL: Final = "model"
METHOD_CATEGORY: Final = "category"
METHOD_NONE: Final = "none"
MODEL_ATTEMPTS: Final = 2

NOTICE_FALLBACK: Final = (
    "The local model was unavailable or gave an unusable answer, so themes "
    "are grouped by each confession's category instead."
)
NOTICE_GROUPS_TOO_SMALL: Final = (
    "The model's groups were each too small to report without risking "
    "identifying someone, so themes are grouped by each confession's "
    "category instead."
)
NOTICE_TOO_FEW: Final = (
    "There are too few confessions in this period to report themes without "
    "risking identifying someone."
)


async def fetch_summary_items(
    session: AsyncSession, after: datetime, up_to: datetime, window: Window
) -> tuple[list[SummaryItem], bool]:
    """Return the newest summaries created in ``(after, up_to]``, and whether capped.

    Only the summary, sentiment and category are selected. Soft-deleted
    confessions are excluded: a confessor who withdrew theirs has withdrawn it.

    Returns:
        The items, newest first, and ``True`` if more existed than
        ``MAX_SUMMARIES_PER_WINDOW`` (so the caller can say the view is partial).
    """
    cap = theme_clustering.MAX_SUMMARIES_PER_WINDOW
    stmt = (
        select(Confession.ai_summary, Confession.sentiment, Confession.category)
        .where(
            Confession.status != ConfessionStatus.deleted,
            Confession.ai_summary.is_not(None),
            func.trim(Confession.ai_summary) != "",
            Confession.created_at > after,
            Confession.created_at <= up_to,
        )
        .order_by(Confession.created_at.desc())
        .limit(cap + 1)
    )
    rows = (await session.execute(stmt)).all()
    items = [
        SummaryItem(
            text=row.ai_summary,
            sentiment=row.sentiment,
            category=row.category,
            window=window,
        )
        for row in rows[:cap]
    ]
    return items, len(rows) > cap


async def _ask_model(llm: LLMService, content: str) -> str:
    """Send *content* to the model in a worker thread and return its raw reply.

    The blocking HTTP call must not run on the event loop: a slow local model
    would otherwise stall every other request.
    """
    return await asyncio.to_thread(
        llm.complete, theme_clustering.CLUSTERING_INSTRUCTION, content
    )


def _parse_or_none(raw: str, item_count: int, attempt: int) -> list[ThemeGroup] | None:
    """Parse a model reply, or log why it was unusable and return ``None``."""
    try:
        return theme_clustering.parse_theme_groups(raw, item_count)
    except ThemeClusteringError as exc:
        # The message is fixed text and never quotes the model's reply.
        logger.warning("theme grouping attempt %d unusable: %s", attempt, exc)
        return None


async def _group_with_model(
    items: list[SummaryItem], llm: LLMService
) -> list[ThemeGroup]:
    """Ask the model to group *items*; raises if it cannot give a usable answer.

    A small local model sometimes answers with malformed JSON, so an answer
    that arrives but does not parse is retried once. No answer at all (the
    model is not running) is not retried, since a second wait would change
    nothing.

    Raises:
        ThemeClusteringError: If there was no reply, or no attempt parsed.
    """
    content = theme_clustering.numbered_summaries(items)
    for attempt in range(1, MODEL_ATTEMPTS + 1):
        raw = await _ask_model(llm, content)
        if not raw:
            raise ThemeClusteringError("model returned no reply")
        groups = _parse_or_none(raw, len(items), attempt)
        if groups is not None:
            return groups
    raise ThemeClusteringError("model gave no usable answer")


async def _choose_groups(
    items: list[SummaryItem], llm: LLMService
) -> tuple[list[ThemeGroup], str, str | None]:
    """Group *items* with the model, or by category if it cannot be used."""
    try:
        return await _group_with_model(items, llm), METHOD_MODEL, None
    except ThemeClusteringError as exc:
        # The message is fixed text and never quotes the model's reply.
        logger.warning("theme grouping fell back to categories: %s", exc)
        return (
            theme_clustering.group_by_category(items),
            METHOD_CATEGORY,
            NOTICE_FALLBACK,
        )


@dataclass(frozen=True)
class _Period:
    """The reporting window and the cohort rule that applies to it."""

    start: datetime
    end: datetime
    days: int
    threshold: int


def _assemble(
    period: _Period,
    analysed: int,
    truncated: bool,
    method: str,
    notice: str | None,
    summary: ThemeSummary,
) -> ThemeReport:
    """Combine the pieces into one report, suppressing the analysed count too."""
    return ThemeReport(
        range_start=period.start,
        range_end=period.end,
        days=period.days,
        min_cohort=period.threshold,
        method=method,
        notice=notice,
        analysed=insights_service.suppress_small_cohort(
            "analysed", analysed, period.threshold
        ),
        truncated=truncated,
        themes=summary.themes,
        hidden_themes=summary.hidden_themes,
    )


async def _fetch_both_periods(
    session: AsyncSession, period: _Period
) -> tuple[list[SummaryItem], list[SummaryItem], bool]:
    """Fetch the current and the preceding period's summaries.

    Returns:
        Current items, previous items, and whether either period was capped.
    """
    current, current_capped = await fetch_summary_items(
        session, period.start, period.end, Window.current
    )
    previous, previous_capped = await fetch_summary_items(
        session,
        period.start - timedelta(days=period.days),
        period.start,
        Window.previous,
    )
    return current, previous, current_capped or previous_capped


async def _themes_for(
    items: list[SummaryItem], threshold: int, llm: LLMService | None
) -> tuple[ThemeSummary, str, str | None]:
    """Return the reportable themes for *items*, and how they were formed.

    The model is tried first. If it is unusable, or its groups are all too
    small to report, the same items are grouped by their stored category
    instead so HR is never left with an unexplained blank.
    """
    groups, method, notice = await _choose_groups(
        items, llm or LLMService(provider="ollama")
    )
    summary = summarise_themes(items, groups, threshold)
    if method == METHOD_MODEL and not summary.themes:
        by_category = summarise_themes(
            items, theme_clustering.group_by_category(items), threshold
        )
        if by_category.themes:
            return by_category, METHOD_CATEGORY, NOTICE_GROUPS_TOO_SMALL
    return summary, method, notice


async def generate_report(
    session: AsyncSession,
    days: int,
    now: datetime,
    llm: LLMService | None = None,
) -> ThemeReport:
    """Build the themes report for the ``days`` ending at *now*.

    Args:
        session: Active database session.
        days: Length of the reporting period; the same length before it is the
            comparison period.
        now: End of the period, injected (AGENTS.md §16.5).
        llm: Model client; defaults to a local-Ollama-only one.

    Returns:
        A report that is already suppressed and ranked.
    """
    threshold = insights_service.min_cohort()
    start = now - timedelta(days=days)
    period = _Period(start=start, end=now, days=days, threshold=threshold)
    current, previous, truncated = await _fetch_both_periods(session, period)
    # Everything read is now plain data. End the (read-only) transaction so the
    # connection is not held idle for the minutes a local model may take. A
    # commit rather than a rollback: sessions here use expire_on_commit=False,
    # so the signed-in User the route still needs is not expired.
    await session.commit()
    if len(current) < threshold:
        return _assemble(
            period,
            len(current),
            truncated,
            METHOD_NONE,
            NOTICE_TOO_FEW,
            ThemeSummary([], 0),
        )

    items = [*current, *previous]
    summary, method, notice = await _themes_for(items, threshold, llm)
    return _assemble(period, len(current), truncated, method, notice, summary)
