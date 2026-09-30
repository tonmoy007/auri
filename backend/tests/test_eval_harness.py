"""Tests for the shared evaluation harness: runner, latency maths and report writer.

The harness knows nothing about priest mode. Every test here uses canned callables and
a fake clock, so nothing touches a model, the network or the real vault.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from eval.harness import (
    REPORT_SCHEMA_VERSION,
    LatencyStats,
    Report,
    RunRecord,
    Scalar,
    latency_stats,
    markdown_table,
    percentile,
    render_markdown,
    reports_dir,
    run_suite,
    safe_report_path,
    sanitize_label,
    timestamp_slug,
    to_json,
    write_report,
)


@dataclass(frozen=True)
class Item:
    """A minimal evaluation item."""

    id: str
    question: str


class FakeClock:
    """A clock that advances by a fixed step on every reading."""

    def __init__(self, step_s: float) -> None:
        self._now = 0.0
        self._step = step_s

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


def _report(**overrides: object) -> Report:
    base: dict[str, object] = {
        "suite": "demo",
        "label": "model-a",
        "generated_at": "2026-09-30T12:00:00Z",
        "skipped": False,
        "skip_reason": None,
        "summary": {"items": 2, "pass_rate": 0.5},
        "latency": LatencyStats(count=2, p50=10.0, p95=19.0, mean=12.0, max=20.0),
        "columns": ("id", "ok"),
        "rows": ({"id": "a", "ok": True}, {"id": "b", "ok": False}),
    }
    base.update(overrides)
    return Report(**base)  # type: ignore[arg-type]


# ── percentile and latency stats ─────────────────────────────────────────


def test_percentile_interpolates_between_ranks() -> None:
    # Arrange
    values = [4.0, 1.0, 3.0, 2.0]

    # Act
    p50 = percentile(values, 0.5)
    p95 = percentile(values, 0.95)

    # Assert
    assert p50 == pytest.approx(2.5)
    assert p95 == pytest.approx(3.85)  # 3.0 + 0.85 * (4.0 - 3.0)


def test_percentile_of_one_value_is_that_value() -> None:
    # Arrange
    values = [7.5]

    # Act
    result = percentile(values, 0.95)

    # Assert
    assert result == 7.5


def test_percentile_ends_are_min_and_max() -> None:
    # Arrange
    values = [5.0, 9.0, 1.0]

    # Act
    low, high = percentile(values, 0.0), percentile(values, 1.0)

    # Assert
    assert (low, high) == (1.0, 9.0)


@pytest.mark.parametrize("fraction", [-0.1, 1.1])
def test_percentile_rejects_a_fraction_outside_zero_to_one(fraction: float) -> None:
    # Arrange
    values = [1.0, 2.0]

    # Act / Assert
    with pytest.raises(ValueError, match="fraction"):
        percentile(values, fraction)


def test_percentile_of_nothing_raises() -> None:
    # Arrange
    values: list[float] = []

    # Act / Assert
    with pytest.raises(ValueError, match="empty"):
        percentile(values, 0.5)


def test_latency_stats_summarises_values() -> None:
    # Arrange
    values = [10.0, 20.0, 30.0, 40.0]

    # Act
    stats = latency_stats(values)

    # Assert
    assert stats == LatencyStats(count=4, p50=25.0, p95=38.5, mean=25.0, max=40.0)


def test_latency_stats_of_nothing_is_all_zero() -> None:
    # Arrange
    values: list[float] = []

    # Act
    stats = latency_stats(values)

    # Assert
    assert stats == LatencyStats(count=0, p50=0.0, p95=0.0, mean=0.0, max=0.0)


# ── runner ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_suite_times_each_item_and_keeps_output() -> None:
    # Arrange
    items = [Item("a", "qa"), Item("b", "qb")]
    calls: list[str] = []

    async def answer(item: Item) -> dict[str, object]:
        calls.append(item.id)
        return {"echo": item.question}

    # Act
    run = await run_suite(items, answer, clock=FakeClock(0.25))

    # Assert
    assert calls == ["a", "b"]
    assert not run.skipped
    assert [r.item_id for r in run.records] == ["a", "b"]
    assert [r.elapsed_ms for r in run.records] == [250.0, 250.0]
    assert run.records[0].output == {"echo": "qa"}
    assert run.records[0].error is None


@pytest.mark.asyncio
async def test_run_suite_records_a_recoverable_error_by_class_name_only() -> None:
    # Arrange
    async def answer(item: Item) -> dict[str, object]:
        raise TimeoutError(f"secret question text: {item.question}")

    # Act
    run = await run_suite(
        [Item("a", "my private words")],
        answer,
        recoverable=(TimeoutError,),
        clock=FakeClock(0.1),
    )

    # Assert
    record = run.records[0]
    assert record.error == "TimeoutError"
    assert record.output is None
    assert "private" not in repr(record)


@pytest.mark.asyncio
async def test_run_suite_lets_an_unexpected_error_propagate() -> None:
    # Arrange
    async def answer(item: Item) -> dict[str, object]:
        raise KeyError(item.id)

    # Act / Assert
    with pytest.raises(KeyError):
        await run_suite([Item("a", "q")], answer, recoverable=(TimeoutError,))


@pytest.mark.asyncio
async def test_run_suite_skips_when_the_model_is_unavailable() -> None:
    # Arrange
    calls: list[str] = []

    async def answer(item: Item) -> dict[str, object]:
        calls.append(item.id)
        return {}

    async def availability() -> str | None:
        return "model 'x' is not installed"

    # Act
    run = await run_suite([Item("a", "q")], answer, availability=availability)

    # Assert
    assert run.skipped
    assert run.skip_reason == "model 'x' is not installed"
    assert run.records == ()
    assert calls == []


@pytest.mark.asyncio
async def test_run_suite_runs_when_availability_returns_none() -> None:
    # Arrange
    async def answer(item: Item) -> dict[str, object]:
        return {"ok": True}

    async def availability() -> str | None:
        return None

    # Act
    run = await run_suite([Item("a", "q")], answer, availability=availability)

    # Assert
    assert not run.skipped
    assert run.skip_reason is None
    assert len(run.records) == 1


# ── report schema ────────────────────────────────────────────────────────


def test_to_json_has_the_fixed_schema() -> None:
    # Arrange
    report = _report()

    # Act
    body = to_json(report)

    # Assert
    assert list(body) == [
        "schema_version",
        "suite",
        "label",
        "generated_at",
        "skipped",
        "skip_reason",
        "summary",
        "latency_ms",
        "columns",
        "rows",
    ]
    assert body["schema_version"] == REPORT_SCHEMA_VERSION
    assert body["latency_ms"] == {
        "count": 2,
        "p50": 10.0,
        "p95": 19.0,
        "mean": 12.0,
        "max": 20.0,
    }
    assert body["columns"] == ["id", "ok"]
    assert body["rows"] == [{"id": "a", "ok": True}, {"id": "b", "ok": False}]
    json.dumps(body)  # must be serialisable as it is


def test_to_json_fills_a_missing_cell_with_none() -> None:
    # Arrange
    report = _report(rows=({"id": "a"},))

    # Act
    body = to_json(report)

    # Assert
    assert body["rows"] == [{"id": "a", "ok": None}]


def test_to_json_rejects_a_cell_outside_the_columns() -> None:
    # Arrange
    report = _report(rows=({"id": "a", "ok": True, "question": "leaky extra"},))

    # Act / Assert
    with pytest.raises(ValueError, match="question"):
        to_json(report)


def test_markdown_lists_summary_latency_and_item_rows() -> None:
    # Arrange
    report = _report()

    # Act
    text = render_markdown(report)

    # Assert
    assert text.startswith("# demo report: model-a")
    assert "| pass_rate | 0.5 |" in text
    assert "p50 10.0 ms" in text
    assert "p95 19.0 ms" in text
    assert "| id | ok |" in text
    assert "| a | yes |" in text
    assert "| b | no |" in text


def test_markdown_escapes_pipes_and_line_breaks_in_cells() -> None:
    # Arrange
    report = _report(rows=({"id": "a|b", "ok": "line1\nline2"},))

    # Act
    text = render_markdown(report)

    # Assert
    assert "a\\|b" in text
    assert "line1 line2" in text


def test_markdown_of_a_skipped_run_states_the_reason() -> None:
    # Arrange
    report = _report(skipped=True, skip_reason="ollama is not reachable", rows=())

    # Act
    text = render_markdown(report)

    # Assert
    assert "SKIPPED: ollama is not reachable" in text
    assert "| id | ok |" not in text


# ── writing reports safely ───────────────────────────────────────────────


def test_write_report_writes_markdown_and_json_side_by_side(tmp_path: Path) -> None:
    # Arrange
    directory = tmp_path / "reports"

    # Act
    paths = write_report(_report(), directory, "demo_model-a_20260930T120000Z")

    # Assert
    assert paths.markdown == directory / "demo_model-a_20260930T120000Z.md"
    assert paths.json == directory / "demo_model-a_20260930T120000Z.json"
    assert json.loads(paths.json.read_text("utf-8"))["suite"] == "demo"
    assert paths.markdown.read_text("utf-8").startswith("# demo report")


@pytest.mark.parametrize(
    "stem", ["../escape", "a/b", "", ".hidden", "a\\b", "x" * 200, "ok\x00bad"]
)
def test_safe_report_path_refuses_names_that_could_leave_the_directory(
    tmp_path: Path, stem: str
) -> None:
    # Arrange
    directory = tmp_path / "reports"

    # Act / Assert
    with pytest.raises(ValueError, match="report name"):
        safe_report_path(directory, stem, ".json")


def test_safe_report_path_refuses_a_symlink_that_points_out(tmp_path: Path) -> None:
    # Arrange
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "trap.json").symlink_to(outside / "target.json")

    # Act / Assert
    with pytest.raises(ValueError, match="report name"):
        safe_report_path(directory, "trap", ".json")


def test_sanitize_label_keeps_a_model_tag_but_not_path_characters() -> None:
    # Arrange
    labels = ["qwen3.5-9b", "llama3.2:3b", "../../etc/passwd", "", "a b/c"]

    # Act
    cleaned = [sanitize_label(label) for label in labels]

    # Assert
    assert cleaned == [
        "qwen3.5-9b",
        "llama3.2_3b",
        ".._.._etc_passwd",
        "unnamed",
        "a_b_c",
    ]


def test_sanitize_label_is_bounded() -> None:
    # Arrange
    label = "m" * 500

    # Act
    cleaned = sanitize_label(label)

    # Assert
    assert len(cleaned) == 40


def test_timestamp_slug_is_compact_utc() -> None:
    # Arrange
    moment = datetime(2026, 9, 30, 12, 5, 9, tzinfo=timezone.utc)

    # Act
    slug = timestamp_slug(moment)

    # Assert
    assert slug == "20260930T120509Z"


def test_run_record_repr_has_no_free_text_field() -> None:
    # Arrange
    record = RunRecord(
        item_id="a", elapsed_ms=1.0, output={"kind": "answer"}, error=None
    )

    # Act
    fields = set(vars(record))

    # Assert
    assert fields == {"item_id", "elapsed_ms", "output", "error"}


def test_reports_dir_sits_under_the_index_root(tmp_path: Path) -> None:
    # Arrange / Act
    directory = reports_dir(tmp_path)

    # Assert
    assert directory == tmp_path / "reports"


def test_reports_dir_refuses_a_link_that_leaves_the_index(tmp_path: Path) -> None:
    # Arrange
    root = tmp_path / "index"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "reports").symlink_to(outside, target_is_directory=True)

    # Act / Assert
    with pytest.raises(ValueError, match="outside the index"):
        reports_dir(root)


def test_markdown_table_escapes_cells() -> None:
    # Arrange
    rows: list[list[Scalar]] = [["a|b", True, None, 1.5]]

    # Act
    lines = markdown_table(["h1", "h2", "h3", "h4"], rows)

    # Assert
    assert lines == [
        "| h1 | h2 | h3 | h4 |",
        "| --- | --- | --- | --- |",
        "| a\\|b | yes |  | 1.5 |",
    ]
