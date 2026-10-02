"""The Guide's response shapes, built from fixed text and validated drafts (plan 14.2).

Split out of ``priest_service``. Everything here is deterministic: the fixed crisis,
deferral, not-covered and English-only replies, the library-excerpts fallback, and
the conversion of a validated draft into a response. No model text reaches a
response except through ``answer_response``, which only takes a draft the validator
passed.
"""

from __future__ import annotations

from typing import Final

from app.priest import safety_router
from app.priest.answer_validator import SourceChunk, ValidationOutcome
from app.priest.prompt_builder import BuiltPrompt
from app.priest.safety_router import SafetyDecision
from app.priest.schemas import (
    MAX_SNIPPET_CHARS,
    TRADITION_LABELS,
    AnswerKind,
    CrisisContactOut,
    PriestAnswerResponse,
    PriestCitation,
    PriestPoint,
    PriestQuote,
)
from app.priest.service_run import QuestionRun
from app.priest.types import Chunk, RetrievalResult

# Fixed text for the deterministic fallback; never model output.
EXCERPTS_FRAMING: Final = (
    "I could not put together a reliable summary this time, so here are the "
    "passages from the study library that looked most relevant to your question."
)
EXCERPT_COUNT: Final = 3


def snippet(chunk: Chunk) -> str:
    """The chunk body without its breadcrumb, on one line, at most 280 characters."""
    body = chunk.text.split("\n\n", 1)[-1]
    flat = " ".join(body.split())
    if len(flat) <= MAX_SNIPPET_CHARS:
        return flat
    cut = flat[: MAX_SNIPPET_CHARS - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip() + "…"


def citation(source_id: str, chunk: Chunk) -> PriestCitation:
    return PriestCitation(
        id=source_id,
        note_title=chunk.note_title,
        heading_path=list(chunk.heading_path),
        note_type=chunk.note_type,
        tradition_labels=[
            TRADITION_LABELS[t] for t in chunk.traditions if t in TRADITION_LABELS
        ],
        snippet=snippet(chunk),
    )


def notice(base: str | None, decision: SafetyDecision) -> str | None:
    """Fixed text for the response, with the scholar footer when a ruling was asked for."""
    parts = [base] if base else []
    if decision.ruling_footer:
        parts.append(safety_router.ruling_footer_text())
    return "\n\n".join(parts) or None


def sources(built: BuiltPrompt, retrieval: RetrievalResult) -> list[SourceChunk]:
    """The chunks under the ids the prompt gave them (ascending rank, as the builder does)."""
    ranked = sorted(retrieval.chunks, key=lambda item: item.rank)
    return [
        SourceChunk(source_id, item.chunk)
        for source_id, item in zip(built.source_ids, ranked, strict=True)
    ]


def answer_response(
    run: QuestionRun, sources: list[SourceChunk], outcome: ValidationOutcome
) -> PriestAnswerResponse:
    """A validated draft as a response; only the sources it cites become citations."""
    draft = outcome.draft
    index_version = run.retrieval.index_version if run.retrieval else None
    if draft is None or draft.kind == "not_covered":
        return not_covered(run, index_version)
    cited = {s for p in draft.points for s in p.sources} | {
        q.source_id for q in outcome.quotes
    }
    return PriestAnswerResponse(
        request_id=run.request_id,
        kind=AnswerKind.answer,
        points=[
            PriestPoint(text=p.text, citation_ids=list(p.sources)) for p in draft.points
        ],
        quotes=[
            PriestQuote(text=q.text, citation_id=q.source_id, label=q.label)
            for q in outcome.quotes
        ],
        reflection=draft.reflection,
        citations=[citation(s.id, s.chunk) for s in sources if s.id in cited],
        notice=notice(None, run.decision),
        index_version=index_version,
        prompt_version=run.prompt_version,
    )


def excerpts(run: QuestionRun) -> PriestAnswerResponse:
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
            citation(f"S{i}", item.chunk) for i, item in enumerate(top, start=1)
        ],
        notice=notice(EXCERPTS_FRAMING, run.decision),
        index_version=retrieval.index_version if retrieval else None,
        prompt_version=run.prompt_version,
    )


def not_covered(run: QuestionRun, index_version: str | None) -> PriestAnswerResponse:
    return PriestAnswerResponse(
        request_id=run.request_id,
        kind=AnswerKind.not_covered,
        notice=notice(safety_router.not_covered_text(), run.decision),
        index_version=index_version,
    )


def crisis_response(run: QuestionRun) -> PriestAnswerResponse:
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


def english_only_response(run: QuestionRun) -> PriestAnswerResponse:
    """A fixed notice for a script the Guide cannot answer; no retrieval, no model."""
    reply = safety_router.english_only_reply()
    return PriestAnswerResponse(
        request_id=run.request_id,
        kind=AnswerKind.not_covered,
        notice=reply.text,
        contacts=[
            CrisisContactOut(label=c.label, detail=c.detail, dial=c.dial)
            for c in reply.contacts
        ]
        or None,
    )


def deferral_response(run: QuestionRun) -> PriestAnswerResponse:
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
