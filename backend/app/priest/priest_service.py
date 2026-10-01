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
from collections.abc import Callable
from pathlib import Path
from typing import Final

from app.config import settings
from app.exceptions import (
    PriestEndpointError,
    PriestIndexError,
    PriestLLMError,
    PriestUnavailableError,
)
from app.llm.chat_client import ChatMessage
from app.llm.chat_endpoint import checked_base, refuse_cloud_model
from app.llm.prompt_loader import PromptError
from app.models.confession import ModerationSeverity
from app.priest import metrics, moderation, priest_config, safety_router
from app.priest.answer_validator import CORRECTION_LINES, validate_answer
from app.priest.embedder import OllamaEmbedder
from app.priest.index_store import ActiveIndex, shared_active_index
from app.priest.live_chain import LiveChain
from app.priest.prompt_builder import BuiltPrompt, build_messages, generate_canary
from app.priest.retriever import Retriever
from app.priest.schemas import (
    PriestAnswerResponse,
    PriestDraft,
    TraditionId,
)
from app.priest.service_moderation import (
    MODERATION_CAP_SECONDS,
)
from app.priest.service_pipeline import BUSY_RETRY_AFTER, PassPath
from app.priest.service_responses import (
    EXCERPTS_FRAMING,
    answer_response,
    crisis_response,
    english_only_response,
    excerpts,
    sources,
)
from app.priest.service_run import (
    Attempt,
    ChatChain,
    ModeratorFn,
    QuestionRetriever,
    QuestionRun,
    is_fixed_reply,
    record_answer,
)
from app.priest.slots import LiveSlots
from app.priest.types import RetrievalResult

# Imported from here by the route, the scripts and the tests (``eval_priest`` swaps
# ``generate_canary``, ``validate_answer`` and ``metrics.record_latency`` on this module).
__all__ = [
    "BUSY_RETRY_AFTER",
    "EXCERPTS_FRAMING",
    "MODERATION_CAP_SECONDS",
    "ChatChain",
    "LiveChain",
    "LiveRetriever",
    "PriestService",
    "build_priest_service",
    "generate_canary",
    "get_priest_service",
    "is_fixed_reply",
    "metrics",
    "reset_priest_service",
    "validate_answer",
]

logger = logging.getLogger(__name__)

BUSY_WAIT_SECONDS: Final = 2.0
_MAX_ATTEMPTS: Final = 2
_MIN_CHAT_SECONDS: Final = 0.1
# Asks the server for structured output (guided decoding on vLLM, JSON mode on Ollama).
_DRAFT_SCHEMA: Final = PriestDraft.model_json_schema()


# ── the service ──────────────────────────────────────────────────────────


class PriestService(PassPath):
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
            return self._finish(run, crisis_response(run))
        if run.decision.kind == "deferral":
            return self._finish(run, await self._deferral_or_crisis(run))
        if safety_router.is_unsupported_script(run.original):
            return self._finish(run, english_only_response(run))
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

    async def _generate(
        self, run: QuestionRun, retrieval: RetrievalResult
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
        return excerpts(run)

    async def _attempt(
        self, run: QuestionRun, retrieval: RetrievalResult, correction: str | None
    ) -> Attempt:
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
            return Attempt()
        run.prompt_version = built.prompt_version
        reply = await self._chat(run, built)
        if reply is None:
            return Attempt()
        return self._judge(run, retrieval, built, reply)

    async def _chat(self, run: QuestionRun, built: BuiltPrompt) -> str | None:
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
        self,
        run: QuestionRun,
        retrieval: RetrievalResult,
        built: BuiltPrompt,
        reply: str,
    ) -> Attempt:
        """Validate a reply; on failure say which correction lines to retry with."""
        began = self._clock()
        cited_sources = sources(built, retrieval)
        outcome = validate_answer(
            reply,
            cited_sources,
            canary=built.canary,
            instruction_text=built.rules_text or built.messages[0]["content"],
        )
        run.add_stage("validate", self._clock() - began)
        if outcome.ok:
            return Attempt(response=answer_response(run, cited_sources, outcome))
        run.codes.extend(outcome.codes)
        logger.info(
            "priest validation failed request_id=%s codes=%s",
            run.request_id,
            ",".join(outcome.codes),
        )
        lines = (CORRECTION_LINES.get(code, "") for code in outcome.codes)
        return Attempt(correction=" ".join(line for line in lines if line))

    def _finish(
        self, run: QuestionRun, response: PriestAnswerResponse
    ) -> PriestAnswerResponse:
        """Record metrics and write the one metadata log line for this question."""
        run.add_stage("total", self._clock() - run.started)
        return record_answer(run, response, logger)


# ── the real wiring ──────────────────────────────────────────────────────


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
