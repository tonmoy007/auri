"""Evaluate the counselor response with the deterministic checks of plan task 12.2.

Usage::

    python backend/scripts/eval_counsel.py [--eval-set FILE] [--model LABEL]
        [--output-dir DIR] [--review-sheet]

Each confession in the eval set goes through the same path the confession flow takes:
moderation first, then either the fixed crisis template or ``counsel()`` over the
de-identified text. The reply is scored by pure checks (right route, byte-equal crisis
text, length, no private detail or PII repeated back, no clinical advice, no doctrine,
no prompt leak) and the run is timed. Nothing here is graded by a model: the four
subjective columns (acknowledges without parroting, validates, one concrete reflection,
tone) are left empty for human reviewers.

The reply dict has two keys, ``route`` (``"counsel"`` or ``"crisis"``) and ``reply``.
Reports carry ids, check results and timings, never a confession or a reply. The review
sheet (opt-in) is the one file that shows replies, and it is written beside the reports.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import httpx

# `app` lives one level up, and `eval` is the sibling package next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings
from app.exceptions import AuriError
from app.models.confession import ModerationSeverity
from app.services import crisis_response
from app.services.llm import LLMService
from eval.counsel_checks import (
    HUMAN_COLUMNS,
    ROUTE_COUNSEL,
    ROUTE_CRISIS,
    SUITE,
    EvalItem,
    EvalSetError,
    ItemScore,
    Reply,
    ScoreContext,
    build_report,
    default_context,
    load_eval_set,
    score_item,
)
from eval.harness import (
    Report,
    ReportPaths,
    SuiteRun,
    run_suite,
    safe_report_path,
    sanitize_label,
    timestamp_slug,
    write_report,
)

AnswerFn = Callable[[EvalItem], Awaitable[Reply]]
Availability = Callable[[], Awaitable[str | None]]

OLLAMA_PROBE_TIMEOUT_S: Final = 5.0
EXIT_ERROR: Final = 2
DEFAULT_EVAL_SET: Final = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "counsel_eval_set.json"
)
DEFAULT_RECOVERABLE: Final[tuple[type[Exception], ...]] = (
    AuriError,
    httpx.HTTPError,
    TimeoutError,
    OSError,
)


# ── running ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvalResult:
    """A finished run, or the reason it was skipped."""

    skipped: bool
    skip_reason: str | None
    scores: tuple[ItemScore, ...]
    replies: tuple[Reply | None, ...]
    report: Report | None


async def run_eval(
    items: Sequence[EvalItem],
    answer: AnswerFn,
    *,
    label: str,
    ctx: ScoreContext,
    availability: Availability | None = None,
    recoverable: tuple[type[Exception], ...] = DEFAULT_RECOVERABLE,
    clock: Callable[[], float] = time.perf_counter,
    now: datetime | None = None,
) -> EvalResult:
    """Run *answer* over *items*, score every reply and build the report.

    A model that is not available skips the run (``skipped`` is set and there is no
    report). A call that raises one of *recoverable* is an errored item, not a pass.
    """
    run: SuiteRun = await run_suite(
        items, answer, availability=availability, recoverable=recoverable, clock=clock
    )
    if run.skipped:
        return EvalResult(True, run.skip_reason, (), (), None)
    by_id = {item.id: item for item in items}
    scores = tuple(
        score_item(
            by_id[r.item_id], r.output, ctx, elapsed_ms=r.elapsed_ms, error=r.error
        )
        for r in run.records
    )
    moment = now or datetime.now(timezone.utc)
    report = build_report(
        scores, label=label, generated_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return EvalResult(False, None, scores, tuple(r.output for r in run.records), report)


def render_review_sheet(pairs: Sequence[tuple[EvalItem, Reply | None]]) -> str:
    """A Markdown sheet with each reply and the reviewer notes; the only file with text."""
    lines = ["# Counselor reply review sheet", ""]
    for item, reply in pairs:
        text = reply.get("reply") if reply else None
        lines += [
            f"## {item.id} ({item.category})",
            "",
            f"Notes: {item.notes}",
            "",
            f"Reply: {text or '(no usable reply)'}",
            "",
            *(f"- {column}: " for column in HUMAN_COLUMNS),
            "",
        ]
    return "\n".join(lines)


# ── the live adapter ─────────────────────────────────────────────────────


def live_answer_fn(*, service: LLMService | None = None) -> AnswerFn:
    """An answer function that follows the confession flow over a real model.

    Moderation reads the original text; a crisis result is the fixed template, never
    generated; anything else is ``counsel()`` over the de-identified text. The calls
    block, so they run in a worker thread. The argument swaps the service in tests.
    """
    llm = service or LLMService(provider="ollama")
    closing = crisis_response.CONFESSION_CLOSING_LINE

    def _reply(item: EvalItem) -> Reply:
        if llm.moderate(item.text) is ModerationSeverity.crisis:
            return {
                "route": ROUTE_CRISIS,
                "reply": crisis_response.render(closing).text,
            }
        reply = llm.counsel(llm.deidentify(item.text))
        return {"route": ROUTE_COUNSEL, "reply": reply.render()}

    async def answer(item: EvalItem) -> Reply:
        return await asyncio.to_thread(_reply, item)

    return answer


async def live_availability(model: str) -> str | None:
    """Why the live run cannot happen for *model*, or ``None`` if it can."""
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_PROBE_TIMEOUT_S) as client:
            response = await client.get(f"{base}/api/tags")
            response.raise_for_status()
            installed = {m.get("name") for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return "the local Ollama server is not reachable"
    if model not in installed:
        return f"model {model!r} is not installed on the local Ollama server"
    return None


# ── command line ─────────────────────────────────────────────────────────


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate counselor responses.")
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_SET)
    parser.add_argument(
        "--model",
        default=settings.OLLAMA_MODEL,
        help="the local Ollama model to run, also the label of this run and its files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports"),
        help="where the reports go (default: ./reports)",
    )
    parser.add_argument(
        "--review-sheet",
        action="store_true",
        help="also write a sheet with the replies, for the human scores",
    )
    return parser.parse_args(argv)


def _write_outputs(
    result: EvalResult,
    items: Sequence[EvalItem],
    args: argparse.Namespace,
    now: datetime,
) -> tuple[ReportPaths, Path | None]:
    if result.report is None:
        raise ValueError("there is no report to write")
    stem = f"{SUITE}_{sanitize_label(args.model)}_{timestamp_slug(now)}"
    paths = write_report(result.report, args.output_dir, stem)
    if not args.review_sheet:
        return paths, None
    by_id = {item.id: item for item in items}
    pairs = [
        (by_id[s.item_id], r)
        for s, r in zip(result.scores, result.replies, strict=True)
    ]
    sheet = safe_report_path(args.output_dir, f"{stem}_review", ".md")
    sheet.write_text(render_review_sheet(pairs), encoding="utf-8")
    return paths, sheet


async def _run(
    args: argparse.Namespace,
    answer_fn: AnswerFn | None,
    availability: Availability | None,
    context: ScoreContext | None,
) -> int:
    items = load_eval_set(args.eval_set)
    if answer_fn is None:
        settings.OLLAMA_MODEL = args.model
        answer_fn = live_answer_fn()
        availability = availability or (lambda: live_availability(args.model))
    now = datetime.now(timezone.utc)
    result = await run_eval(
        items,
        answer_fn,
        label=args.model,
        ctx=context or default_context(),
        availability=availability,
        now=now,
    )
    if result.skipped or result.report is None:
        sys.stdout.write(f"SKIP: {result.skip_reason}\n")
        return 0
    paths, sheet = _write_outputs(result, items, args, now)
    summary = result.report.summary
    sys.stdout.write(
        f"{args.model}: {summary['items_all_checks_passed']} of {summary['items']} "
        f"items passed every check, {summary['errors']} errors\n"
        f"wrote {paths.markdown}, {paths.json}" + (f", {sheet}" if sheet else "") + "\n"
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    answer_fn: AnswerFn | None = None,
    availability: Availability | None = None,
    context: ScoreContext | None = None,
) -> int:
    """Run the evaluation; return 0 on success or skip, 2 on a real problem.

    Failed checks do not change the exit code: this reports quality, it does not gate
    a shell. The injected arguments exist for tests.
    """
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args, answer_fn, availability, context))
    except EvalSetError as exc:
        sys.stderr.write(f"eval set error: {exc}\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
