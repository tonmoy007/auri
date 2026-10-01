"""Admission, the deadline and the Guide's pass path (plan 14.2; split out of
``priest_service``).

``PassPath`` is mixed into ``PriestService``. It cleans and routes a question, takes
a concurrency slot or refuses as busy, and owns the order that makes the pass path
safe: moderation starts first and its verdict is always awaited, so a crisis heard
by moderation replaces whatever retrieval or generation produced, even an error.
Generation itself stays in ``priest_service``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Final

from app.exceptions import PriestIndexError, PriestUnavailableError
from app.llm.fencing import strip_fence_runs
from app.models.confession import ModerationSeverity
from app.priest import metrics, priest_config
from app.priest.schemas import MAX_QUESTION_CHARS, PriestAnswerResponse, TraditionId
from app.priest.service_moderation import moderate, moderate_deferral
from app.priest.service_responses import (
    crisis_response,
    deferral_response,
    excerpts,
    not_covered,
)
from app.priest.service_run import (
    ModeratorFn,
    QuestionRetriever,
    QuestionRun,
    clean_question,
    decide,
    failure_site,
    tradition_scope,
)
from app.priest.slots import LiveSlots
from app.priest.types import RetrievalResult

logger = logging.getLogger(__name__)

BUSY_RETRY_AFTER: Final = 5


class PassPath:
    """Admission, moderation, the deadline and the retrieve-then-generate pipeline."""

    _retriever: QuestionRetriever
    _moderator: ModeratorFn
    _moderation_cap: float
    _clock: Callable[[], float]
    _slots: LiveSlots
    _busy_wait: float
    _deadline_override: float | None

    def _deadline(self) -> float:
        if self._deadline_override is not None:
            return self._deadline_override
        return float(priest_config.total_deadline_seconds())

    def _remaining(self, run: QuestionRun) -> float:
        """Seconds left of the overall deadline, never negative."""
        return max(0.0, self._deadline() - (self._clock() - run.started))

    async def _generate(
        self, run: QuestionRun, retrieval: RetrievalResult
    ) -> PriestAnswerResponse:
        raise NotImplementedError

    def _prepare(
        self,
        question: str,
        tradition: TraditionId | None,
        request_id: str,
        started: float,
    ) -> QuestionRun:
        """Clean the question and route it; this is the whole "safety" stage."""
        original = " ".join(strip_fence_runs(question).split())[:MAX_QUESTION_CHARS]
        cleaned = clean_question(question)
        run = QuestionRun(
            request_id, cleaned, original, decide(original, cleaned), started
        )
        run.traditions, run.label = tradition_scope(tradition)
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

    async def _heavy(self, run: QuestionRun) -> PriestAnswerResponse:
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
            return crisis_response(run)
        if outcome is None and run.timed_out:
            # Too slow, not broken: the index is fine, so say "busy", not "unavailable".
            raise PriestUnavailableError("priest_busy", BUSY_RETRY_AFTER)
        if outcome is None:
            raise PriestUnavailableError("priest_index_unavailable")
        return outcome

    async def _moderate(self, text: str) -> ModerationSeverity:
        """Moderate on local Ollama in a thread; a failure or timeout is policy."""
        return await moderate(self._moderator, text, self._moderation_cap)

    async def _deferral_or_crisis(self, run: QuestionRun) -> PriestAnswerResponse:
        """The fixed deferral, unless moderation hears a crisis in the question.

        The router's lexicons decide a deferral; moderation is the backstop that
        the pass path always had and this path did not (privacy review row 43).
        """
        severity = await self._moderate_deferral(run.original)
        if severity is ModerationSeverity.crisis:
            return crisis_response(run)
        return deferral_response(run)

    async def _moderate_deferral(self, text: str) -> ModerationSeverity:
        """Moderate a deferral question if a check slot is free; never queue for one."""
        return await moderate_deferral(self._moderator, text, self._moderation_cap)

    async def _within_deadline(self, run: QuestionRun) -> PriestAnswerResponse | None:
        """Run the pipeline; ``None`` means there is nothing to show (index problem)."""
        try:
            return await asyncio.wait_for(
                self._pipeline(run), timeout=self._remaining(run)
            )
        except TimeoutError:
            run.timed_out = True
            logger.warning("priest deadline passed request_id=%s", run.request_id)
            return excerpts(run) if run.retrieval and run.retrieval.chunks else None
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
                failure_site(exc),
                run.request_id,
            )
            return None

    async def _pipeline(self, run: QuestionRun) -> PriestAnswerResponse:
        """Retrieve, then generate; below the relevance floor nothing is generated."""
        if run.traditions is not None and not run.traditions:
            return not_covered(run, None)
        began = self._clock()
        retrieval = await self._retriever.retrieve(run.question, run.traditions)
        run.retrieval = retrieval
        run.add_stage("retrieve", self._clock() - began)
        if not retrieval.covered or not retrieval.chunks:
            return not_covered(run, retrieval.index_version)
        return await self._generate(run, retrieval)
