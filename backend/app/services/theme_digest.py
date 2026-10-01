"""Rendering a theme report as a Markdown or CSV leadership digest.

Both formats are built from the *already suppressed* report, so a withheld
figure has nowhere to come from: it is simply absent, with a note saying why.

Theme labels were written by a model reading user speech, so they are treated
as hostile text here. In CSV a leading ``=``, ``+``, ``-`` or ``@`` would be a
formula in a spreadsheet; in Markdown a label could carry a link or table
break. Each renderer neutralises its own format's metacharacters.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Final

from app.services.insights_service import Bucket
from app.services.theme_report import SentimentStatus, Theme, ThemeReport

CSV_COLUMNS: Final = (
    "rank",
    "theme",
    "confessions",
    "previous_period_confessions",
    "negative_share",
    "previous_negative_share",
    "sentiment_change",
    "note",
)
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@")
_LEADING_BLANKS: Final = " \t\r\n"
# Only what can form a link, image, emphasis, HTML or a table cell. Plain
# punctuation is left alone so "Work-life balance" stays readable in email.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]()<>|~])")

_METHOD_NAMES: Final = {
    "model": "grouped automatically by the local model, so groupings may be imperfect",
    "category": "grouped by category (the model was unavailable)",
    "none": "not enough confessions to group",
}
_PARTIAL_VIEW: Final = (
    "Partial view: this period held more confessions than the report reads, "
    "so the most recent ones are used."
)


def csv_safe(text: str) -> str:
    """Return *text* with a leading formula character defused for spreadsheets.

    Compatibility-normalises first, so a fullwidth ``＝`` is caught as ``=``.

    Args:
        text: A cell value written by a model or by us.

    Returns:
        *text*, prefixed with an apostrophe if a spreadsheet could read it as
        a formula.
    """
    normalised = unicodedata.normalize("NFKC", text).lstrip(_LEADING_BLANKS)
    if normalised.startswith(_FORMULA_PREFIXES) or text[:1] in ("\t", "\r"):
        return f"'{text}"
    return text


def markdown_safe(text: str) -> str:
    """Escape Markdown link, emphasis, HTML and table characters in *text*.

    Args:
        text: Untrusted text destined for a Markdown table cell.

    Returns:
        *text* on one line with those characters backslash-escaped.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _previous_cell(bucket: Bucket) -> str:
    """Render the previous-period count, or why it is not shown."""
    if bucket.suppressed:
        return "withheld"
    return str(bucket.count)


def _percent(share: float | None) -> str:
    """Render a 0-1 share as a whole percentage, or an en dash if unknown."""
    return "–" if share is None else f"{round(share * 100)}%"


def _change_cell(theme: Theme) -> str:
    """Render the sentiment change for the Markdown table."""
    if theme.sentiment_status is SentimentStatus.no_previous_period:
        return "new"
    if theme.sentiment_change is None:
        return "withheld"
    points = round(theme.sentiment_change * 100)
    return f"{points:+d} pts"


def _markdown_row(theme: Theme) -> str:
    """Render one theme as a Markdown table row."""
    return (
        f"| {theme.rank} | {markdown_safe(theme.label)} | {theme.confessions} "
        f"| {_previous_cell(theme.previous_period)} "
        f"| {_percent(theme.negative_share)} | {_change_cell(theme)} |"
    )


def _markdown_header(report: ThemeReport) -> list[str]:
    """Render the title, period, method and any notices."""
    lines = [
        "# Auri leadership digest",
        "",
        (
            f"Period: {report.range_start:%Y-%m-%d} to {report.range_end:%Y-%m-%d} "
            f"({report.days} days), compared with the {report.days} days before."
        ),
        f"Themes: {_METHOD_NAMES.get(report.method, report.method)}.",
        "",
    ]
    # Notices are fixed server text, not model output, so they are not escaped.
    for notice in (report.notice, _PARTIAL_VIEW if report.truncated else None):
        if notice:
            lines += [f"> {notice}", ""]
    return lines


def render_markdown(report: ThemeReport) -> str:
    """Render *report* as a Markdown digest suitable for a leadership update.

    Args:
        report: An already-suppressed theme report.

    Returns:
        The Markdown document, ending in a newline.
    """
    lines = _markdown_header(report)
    if report.themes:
        lines += [
            "| # | Theme | Confessions | Previous period | Negative | Change |",
            "|---|-------|-------------|-----------------|----------|--------|",
            *(_markdown_row(theme) for theme in report.themes),
            "",
        ]
    if report.hidden_themes:
        lines += [
            (
                f"{report.hidden_themes} further theme(s) are withheld because "
                f"fewer than {report.min_cohort} confessions shared them, to "
                "protect anonymity."
            ),
            "",
        ]
    lines.append(
        "Built from de-identified summaries only. Figures under "
        f"{report.min_cohort} are never shown."
    )
    return "\n".join(lines) + "\n"


def _csv_note(theme: Theme, report: ThemeReport) -> str:
    """Explain any withheld cell, and a partial view, in a theme's CSV row."""
    notes = []
    if theme.previous_period.suppressed:
        notes.append(f"previous period withheld (fewer than {report.min_cohort})")
    if theme.sentiment_status is SentimentStatus.suppressed:
        notes.append(f"sentiment withheld (fewer than {report.min_cohort} labelled)")
    if report.truncated:
        notes.append("partial view: most recent confessions only")
    return "; ".join(notes)


def _csv_row(theme: Theme, report: ThemeReport) -> list[str | int | float]:
    """Build one CSV row; only text columns are formula-defused."""
    previous = theme.previous_period
    return [
        theme.rank,
        csv_safe(theme.label),
        theme.confessions,
        "" if previous.count is None else previous.count,
        "" if theme.negative_share is None else theme.negative_share,
        "" if theme.previous_negative_share is None else theme.previous_negative_share,
        "" if theme.sentiment_change is None else theme.sentiment_change,
        csv_safe(_csv_note(theme, report)),
    ]


def render_csv(report: ThemeReport) -> str:
    """Render *report*'s visible themes as CSV, one row per theme.

    Args:
        report: An already-suppressed theme report.

    Returns:
        The CSV text with a header row and ``\\n`` line endings.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for theme in report.themes:
        writer.writerow(_csv_row(theme, report))
    return buffer.getvalue()
