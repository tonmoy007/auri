"""Tests for the Markdown and CSV digest renderers.

Theme labels were written by a model reading user speech, so the renderers are
tested against labels that try to become a spreadsheet formula, a Markdown
link, or a table break. A withheld figure must have nowhere to come from.
"""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import datetime, timezone

from app.services.insights_service import Bucket
from app.services.theme_digest import (
    CSV_COLUMNS,
    csv_safe,
    markdown_safe,
    render_csv,
    render_markdown,
)
from app.services.theme_report import SentimentStatus, Theme, ThemeReport

END = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _theme(label: str = "Pay", rank: int = 1) -> Theme:
    return Theme(
        rank=rank,
        label=label,
        confessions=7,
        previous_period=Bucket(label="previous", count=4, suppressed=False),
        negative_share=0.71,
        previous_negative_share=0.5,
        sentiment_change=0.21,
        sentiment_status=SentimentStatus.ok,
    )


def _report(
    themes: list[Theme],
    hidden: int = 0,
    notice: str | None = None,
    truncated: bool = False,
) -> ThemeReport:
    return ThemeReport(
        range_start=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),
        range_end=END,
        days=7,
        min_cohort=5,
        method="model",
        notice=notice,
        analysed=Bucket(label="analysed", count=30, suppressed=False),
        truncated=truncated,
        themes=themes,
        hidden_themes=hidden,
    )


def test_markdown_lists_each_theme_with_its_figures() -> None:
    # Arrange
    report = _report([_theme("Pay")])

    # Act
    text = render_markdown(report)

    # Assert
    assert "| 1 | Pay | 7 | 4 | 71% | +21 pts |" in text
    assert "2026-09-23 to 2026-09-30 (7 days)" in text


def test_markdown_states_how_many_themes_were_withheld_and_why() -> None:
    # Arrange
    report = _report([_theme()], hidden=2)

    # Act
    text = render_markdown(report)

    # Assert
    assert "2 further theme(s) are withheld because fewer than 5 confessions" in text


def test_markdown_shows_withheld_cells_in_words_not_numbers() -> None:
    # Arrange
    theme = replace(
        _theme(),
        previous_period=Bucket(label="previous", count=None, suppressed=True),
        sentiment_change=None,
        sentiment_status=SentimentStatus.suppressed,
    )

    # Act
    text = render_markdown(_report([theme]))

    # Assert
    assert "| 1 | Pay | 7 | withheld | 71% | withheld |" in text


def test_markdown_neutralises_links_html_and_table_breaks_in_a_label() -> None:
    # Arrange
    label = "[click](http://evil.example) <b>|</b>\n| forged | row |"

    # Act
    text = render_markdown(_report([_theme(label)]))

    # Assert
    assert "[click]" not in text
    assert "<b>" not in text
    assert "\n| forged" not in text
    assert text.split("\n| 1 |")[1].split("\n")[0].count("\\|") == 4


def test_markdown_escape_flattens_newlines() -> None:
    # Act / Assert
    assert "\n" not in markdown_safe("a\nb\r\nc")


def test_csv_has_the_documented_header_and_one_row_per_theme() -> None:
    # Arrange
    report = _report([_theme("Pay"), _theme("Workload", rank=2)])

    # Act
    rows = list(csv.reader(io.StringIO(render_csv(report))))

    # Assert
    assert tuple(rows[0]) == CSV_COLUMNS
    assert [row[1] for row in rows[1:]] == ["Pay", "Workload"]
    assert rows[1][2:7] == ["7", "4", "0.71", "0.5", "0.21"]


def test_csv_blanks_withheld_cells_and_says_why() -> None:
    # Arrange
    theme = replace(
        _theme(),
        previous_period=Bucket(label="previous", count=None, suppressed=True),
        previous_negative_share=None,
        sentiment_change=None,
        sentiment_status=SentimentStatus.suppressed,
    )

    # Act
    row = list(csv.reader(io.StringIO(render_csv(_report([theme])))))[1]

    # Assert
    assert row[3] == "" and row[6] == ""
    assert "previous period withheld (fewer than 5)" in row[7]
    assert "sentiment withheld" in row[7]


def test_csv_defuses_a_label_that_would_be_a_spreadsheet_formula() -> None:
    # Arrange
    label = '=HYPERLINK("http://evil.example","x")'

    # Act
    row = list(csv.reader(io.StringIO(render_csv(_report([_theme(label)])))))[1]

    # Assert
    assert row[1] == "'" + label


def test_csv_safe_covers_every_formula_prefix_and_leading_whitespace() -> None:
    # Act / Assert
    for prefix in ("=", "+", "-", "@", "\t", " =", "\r"):
        assert csv_safe(f"{prefix}1+1").startswith("'"), prefix
    assert csv_safe("Pay and benefits") == "Pay and benefits"
    assert csv_safe("") == ""


def test_markdown_leaves_ordinary_punctuation_readable() -> None:
    # Arrange
    label = "Work-life balance, pay and workload."

    # Act
    text = render_markdown(_report([_theme(label)]))

    # Assert
    assert f"| 1 | {label} | 7 |" in text


def test_markdown_and_csv_both_flag_a_partial_view() -> None:
    # Arrange
    report = _report([_theme()], truncated=True)

    # Act
    markdown = render_markdown(report)
    row = list(csv.reader(io.StringIO(render_csv(report))))[1]

    # Assert
    assert "> Partial view" in markdown
    assert "partial view" in row[7]


def test_neither_format_flags_a_partial_view_when_the_report_is_complete() -> None:
    # Arrange
    report = _report([_theme()], truncated=False)

    # Act
    markdown = render_markdown(report)
    row = list(csv.reader(io.StringIO(render_csv(report))))[1]

    # Assert
    assert "Partial view" not in markdown
    assert row[7] == ""


def test_csv_safe_defuses_a_fullwidth_equals_sign() -> None:
    # Arrange — NFKC folds U+FF1D to "="
    text = "＝HYPERLINK(1)"

    # Act
    safe = csv_safe(text)

    # Assert
    assert safe == "'" + text
