"""The state of one Guide question and the pure steps around it (plan 14.2).

Split out of ``priest_service`` so the service file holds the orchestration only:
what a question has learned so far (``QuestionRun``), the protocols the service
depends on, the cleaning, routing and scoping steps that need no I/O, and the one
metadata log line each question ends with.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.llm.chat_client import ChatMessage, ChatResult
from app.llm.fencing import strip_fence_runs
from app.models.confession import ModerationSeverity
from app.priest import metrics, priest_config, safety_router
from app.priest.safety_router import SafetyDecision
from app.priest.schemas import (
    MAX_QUESTION_CHARS,
    TRADITION_LABELS,
    PriestAnswerResponse,
    TraditionId,
)
from app.priest.types import RetrievalResult
from app.services.deidentify import strip_pii_regex

ModeratorFn = Callable[[str], ModerationSeverity]


class QuestionRetriever(Protocol):
    """The part of ``Retriever`` the service needs."""

    async def retrieve(
        self, question: str, traditions: frozenset[str] | None
    ) -> RetrievalResult:
        """Find passages for a cleaned question."""
        ...


class ChatChain(Protocol):
    """The part of ``PriestLLMChain`` the service needs."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        request_id: str,
        max_tokens: int = 600,
        temperature: float = 0.2,
        seed: int | None = None,
        json_schema: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """Generate a reply."""
        ...


@dataclass
class QuestionRun:
    """What one question has learned so far; dropped when it ends, never stored."""

    request_id: str
    question: str
    original: str
    decision: SafetyDecision
    started: float
    traditions: frozenset[str] | None = None
    label: str | None = None
    retrieval: RetrievalResult | None = None
    timed_out: bool = False
    prompt_version: str | None = None
    model: str = "none"
    codes: list[str] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)

    def add_stage(self, name: str, seconds: float) -> None:
        """Add to a stage's time; a stage can run twice (regeneration)."""
        self.stages[name] = self.stages.get(name, 0.0) + seconds


@dataclass(frozen=True)
class Attempt:
    """One generation attempt: a response, or a correction to retry with, or neither."""

    response: PriestAnswerResponse | None = None
    correction: str | None = None


# ── pure helpers ─────────────────────────────────────────────────────────


def clean_question(question: str) -> str:
    """De-identify by regex, strip fence runs, collapse whitespace, cap the length."""
    stripped = strip_fence_runs(strip_pii_regex(question))
    return " ".join(stripped.split())[:MAX_QUESTION_CHARS]


def decide(original: str, cleaned: str) -> SafetyDecision:
    """Route both texts: the PII pass can swallow a crisis phrase ("I'm suicidal")."""
    first, second = safety_router.route(original), safety_router.route(cleaned)
    for kind in ("crisis", "deferral"):
        for decision in (first, second):
            if decision.kind == kind:
                return decision
    return SafetyDecision("pass", None, first.ruling_footer or second.ruling_footer)


def is_fixed_reply(question: str) -> bool:
    """Whether a question gets fixed text without retrieval or a model.

    True for a crisis, a deferral or a script the Guide cannot read. The route asks
    before it meters a device: help and referrals cost nothing, so they are never
    refused for asking too often.
    """
    original = " ".join(strip_fence_runs(question).split())[:MAX_QUESTION_CHARS]
    decision = decide(original, clean_question(question))
    return decision.kind != "pass" or safety_router.is_unsupported_script(original)


def failure_site(exc: BaseException) -> str:
    """Where an exception was raised, as ``file:line`` only: never its message, which
    can echo the question, but enough to find a programming error."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}"


def tradition_scope(
    tradition: TraditionId | None,
) -> tuple[frozenset[str] | None, str | None]:
    """The retrieval filter and prompt label: the request intersected with the admin list.

    An empty filter means the admin has switched off the requested tradition.
    """
    enabled = priest_config.enabled_traditions()
    if tradition is None:
        return enabled, None
    wanted = frozenset({tradition.value})
    allowed = wanted if enabled is None else wanted & enabled
    return allowed, (TRADITION_LABELS.get(tradition.value) if allowed else None)


def record_answer(
    run: QuestionRun, response: PriestAnswerResponse, log: logging.Logger
) -> PriestAnswerResponse:
    """Count the outcome and stage times and write the one metadata log line.

    *log* is the service's logger, so the line keeps its logger name. It holds the
    request id, outcome, versions, validator codes and counts, never the question.
    """
    metrics.record_outcome(response.kind.value)
    for stage, seconds in run.stages.items():
        metrics.record_latency(stage, seconds)
    found = run.retrieval
    log.info(
        "priest answer request_id=%s kind=%s index=%s model=%s prompt=%s codes=%s "
        "sources=%d dense=%.3f bm25=%.3f stages_ms=%s",
        run.request_id,
        response.kind.value,
        response.index_version or "-",
        run.model,
        run.prompt_version or "-",
        ",".join(run.codes) or "-",
        len(found.chunks) if found else 0,
        found.best_dense if found else 0.0,
        found.best_bm25 if found else 0.0,
        ",".join(f"{k}:{v * 1000:.0f}" for k, v in run.stages.items()),
    )
    return response
