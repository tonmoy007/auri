"""Answer one priest-mode question from the study library (plan 3.2).

The order of things is the safety argument. A question is cleaned, then a lexicon
router settles crisis and deferral questions with fixed text before any retrieval or
model call. Everything else is answered only from retrieved notes; the model's reply
is validated by code, regenerated once, and replaced by plain excerpts if it still
fails. A moderation check on local Ollama runs beside all of this and a crisis verdict
from it discards whatever was generated.

Privacy: nothing here stores a question or an answer, and nothing logs one. The log
line holds the request id, the outcome, timings, versions, validator codes and counts.
Exceptions are logged by class name, never by message, because a message can echo the
text being processed. The chat chain only ever sees endpoints resolved by
``app.llm.chat_endpoint``, which refuses hosted providers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

import httpx

from app.config import settings
from app.exceptions import (
    AuriError,
    PriestEndpointError,
    PriestIndexError,
    PriestLLMError,
    PriestUnavailableError,
)
from app.llm.chat_client import ChatClient, ChatMessage, ChatResult, PriestLLMChain
from app.llm.chat_endpoint import (
    ChatEndpoint,
    checked_base,
    refuse_cloud_model,
    resolve_fallback,
    resolve_primary,
)
from app.llm.fencing import strip_fence_runs
from app.llm.prompt_loader import PromptError
from app.models.confession import ModerationSeverity
from app.priest import metrics, moderation, priest_config, safety_router
from app.priest.answer_validator import (
    CORRECTION_LINES,
    SourceChunk,
    ValidationOutcome,
    validate_answer,
)
from app.priest.embedder import OllamaEmbedder
from app.priest.index_store import ActiveIndex, shared_active_index
from app.priest.prompt_builder import BuiltPrompt, build_messages, generate_canary
from app.priest.retriever import Retriever
from app.priest.safety_router import SafetyDecision
from app.priest.schemas import (
    MAX_QUESTION_CHARS,
    MAX_SNIPPET_CHARS,
    TRADITION_LABELS,
    AnswerKind,
    CrisisContactOut,
    PriestAnswerResponse,
    PriestCitation,
    PriestDraft,
    PriestPoint,
    PriestQuote,
    TraditionId,
)
from app.priest.slots import LiveSlots
from app.priest.types import Chunk, RetrievalResult
from app.services.deidentify import strip_pii_regex

logger = logging.getLogger(__name__)

ModeratorFn = Callable[[str], ModerationSeverity]

# Fixed text for the deterministic fallback; never model output.
EXCERPTS_FRAMING: Final = (
    "I could not put together a reliable summary this time, so here are the "
    "passages from the study library that looked most relevant to your question."
)
EXCERPT_COUNT: Final = 3
MODERATION_CAP_SECONDS: Final = 5.0
BUSY_WAIT_SECONDS: Final = 2.0
BUSY_RETRY_AFTER: Final = 5
_MAX_ATTEMPTS: Final = 2
_MIN_CHAT_SECONDS: Final = 0.1
# Asks the server for structured output (guided decoding on vLLM, JSON mode on Ollama).
_DRAFT_SCHEMA: Final = PriestDraft.model_json_schema()
# Moderation gets its own small pool. The shared default pool also serves every other
# ``to_thread`` caller, and a stuck Ollama must not be able to starve them (or itself).
_MODERATION_POOL: Final = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="priest-moderation"
)
# Deferral replies skip the rate limiter (never a 429 instead of help), so their
# moderation check gets a separate pool and a hard cap with no queue: a flood of
# deferral-phrased questions can neither starve the pass path's moderation, where a
# crisis verdict matters most, nor pile work onto Ollama. Past the cap a deferral goes
# out unmoderated, as before this check existed.
DEFERRAL_CHECKS: Final = 2
_DEFERRAL_POOL: Final = ThreadPoolExecutor(
    max_workers=DEFERRAL_CHECKS, thread_name_prefix="priest-deferral-moderation"
)
_DEFERRAL_SLOTS: Final = threading.BoundedSemaphore(DEFERRAL_CHECKS)
# What a moderation call may raise; any of them counts as "policy", never "crisis".
_MODERATION_ERRORS: Final = (
    AuriError,
    httpx.HTTPError,
    OSError,
    ValueError,
    RuntimeError,
    LookupError,
    TypeError,
)


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
class _Run:
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
class _Attempt:
    """One generation attempt: a response, or a correction to retry with, or neither."""

    response: PriestAnswerResponse | None = None
    correction: str | None = None


# ── pure helpers ─────────────────────────────────────────────────────────


def _clean(question: str) -> str:
    """De-identify by regex, strip fence runs, collapse whitespace, cap the length."""
    stripped = strip_fence_runs(strip_pii_regex(question))
    return " ".join(stripped.split())[:MAX_QUESTION_CHARS]


def _decide(original: str, cleaned: str) -> SafetyDecision:
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
    decision = _decide(original, _clean(question))
    return decision.kind != "pass" or safety_router.is_unsupported_script(original)


def _failure_site(exc: BaseException) -> str:
    """Where an exception was raised, as ``file:line`` only: never its message, which
    can echo the question, but enough to find a programming error."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}"


def _tradition_scope(
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


def _snippet(chunk: Chunk) -> str:
    """The chunk body without its breadcrumb, on one line, at most 280 characters."""
    body = chunk.text.split("\n\n", 1)[-1]
    flat = " ".join(body.split())
    if len(flat) <= MAX_SNIPPET_CHARS:
        return flat
    cut = flat[: MAX_SNIPPET_CHARS - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip() + "…"


def _citation(source_id: str, chunk: Chunk) -> PriestCitation:
    return PriestCitation(
        id=source_id,
        note_title=chunk.note_title,
        heading_path=list(chunk.heading_path),
        note_type=chunk.note_type,
        tradition_labels=[
            TRADITION_LABELS[t] for t in chunk.traditions if t in TRADITION_LABELS
        ],
        snippet=_snippet(chunk),
    )


def _notice(base: str | None, decision: SafetyDecision) -> str | None:
    """Fixed text for the response, with the scholar footer when a ruling was asked for."""
    parts = [base] if base else []
    if decision.ruling_footer:
        parts.append(safety_router.ruling_footer_text())
    return "\n\n".join(parts) or None


def _sources(built: BuiltPrompt, retrieval: RetrievalResult) -> list[SourceChunk]:
    """The chunks under the ids the prompt gave them (ascending rank, as the builder does)."""
    ranked = sorted(retrieval.chunks, key=lambda item: item.rank)
    return [
        SourceChunk(source_id, item.chunk)
        for source_id, item in zip(built.source_ids, ranked, strict=True)
    ]


# ── the service ──────────────────────────────────────────────────────────


class PriestService:
    """Answers questions from the study library; built from fakes in tests."""

    def __init__(
        self,
        *,
        retriever: QuestionRetriever,
        chain: ChatChain,
        moderator: ModeratorFn,
        clock: Callable[[], float] = time.monotonic,
        max_concurrency: int | None = None,
        busy_wait_seconds: float = BUSY_WAIT_SECONDS,
        moderation_cap_seconds: float = MODERATION_CAP_SECONDS,
        deadline_seconds: float | None = None,
    ) -> None:
        """Create a service.

        Args:
            retriever: Finds passages for a cleaned question.
            chain: Generates replies, from servers the operator runs.
            moderator: A blocking moderation call (local Ollama); run in a thread.
            clock: A monotonic clock, for timings and the deadline.
            max_concurrency: Questions in flight at once; config when omitted.
            busy_wait_seconds: How long to wait for a free slot before "busy".
            moderation_cap_seconds: How long moderation may take before it counts as
                policy.
            deadline_seconds: Limit for one question; config when omitted.
        """
        self._retriever = retriever
        self._chain = chain
        self._moderator = moderator
        self._clock = clock
        # The limit is read on every request, so a dashboard change applies at once.
        self._slots = LiveSlots(
            (lambda: max_concurrency)
            if max_concurrency
            else priest_config.max_concurrency
        )
        self._busy_wait = busy_wait_seconds
        self._moderation_cap = moderation_cap_seconds
        self._deadline_override = deadline_seconds

    def _deadline(self) -> float:
        if self._deadline_override is not None:
            return self._deadline_override
        return float(priest_config.total_deadline_seconds())

    def _remaining(self, run: _Run) -> float:
        """Seconds left of the overall deadline, never negative."""
        return max(0.0, self._deadline() - (self._clock() - run.started))

    async def answer(
        self, question: str, tradition: TraditionId | None, *, request_id: str
    ) -> PriestAnswerResponse:
        """Answer one question.

        Args:
            question: The user's words; cleaned here, never stored or logged.
            tradition: An optional filter, intersected with the admin's list.
            request_id: A random id for this request, echoed back and logged.

        Raises:
            PriestUnavailableError: ``priest_mode_disabled`` (before any work),
                ``priest_busy`` (no free slot within two seconds) or
                ``priest_index_unavailable``.
        """
        started = self._clock()
        if not priest_config.enabled():
            raise self._refused(request_id, "priest_mode_disabled", "disabled")
        run = self._prepare(question, tradition, request_id, started)
        if run.decision.kind == "crisis":
            return self._finish(run, self._crisis_response(run))
        if run.decision.kind == "deferral":
            return self._finish(run, await self._deferral_or_crisis(run))
        if safety_router.is_unsupported_script(run.original):
            return self._finish(run, self._english_only_response(run))
        await self._acquire(request_id)
        try:
            return self._finish(run, await self._heavy(run))
        except PriestUnavailableError as exc:
            outcome = "busy" if exc.code == "priest_busy" else "error"
            raise self._refused(
                request_id, exc.code, outcome, exc.retry_after
            ) from None
        finally:
            await self._slots.release()

    def _prepare(
        self,
        question: str,
        tradition: TraditionId | None,
        request_id: str,
        started: float,
    ) -> _Run:
        """Clean the question and route it; this is the whole "safety" stage."""
        original = " ".join(strip_fence_runs(question).split())[:MAX_QUESTION_CHARS]
        cleaned = _clean(question)
        run = _Run(request_id, cleaned, original, _decide(original, cleaned), started)
        run.traditions, run.label = _tradition_scope(tradition)
        run.add_stage("safety", self._clock() - started)
        return run

    async def _acquire(self, request_id: str) -> None:
        """Take a slot, or raise busy if none is free in time."""
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self._busy_wait)
        except TimeoutError:
            raise self._refused(
                request_id, "priest_busy", "busy", BUSY_RETRY_AFTER
            ) from None

    def _refused(
        self, request_id: str, code: str, outcome: str, retry_after: int | None = None
    ) -> PriestUnavailableError:
        """Count and log a refusal (by id and reason only) and return the error to raise."""
        metrics.record_outcome(outcome)
        logger.info("priest request refused request_id=%s reason=%s", request_id, code)
        return PriestUnavailableError(code, retry_after)

    # ── the pass path: moderation beside retrieval and generation ────────

    async def _heavy(self, run: _Run) -> PriestAnswerResponse:
        """Answer a question the router passed; a moderation crisis always wins."""
        task = asyncio.create_task(self._moderate(run.original))
        try:
            outcome = await self._within_deadline(run)
            waited = self._clock()
            severity = await task
            run.add_stage("safety", self._clock() - waited)
        finally:
            task.cancel()
        crisis = severity is ModerationSeverity.crisis
        if crisis:
            return self._crisis_response(run)
        if outcome is None and run.timed_out:
            # Too slow, not broken: the index is fine, so say "busy", not "unavailable".
            raise PriestUnavailableError("priest_busy", BUSY_RETRY_AFTER)
        if outcome is None:
            raise PriestUnavailableError("priest_index_unavailable")
        return outcome

    async def _moderate(self, text: str) -> ModerationSeverity:
        """Moderate on local Ollama in a thread; a failure or timeout is policy."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_MODERATION_POOL, self._moderator, text),
                timeout=self._moderation_cap,
            )
        except TimeoutError:
            logger.warning("priest moderation timed out; counting it as policy")
        except _MODERATION_ERRORS as exc:
            logger.warning(
                "priest moderation failed (%s); counting it as policy",
                type(exc).__name__,
            )
        return ModerationSeverity.policy

    async def _deferral_or_crisis(self, run: _Run) -> PriestAnswerResponse:
        """The fixed deferral, unless moderation hears a crisis in the question.

        The router's lexicons decide a deferral; moderation is the backstop that
        the pass path always had and this path did not (privacy review row 43).
        """
        severity = await self._moderate_deferral(run.original)
        if severity is ModerationSeverity.crisis:
            return self._crisis_response(run)
        return self._deferral_response(run)

    async def _moderate_deferral(self, text: str) -> ModerationSeverity:
        """Moderate a deferral question if a check slot is free; never queue for one."""
        if not _DEFERRAL_SLOTS.acquire(blocking=False):
            logger.info("priest deferral moderation skipped: all checks busy")
            return ModerationSeverity.none
        # The slot is returned when the thread really finishes, not when we stop
        # waiting for it, so a hung moderator still counts against the cap.
        job = _DEFERRAL_POOL.submit(self._moderator, text)
        job.add_done_callback(lambda _job: _DEFERRAL_SLOTS.release())
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(job), timeout=self._moderation_cap
            )
        except TimeoutError:
            logger.warning("priest deferral moderation timed out; deferring")
        except _MODERATION_ERRORS as exc:
            logger.warning(
                "priest deferral moderation failed (%s); deferring", type(exc).__name__
            )
        return ModerationSeverity.none

    async def _within_deadline(self, run: _Run) -> PriestAnswerResponse | None:
        """Run the pipeline; ``None`` means there is nothing to show (index problem)."""
        try:
            return await asyncio.wait_for(
                self._pipeline(run), timeout=self._remaining(run)
            )
        except TimeoutError:
            run.timed_out = True
            logger.warning("priest deadline passed request_id=%s", run.request_id)
            return (
                self._excerpts(run) if run.retrieval and run.retrieval.chunks else None
            )
        except PriestIndexError as exc:
            logger.warning(
                "priest index unavailable (%s) request_id=%s",
                type(exc).__name__,
                run.request_id,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — whatever the pipeline raised, moderation is consulted before an error goes out, or a crisis verdict would be lost; the class name is logged, never the message, which can echo the question
            logger.error(
                "priest pipeline failed (%s at %s) request_id=%s",
                type(exc).__name__,
                _failure_site(exc),
                run.request_id,
            )
            return None

    async def _pipeline(self, run: _Run) -> PriestAnswerResponse:
        """Retrieve, then generate; below the relevance floor nothing is generated."""
        if run.traditions is not None and not run.traditions:
            return self._not_covered(run, None)
        began = self._clock()
        retrieval = await self._retriever.retrieve(run.question, run.traditions)
        run.retrieval = retrieval
        run.add_stage("retrieve", self._clock() - began)
        if not retrieval.covered or not retrieval.chunks:
            return self._not_covered(run, retrieval.index_version)
        return await self._generate(run, retrieval)

    async def _generate(
        self, run: _Run, retrieval: RetrievalResult
    ) -> PriestAnswerResponse:
        """Generate and validate; regenerate once with a correction, then excerpts."""
        correction: str | None = None
        for _ in range(_MAX_ATTEMPTS):
            attempt = await self._attempt(run, retrieval, correction)
            if attempt.response is not None:
                return attempt.response
            if attempt.correction is None:
                break
            correction = attempt.correction
        return self._excerpts(run)

    async def _attempt(
        self, run: _Run, retrieval: RetrievalResult, correction: str | None
    ) -> _Attempt:
        try:
            built = build_messages(
                run.question,
                retrieval.chunks,
                persona_name=priest_config.persona_name(),
                tradition_label=run.label,
                canary=generate_canary(),
                correction=correction,
            )
        except PromptError:
            logger.error("priest prompt unavailable request_id=%s", run.request_id)
            return _Attempt()
        run.prompt_version = built.prompt_version
        reply = await self._chat(run, built)
        if reply is None:
            return _Attempt()
        return self._judge(run, retrieval, built, reply)

    async def _chat(self, run: _Run, built: BuiltPrompt) -> str | None:
        """One chat call within the time left; ``None`` if no server gave a reply."""
        began = self._clock()
        limit = float(
            max(
                _MIN_CHAT_SECONDS,
                min(priest_config.llm_timeout_seconds(), self._remaining(run)),
            )
        )
        messages = [ChatMessage(m["role"], m["content"]) for m in built.messages]
        try:
            result = await self._chain.complete(
                messages,
                request_id=run.request_id,
                json_schema=_DRAFT_SCHEMA,
                timeout=limit,
            )
        except (PriestEndpointError, PriestLLMError) as exc:
            logger.warning(
                "priest chat failed (%s) request_id=%s",
                type(exc).__name__,
                run.request_id,
            )
            return None
        finally:
            run.add_stage("generate", self._clock() - began)
        run.model = result.model
        return result.text

    def _judge(
        self, run: _Run, retrieval: RetrievalResult, built: BuiltPrompt, reply: str
    ) -> _Attempt:
        """Validate a reply; on failure say which correction lines to retry with."""
        began = self._clock()
        sources = _sources(built, retrieval)
        outcome = validate_answer(
            reply,
            sources,
            canary=built.canary,
            instruction_text=built.rules_text or built.messages[0]["content"],
        )
        run.add_stage("validate", self._clock() - began)
        if outcome.ok:
            return _Attempt(response=self._answer_response(run, sources, outcome))
        run.codes.extend(outcome.codes)
        logger.info(
            "priest validation failed request_id=%s codes=%s",
            run.request_id,
            ",".join(outcome.codes),
        )
        lines = (CORRECTION_LINES.get(code, "") for code in outcome.codes)
        return _Attempt(correction=" ".join(line for line in lines if line))

    # ── building responses ───────────────────────────────────────────────

    def _answer_response(
        self, run: _Run, sources: list[SourceChunk], outcome: ValidationOutcome
    ) -> PriestAnswerResponse:
        """A validated draft as a response; only the sources it cites become citations."""
        draft = outcome.draft
        index_version = run.retrieval.index_version if run.retrieval else None
        if draft is None or draft.kind == "not_covered":
            return self._not_covered(run, index_version)
        cited = {s for p in draft.points for s in p.sources} | {
            q.source_id for q in outcome.quotes
        }
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.answer,
            points=[
                PriestPoint(text=p.text, citation_ids=list(p.sources))
                for p in draft.points
            ],
            quotes=[
                PriestQuote(text=q.text, citation_id=q.source_id, label=q.label)
                for q in outcome.quotes
            ],
            reflection=draft.reflection,
            citations=[_citation(s.id, s.chunk) for s in sources if s.id in cited],
            notice=_notice(None, run.decision),
            index_version=index_version,
            prompt_version=run.prompt_version,
        )

    def _excerpts(self, run: _Run) -> PriestAnswerResponse:
        """Deterministic fallback: the top passages with fixed framing, no model text."""
        retrieval = run.retrieval
        top = (
            sorted(retrieval.chunks, key=lambda item: item.rank)[:EXCERPT_COUNT]
            if retrieval
            else []
        )
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.library_excerpts,
            citations=[
                _citation(f"S{i}", item.chunk) for i, item in enumerate(top, start=1)
            ],
            notice=_notice(EXCERPTS_FRAMING, run.decision),
            index_version=retrieval.index_version if retrieval else None,
            prompt_version=run.prompt_version,
        )

    def _not_covered(
        self, run: _Run, index_version: str | None
    ) -> PriestAnswerResponse:
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.not_covered,
            notice=_notice(safety_router.not_covered_text(), run.decision),
            index_version=index_version,
        )

    def _crisis_response(self, run: _Run) -> PriestAnswerResponse:
        """The fixed crisis template; its text goes in ``notice``, contacts beside it."""
        reply = safety_router.crisis_reply()
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.crisis,
            notice=reply.text,
            contacts=[
                CrisisContactOut(label=c.label, detail=c.detail, dial=c.dial)
                for c in reply.contacts
            ],
        )

    def _english_only_response(self, run: _Run) -> PriestAnswerResponse:
        """A fixed notice for a script the Guide cannot answer; no retrieval, no model."""
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.not_covered,
            notice=safety_router.english_only_text(),
        )

    def _deferral_response(self, run: _Run) -> PriestAnswerResponse:
        text, contacts = safety_router.render_deferral(run.decision.category or "")
        return PriestAnswerResponse(
            request_id=run.request_id,
            kind=AnswerKind.deferral,
            notice=text,
            contacts=[
                CrisisContactOut(label=c.label, detail=c.detail, dial=c.dial)
                for c in contacts
            ]
            or None,
        )

    def _finish(
        self, run: _Run, response: PriestAnswerResponse
    ) -> PriestAnswerResponse:
        """Record metrics and write the one metadata log line for this question."""
        run.add_stage("total", self._clock() - run.started)
        metrics.record_outcome(response.kind.value)
        for stage, seconds in run.stages.items():
            metrics.record_latency(stage, seconds)
        found = run.retrieval
        logger.info(
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


# ── the real wiring ──────────────────────────────────────────────────────


class LiveChain:
    """Resolves the priest endpoints on every call, so config changes apply at once.

    Never the ``auto`` chain: only the primary and fallback that
    ``app.llm.chat_endpoint`` validated, which refuses hosted providers.
    """

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        """Bind an optional HTTP client (tests pass a mock transport)."""
        self._http = http_client

    def _client(self, endpoint: ChatEndpoint | None) -> ChatClient | None:
        return None if endpoint is None else ChatClient(endpoint, client=self._http)

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
        """Generate through the primary then the fallback.

        Raises:
            PriestEndpointError: If an address must not be used (a hosted provider).
            PriestLLMError: If no server is configured or none gave a reply.
        """
        chain = PriestLLMChain(
            self._client(resolve_primary()), self._client(resolve_fallback())
        )
        return await chain.complete(
            messages,
            request_id=request_id,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            json_schema=json_schema,
            timeout=timeout,
        )


class LiveRetriever:
    """Builds the real retriever lazily and rebuilds it when its live settings change.

    Queries are embedded with the active index's own model, not the config value, so
    changing ``PRIEST_EMBED_MODEL`` only affects the next build. A failure to build
    (an unknown model) surfaces on the first question as an index problem.
    """

    def __init__(self, active: ActiveIndex) -> None:
        """Bind the active-index loader; nothing is built yet."""
        self._active = active
        self._key: tuple[str, float, float, int] | None = None
        self._embedder: OllamaEmbedder | None = None
        self._retriever: Retriever | None = None

    def _model(self) -> str:
        try:
            return self._active.get().manifest.embed_model
        except PriestIndexError:
            return priest_config.embed_model()

    async def _current(self) -> Retriever:
        key = (
            await asyncio.to_thread(self._model),
            priest_config.min_relevance_dense(),
            priest_config.min_relevance_bm25(),
            priest_config.top_k(),
        )
        if self._retriever is not None and key == self._key:
            return self._retriever
        if self._embedder is not None and self._key and key[0] != self._key[0]:
            await self._embedder.aclose()
            self._embedder = None
        if self._embedder is None:
            # The embedder reads the cleaned question, so its address gets the same
            # checks as the chat servers: not a hosted provider, not plain http to a
            # public address, not a cloud-hosted model.
            base, _ = checked_base(settings.OLLAMA_BASE_URL, "OLLAMA_BASE_URL")
            refuse_cloud_model(key[0])
            self._embedder = OllamaEmbedder(base, key[0])
        self._retriever = Retriever(
            self._active,
            self._embedder,
            dense_floor=key[1],
            bm25_floor=key[2],
            top_k=key[3],
        )
        self._key = key
        return self._retriever

    async def retrieve(
        self, question: str, traditions: frozenset[str] | None
    ) -> RetrievalResult:
        """Retrieve with the current settings."""
        return await (await self._current()).retrieve(question, traditions)


def default_moderator() -> ModeratorFn:
    """Moderation on the environment's Ollama only (see ``app.priest.moderation``)."""

    def moderate(text: str) -> ModerationSeverity:
        return moderation.moderate(text, timeout=MODERATION_CAP_SECONDS)

    return moderate


def build_priest_service(
    *,
    chain: ChatChain | None = None,
    retriever: QuestionRetriever | None = None,
    moderator: ModeratorFn | None = None,
) -> PriestService:
    """A service wired from config; any part can be swapped (the smoke script counts chat)."""
    return PriestService(
        retriever=retriever
        or LiveRetriever(shared_active_index(Path(settings.PRIEST_INDEX_DIR))),
        chain=chain or LiveChain(),
        moderator=moderator or default_moderator(),
    )


_service: PriestService | None = None
_service_lock = threading.Lock()


def get_priest_service() -> PriestService:
    """The process-wide service, built on first use from the current settings."""
    global _service
    with _service_lock:
        if _service is None:
            _service = build_priest_service()
        return _service


def reset_priest_service() -> None:
    """Forget the shared service so the next call rebuilds it (tests)."""
    global _service
    with _service_lock:
        _service = None
