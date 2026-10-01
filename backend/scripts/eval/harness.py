"""Shared core for the evaluation scripts: a runner, latency maths and a report writer.

It knows nothing about any one feature. A script supplies the items and an async
callable that answers one of them; the harness times each call, records an error by
class name only (an exception message can hold the text being evaluated), skips the
whole run when the model is not available, and writes a Markdown and a JSON report with
a fixed schema. Everything except ``run_suite`` and ``write_report`` is pure.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, TypeVar

REPORT_SCHEMA_VERSION: Final = 1
MAX_LABEL_CHARS: Final = 40
_MAX_NAME_CHARS: Final = 120
_UNNAMED: Final = "unnamed"
_LABEL_UNSAFE: Final = re.compile(r"[^A-Za-z0-9._-]")
_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_YES_NO: Final = {True: "yes", False: "no"}

Scalar = str | int | float | bool | None
Output = Mapping[str, object]


class HasId(Protocol):
    """An evaluation item: anything with a string ``id``."""

    @property
    def id(self) -> str:
        """A short stable identifier, used in reports instead of the item's text."""
        ...


ItemT = TypeVar("ItemT", bound=HasId)
Clock = Callable[[], float]
Availability = Callable[[], Awaitable[str | None]]


# ── latency maths ────────────────────────────────────────────────────────


def percentile(values: Sequence[float], fraction: float) -> float:
    """The *fraction* quantile of *values*, interpolating linearly between ranks.

    Args:
        values: The samples, in any order.
        fraction: A number from 0.0 (minimum) to 1.0 (maximum); 0.5 is the median.

    Raises:
        ValueError: If *values* is empty or *fraction* is outside ``[0, 1]``.
    """
    if not values:
        raise ValueError("cannot take a percentile of an empty list")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(frozen=True)
class LatencyStats:
    """Summary of a set of latencies, in milliseconds."""

    count: int
    p50: float
    p95: float
    mean: float
    max: float


def latency_stats(values_ms: Sequence[float]) -> LatencyStats:
    """Summarise latencies; an empty list gives all zeros so a report always renders."""
    if not values_ms:
        return LatencyStats(count=0, p50=0.0, p95=0.0, mean=0.0, max=0.0)
    return LatencyStats(
        count=len(values_ms),
        p50=percentile(values_ms, 0.5),
        p95=percentile(values_ms, 0.95),
        mean=sum(values_ms) / len(values_ms),
        max=max(values_ms),
    )


# ── runner ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunRecord:
    """What happened for one item. ``error`` is an exception class name, never a message."""

    item_id: str
    elapsed_ms: float
    output: Output | None
    error: str | None


@dataclass(frozen=True)
class SuiteRun:
    """All records of a run, or the reason the run was skipped."""

    skipped: bool
    skip_reason: str | None
    records: tuple[RunRecord, ...]


async def run_suite(
    items: Sequence[ItemT],
    answer: Callable[[ItemT], Awaitable[Output]],
    *,
    availability: Availability | None = None,
    recoverable: tuple[type[Exception], ...] = (),
    clock: Clock = time.perf_counter,
) -> SuiteRun:
    """Run *answer* over *items* one at a time, timing each call.

    Items run sequentially on purpose: concurrent calls would make the latency figures
    measure the queue, not the model.

    Args:
        items: The evaluation items, each with an ``id``.
        answer: Answers one item and returns a mapping of what it produced.
        availability: Called once first; a returned string skips the whole run and is
            kept as the reason (a model that is not installed is not a failure).
        recoverable: Exception types that count as a failed item instead of stopping the
            run. Anything else propagates, so a bug is never scored as a bad answer.
        clock: A monotonic clock in seconds; replaced in tests.
    """
    if availability is not None:
        reason = await availability()
        if reason is not None:
            return SuiteRun(skipped=True, skip_reason=reason, records=())
    records: list[RunRecord] = []
    for item in items:
        started = clock()
        output: Output | None = None
        error: str | None = None
        try:
            output = await answer(item)
        except recoverable as exc:
            error = type(exc).__name__
        elapsed_ms = (clock() - started) * 1000.0
        records.append(RunRecord(item.id, elapsed_ms, output, error))
    return SuiteRun(skipped=False, skip_reason=None, records=tuple(records))


# ── report ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Report:
    """A finished evaluation, in the one shape both writers render.

    ``rows`` hold scalars only, one mapping per item, keyed by ``columns``. Put ids,
    kinds, check results and timings in a row; never a question or an answer.
    """

    suite: str
    label: str
    generated_at: str
    skipped: bool
    skip_reason: str | None
    summary: Mapping[str, Scalar]
    latency: LatencyStats
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Scalar], ...]


@dataclass(frozen=True)
class ReportPaths:
    """Where a report was written."""

    markdown: Path
    json: Path


def timestamp_slug(moment: datetime) -> str:
    """A compact UTC stamp such as ``20260930T120509Z``, safe in a file name."""
    return moment.strftime("%Y%m%dT%H%M%SZ")


def sanitize_label(label: str) -> str:
    """Make a model label (``llama3.2:3b``) safe to use inside a file name."""
    cleaned = _LABEL_UNSAFE.sub("_", label.strip())[:MAX_LABEL_CHARS]
    return cleaned or _UNNAMED


def _checked_row(report: Report, row: Mapping[str, Scalar]) -> dict[str, Scalar]:
    """The row in column order; a key outside the columns is refused, not dropped."""
    extra = sorted(set(row) - set(report.columns))
    if extra:
        raise ValueError(f"row has a cell outside the columns: {', '.join(extra)}")
    return {column: row.get(column) for column in report.columns}


def to_json(report: Report) -> dict[str, object]:
    """The report as a JSON-ready mapping with a fixed, versioned key order."""
    latency = report.latency
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite": report.suite,
        "label": report.label,
        "generated_at": report.generated_at,
        "skipped": report.skipped,
        "skip_reason": report.skip_reason,
        "summary": dict(report.summary),
        "latency_ms": {
            "count": latency.count,
            "p50": latency.p50,
            "p95": latency.p95,
            "mean": latency.mean,
            "max": latency.max,
        },
        "columns": list(report.columns),
        "rows": [_checked_row(report, row) for row in report.rows],
    }


def _cell(value: Scalar) -> str:
    """One Markdown table cell: booleans as yes/no, no pipes, no line breaks."""
    if value is None:
        return ""
    text = _YES_NO[value] if isinstance(value, bool) else str(value)
    return " ".join(text.split()).replace("|", "\\|")


def markdown_table(
    headers: Sequence[str], rows: Sequence[Sequence[Scalar]]
) -> list[str]:
    """A Markdown table as lines; cells are escaped the same way in every report."""
    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)
    return lines


def render_markdown(report: Report) -> str:
    """Render *report* as Markdown: a summary table, latency and one row per item."""
    lines = [
        f"# {report.suite} report: {report.label}",
        "",
        f"Generated {report.generated_at}. Schema version {REPORT_SCHEMA_VERSION}.",
        "",
    ]
    if report.skipped:
        lines += [f"SKIPPED: {report.skip_reason}", ""]
        return "\n".join(lines)
    lines += ["## Summary", ""]
    lines += markdown_table(["metric", "value"], list(report.summary.items()))
    latency = report.latency
    lines += [
        "",
        (
            f"Latency over {latency.count} items: p50 {latency.p50:.1f} ms, "
            f"p95 {latency.p95:.1f} ms, mean {latency.mean:.1f} ms, "
            f"max {latency.max:.1f} ms."
        ),
        "",
        "## Items",
        "",
    ]
    rows = [[_checked_row(report, r)[c] for c in report.columns] for r in report.rows]
    lines += markdown_table(list(report.columns), rows)
    return "\n".join([*lines, ""])


def safe_report_path(directory: Path, stem: str, suffix: str) -> Path:
    """The path ``directory/stem+suffix``, refusing anything that could leave *directory*.

    The name must be a plain file name (letters, digits, dot, dash, underscore), must
    not start with a dot, and, once resolved, must still sit inside *directory*, so an
    existing symlink cannot redirect the write.

    Raises:
        ValueError: If the name is unsafe or resolves outside *directory*.
    """
    if not _SAFE_NAME.fullmatch(stem) or len(stem) > _MAX_NAME_CHARS:
        raise ValueError("report name must be a plain file name")
    candidate = directory / f"{stem}{suffix}"
    if not candidate.resolve().is_relative_to(directory.resolve()):
        raise ValueError("report name resolves outside the reports directory")
    return candidate


def reports_dir(index_root: Path) -> Path:
    """``<index_root>/reports``, refusing one that resolves outside the index.

    Raises:
        ValueError: If ``reports`` is, or links to, somewhere outside *index_root*.
    """
    directory = index_root / "reports"
    if not directory.resolve().is_relative_to(index_root.resolve()):
        raise ValueError("the reports directory resolves outside the index")
    return directory


def write_report(report: Report, directory: Path, stem: str) -> ReportPaths:
    """Write ``<stem>.md`` and ``<stem>.json`` into *directory*, creating it if needed.

    Raises:
        ValueError: If *stem* is not a safe file name.
        OSError: If a file cannot be written.
    """
    markdown = safe_report_path(directory, stem, ".md")
    json_path = safe_report_path(directory, stem, ".json")
    directory.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(to_json(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ReportPaths(markdown=markdown, json=json_path)
