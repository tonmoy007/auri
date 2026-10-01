"""Tests for the priest-mode ops smoke script.

The script is meant to run against the real index and servers, so here everything it
touches is faked: the retriever returns hand-made chunks, the chat chain replays one
valid draft, and the moderator is quiet. Nothing touches the network, Ollama, vLLM or
the real vault. What the script must guarantee is that it prints only kinds, note
titles and pass or fail, never an answer, and that the crisis question reaches the
chat model zero times.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from app.llm.chat_client import ChatMessage, ChatResult
from app.models.confession import ModerationSeverity
from app.priest import safety_router
from app.priest.priest_service import LiveChain, PriestService
from app.priest.schemas import AnswerKind, PriestAnswerResponse
from app.priest.types import Chunk, QuoteBlock, RetrievalResult, RetrievedChunk

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "priest_smoke.py"
INDEX_VERSION = "20260930T000000Z-abcd1234"
SEED_QUOTE = "Bring me one seed from a house where no one has died."
ANSWER_MARKER = "answer-marker-k3"
SNIPPET_MARKER = "snippet-marker-q9"
TITLES = ("Seed Story", "Second Note")


def _load_script() -> ModuleType:
    """Import the script by path, as it is not part of a package."""
    spec = importlib.util.spec_from_file_location("priest_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


def _chunk(i: int, title: str, body: str) -> Chunk:
    text = f"{title} › The Search\n\n{body}"
    return Chunk(
        chunk_id=f"c{i:03d}",
        note_path=f"stories/{title.casefold().replace(' ', '-')}.md",
        note_title=title,
        heading_path=(title, "The Search"),
        obsidian_anchor="",
        note_type="story",
        traditions=("buddhism",),
        text=text,
        quote_blocks=(QuoteBlock(SEED_QUOTE, None, True),) if i == 1 else (),
        char_len=len(text),
    )


def _result(covered: bool) -> RetrievalResult:
    bodies = (f'{SNIPPET_MARKER}\n\n> "{SEED_QUOTE}"', "Second body.")
    chunks = tuple(
        RetrievedChunk(_chunk(i, TITLES[i - 1], bodies[i - 1]), i, 0.8, 4.0, 0.03)
        for i in (1, 2)
    )
    return RetrievalResult(chunks, covered, 0.8, 4.0, INDEX_VERSION)


class FakeRetriever:
    """Covers every question except the off-topic one; records what it returned."""

    def __init__(self, *, cover_grief: bool = True) -> None:
        self.cover_grief = cover_grief
        self.returned_titles: set[str] = set()

    async def retrieve(
        self, question: str, traditions: frozenset[str] | None
    ) -> RetrievalResult:
        """Return two chunks for a grief question, an uncovered result otherwise."""
        about_grief = "grief" in question.casefold()
        result = _result(covered=about_grief and self.cover_grief)
        self.returned_titles |= {c.chunk.note_title for c in result.chunks}
        return result


class FakeChain:
    """Replies with one valid draft, counting calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], **kwargs: Any
    ) -> ChatResult:
        """Count the call and return a draft that cites the first source."""
        self.calls += 1
        draft = {
            "kind": "answer",
            "points": [
                {"text": f"A seed is sought ({ANSWER_MARKER}).", "sources": ["S1"]}
            ],
            "quotes": [{"text": SEED_QUOTE, "source": "S1"}],
            "reflection": "Grief can feel heavy, and sharing it may help.",
        }
        return ChatResult(json.dumps(draft), "fake-model", "vllm")


def _quiet(text: str) -> ModerationSeverity:
    return ModerationSeverity.none


def _service(
    retriever: FakeRetriever | None = None, chain: FakeChain | None = None
) -> tuple[PriestService, FakeRetriever, FakeChain]:
    retriever = retriever or FakeRetriever()
    chain = chain or FakeChain()
    service = PriestService(retriever=retriever, chain=chain, moderator=_quiet)
    return service, retriever, chain


def _run(service: Any, chain: FakeChain) -> tuple[int, str]:
    """Run the smoke checks; return the exit code and everything printed."""
    lines: list[str] = []
    code = asyncio.run(smoke.run_smoke(service, lambda: chain.calls, lines.append))
    return code, "".join(lines)


@pytest.fixture(autouse=True)
def _enabled(set_setting: Callable[[str, object], None]) -> None:
    set_setting("PRIEST_MODE_ENABLED", True)


def test_all_three_questions_pass_and_exit_zero() -> None:
    # Arrange
    service, _, chain = _service()

    # Act
    code, output = _run(service, chain)

    # Assert
    assert code == 0
    assert output.count("PASS") == 3 and "FAIL" not in output


def test_the_crisis_question_makes_zero_chat_calls() -> None:
    # Arrange
    service, _, chain = _service()

    # Act
    code, output = _run(service, chain)

    # Assert
    assert code == 0
    assert chain.calls == 1  # the grief question only
    crisis_line = next(line for line in output.splitlines() if "kind=crisis" in line)
    assert "chat_calls=0" in crisis_line and "template=exact" in crisis_line


def test_the_off_topic_question_is_not_covered_and_makes_no_chat_call() -> None:
    # Arrange
    service, _, chain = _service()

    # Act
    _, output = _run(service, chain)

    # Assert
    line = next(line for line in output.splitlines() if "kind=not_covered" in line)
    assert "chat_calls=0" in line and "PASS" in line


def test_cited_notes_are_a_subset_of_the_retrieved_notes() -> None:
    # Arrange
    service, retriever, chain = _service()

    # Act
    _, output = _run(service, chain)

    # Assert
    line = next(line for line in output.splitlines() if "kind=answer" in line)
    cited = line.split("cited=[", 1)[1].split("]", 1)[0].split("; ")
    assert cited == ["Seed Story"]
    assert set(cited) <= retriever.returned_titles


def test_the_output_never_holds_an_answer_a_snippet_or_a_quote() -> None:
    # Arrange
    service, _, chain = _service()

    # Act
    _, output = _run(service, chain)

    # Assert
    fixed_texts = (safety_router.crisis_reply().text, safety_router.not_covered_text())
    for private in (ANSWER_MARKER, SNIPPET_MARKER, "Grief can feel", SEED_QUOTE):
        assert private not in output
    assert all(text not in output for text in fixed_texts)


def test_an_uncovered_grief_question_fails_the_run() -> None:
    # Arrange
    service, _, chain = _service(FakeRetriever(cover_grief=False))

    # Act
    code, output = _run(service, chain)

    # Assert
    assert code == 1
    assert "kind=not_covered" in output and "FAIL" in output


def test_a_degraded_answer_fails_the_run() -> None:
    # Arrange
    chain = FakeChain()

    async def garbage(messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResult:
        chain.calls += 1
        return ChatResult("not json", "fake-model", "vllm")

    chain.complete = garbage  # type: ignore[method-assign]
    service, _, _ = _service(chain=chain)

    # Act
    code, output = _run(service, chain)

    # Assert
    assert code == 1 and "kind=library_excerpts" in output


def test_a_crisis_question_that_reaches_the_chat_model_fails_the_run() -> None:
    # Arrange
    chain = FakeChain()

    class Leaky:
        """A broken service that generates for every question, crisis included."""

        async def answer(
            self, question: str, tradition: object, *, request_id: str
        ) -> PriestAnswerResponse:
            await chain.complete([])
            return PriestAnswerResponse(
                request_id=request_id,
                kind=AnswerKind.crisis,
                notice=safety_router.crisis_reply().text,
            )

    # Act
    code, output = _run(Leaky(), chain)

    # Assert
    crisis_line = next(ln for ln in output.splitlines() if ln.startswith("crisis:"))
    assert code == 1 and "FAIL" in crisis_line and "chat_calls=1" in crisis_line


def test_a_crisis_reply_that_is_not_the_template_fails_the_run() -> None:
    # Arrange
    chain = FakeChain()

    class Improvising:
        """A broken service that paraphrases the crisis text."""

        async def answer(
            self, question: str, tradition: object, *, request_id: str
        ) -> PriestAnswerResponse:
            return PriestAnswerResponse(
                request_id=request_id,
                kind=AnswerKind.crisis,
                notice="Call somebody.",
            )

    # Act
    code, output = _run(Improvising(), chain)

    # Assert
    assert code == 1 and "template=different" in output


def test_a_disabled_guide_exits_with_its_own_code_and_names_the_reason(
    set_setting: Callable[[str, object], None],
) -> None:
    # Arrange
    set_setting("PRIEST_MODE_ENABLED", False)
    service, _, chain = _service()

    # Act
    code, output = _run(service, chain)

    # Assert
    assert code == 2
    assert "priest_mode_disabled" in output and chain.calls == 0


def test_main_runs_the_real_service_with_a_counting_wrapper_over_the_live_chain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    _, _, fake_chain = _service()
    seen: dict[str, Any] = {}

    def build(*, chain: Any = None, **kwargs: Any) -> PriestService:
        seen["chain"] = chain
        return PriestService(
            retriever=FakeRetriever(), chain=fake_chain, moderator=_quiet
        )

    monkeypatch.setattr(smoke, "build_priest_service", build)

    # Act
    code = smoke.main()

    # Assert
    assert code == 0
    assert isinstance(seen["chain"].inner, LiveChain)
    assert "PASS" in capsys.readouterr().out
