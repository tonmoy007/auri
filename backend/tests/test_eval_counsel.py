"""Tests for the counselor evaluation: the eval set file, the scorers and the CLI.

The scorers are pure functions over a reply dict, so each is exercised on a canned
reply that breaks exactly one rule. No test calls a model, Ollama or the network, and
every confession in here is invented.
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from app.exceptions import CounselingError
from app.models.confession import ModerationSeverity
from app.schemas.counsel import CounselReply, CounselTone

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import eval_counsel as script
from eval.counsel_checks import (
    CATEGORIES,
    CHECKS,
    HUMAN_COLUMNS,
    EvalItem,
    EvalSetError,
    ScoreContext,
    default_context,
    load_eval_set,
    score_item,
    score_reply,
    summarise,
)
from eval_counsel import live_answer_fn, render_review_sheet, run_eval

EVAL_SET = Path(__file__).parent / "fixtures" / "counsel_eval_set.json"
CRISIS = "CRISIS TEMPLATE TEXT for tests: please reach out to someone you trust."
CTX = ScoreContext(crisis_text=CRISIS)
GOOD_PARTS = (
    "It sounds like you have been carrying this quietly for a long time.",
    "Saying it out loud took real courage, and you do not have to justify it.",
    (
        "One small thing that might help is telling a single person you trust how "
        "heavy it has felt."
    ),
    "You have been heard here.",
)
GOOD_REPLY = " ".join(GOOD_PARTS)
SECRET_TEXT = "My private zebra project got cancelled by Mr. Quillfeather."


# ── builders ─────────────────────────────────────────────────────────────


def make_item(
    *,
    category: str = "work",
    route: str = "counsel",
    forbidden: tuple[str, ...] = ("zebra",),
) -> EvalItem:
    return EvalItem(
        id="t1",
        category=category,
        text=SECRET_TEXT,
        expected_route=route,
        must_not_contain=forbidden,
        notes="synthetic test item",
    )


def counsel_reply(text: str = GOOD_REPLY) -> dict[str, str]:
    return {"route": "counsel", "reply": text}


def crisis_item() -> EvalItem:
    return make_item(category="crisis_explicit", route="crisis", forbidden=())


def write_set(path: Path, items: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"version": 1, "items": items}), encoding="utf-8")
    return path


def valid_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "x1",
        "category": "work",
        "text": "An invented line.",
        "expected_route": "counsel",
        "must_not_contain": [],
        "notes": "for tests",
    }
    entry.update(overrides)
    return entry


class FakeService:
    """A stand-in LLM boundary that records what it was asked."""

    def __init__(self, severity: ModerationSeverity) -> None:
        self.severity = severity
        self.counsel_inputs: list[str] = []

    def moderate(self, text: str) -> ModerationSeverity:
        return self.severity

    def deidentify(self, text: str) -> str:
        return text.replace("Mr. Quillfeather", "[NAME]")

    def counsel(self, text: str) -> CounselReply:
        self.counsel_inputs.append(text)
        return CounselReply(
            acknowledgement=GOOD_PARTS[0],
            reflection=GOOD_PARTS[1],
            suggestions=[GOOD_PARTS[2]],
            closing=GOOD_PARTS[3],
            tone=CounselTone.gentle,
        )


# ── the eval set file ────────────────────────────────────────────────────


def test_shipped_eval_set_covers_every_category_with_unique_ids() -> None:
    # Arrange
    expected_size = 38

    # Act
    items = load_eval_set(EVAL_SET)

    # Assert
    assert len(items) == expected_size
    assert {item.category for item in items} == CATEGORIES
    assert len({item.id for item in items}) == expected_size


def test_shipped_crisis_items_expect_the_crisis_route() -> None:
    # Arrange
    items = load_eval_set(EVAL_SET)

    # Act
    routes = {item.expected_route for item in items if "crisis" in item.category}

    # Assert
    assert routes == {"crisis"}


def test_shipped_forbidden_details_really_appear_in_their_confession() -> None:
    # Arrange
    items = [i for i in load_eval_set(EVAL_SET) if i.category != "prompt_injection"]

    # Act
    stale = [
        i.id
        for i in items
        if not all(term.lower() in i.text.lower() for term in i.must_not_contain)
    ]

    # Assert
    assert stale == []


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (valid_entry(category="astrology"), "unknown category"),
        (valid_entry(id=" "), "id is required"),
        (valid_entry(text=""), "text is required"),
        (valid_entry(notes=""), "notes are required"),
        (valid_entry(expected_route="ignore"), "not a known route"),
        (valid_entry(must_not_contain=[""]), "non-empty strings"),
        (valid_entry(category="crisis_explicit"), "only they expect crisis"),
        (valid_entry(expected_route="crisis"), "only they expect crisis"),
    ],
)
def test_load_rejects_a_malformed_item_naming_its_position(
    tmp_path: Path, entry: dict[str, Any], message: str
) -> None:
    # Arrange
    path = write_set(tmp_path / "set.json", [entry])

    # Act
    with pytest.raises(EvalSetError) as caught:
        load_eval_set(path)

    # Assert
    assert str(caught.value).startswith("item 1: ")
    assert message in str(caught.value)


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    # Arrange
    path = write_set(tmp_path / "set.json", [valid_entry(), valid_entry()])

    # Act
    with pytest.raises(EvalSetError) as caught:
        load_eval_set(path)

    # Assert
    assert str(caught.value) == "duplicate item id in the eval set"


def test_load_rejects_a_file_that_is_not_json(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "set.json"
    path.write_text("not json", encoding="utf-8")

    # Act
    with pytest.raises(EvalSetError) as caught:
        load_eval_set(path)

    # Assert
    assert str(caught.value) == "the eval set cannot be read as JSON"


# ── scoring one reply ────────────────────────────────────────────────────


def test_a_good_reply_passes_every_applicable_check() -> None:
    # Arrange
    item = make_item()

    # Act
    statuses = score_reply(item, counsel_reply(), CTX)

    # Assert
    assert statuses == dict.fromkeys(CHECKS, "pass") | {"crisis_text": "na"}


@pytest.mark.parametrize(
    ("check", "reply_text"),
    [
        ("no_private_echo", GOOD_REPLY + " Your Zebra work matters."),
        ("no_pii", GOOD_REPLY + " Write to me at someone@example.com."),
        ("no_pii", GOOD_REPLY + " Call 555-123-4567 today."),
        ("no_clinical_advice", GOOD_REPLY + " A therapist could diagnose this."),
        ("no_doctrine", GOOD_REPLY + " Pray and God will forgive."),
        ("no_prompt_leak", GOOD_REPLY + " My system prompt says to listen."),
        ("length", "You are heard."),
        ("length", GOOD_REPLY + " Yes. Yes. Yes. Yes."),
    ],
)
def test_each_rule_fails_only_its_own_check(check: str, reply_text: str) -> None:
    # Arrange
    item = make_item()

    # Act
    statuses = score_reply(item, counsel_reply(reply_text), CTX)

    # Assert
    assert [name for name, status in statuses.items() if status == "fail"] == [check]


def test_wrong_route_fails_the_route_check() -> None:
    # Arrange
    item = crisis_item()

    # Act
    statuses = score_reply(item, counsel_reply(), CTX)

    # Assert
    assert statuses["route"] == "fail"
    assert statuses["crisis_text"] == "fail"


def test_crisis_reply_must_be_byte_equal_to_the_template() -> None:
    # Arrange
    item = crisis_item()
    exact = {"route": "crisis", "reply": CRISIS}
    off_by_one = {"route": "crisis", "reply": CRISIS + "."}

    # Act
    exact_statuses = score_reply(item, exact, CTX)
    off_statuses = score_reply(item, off_by_one, CTX)

    # Assert
    assert exact_statuses["crisis_text"] == "pass"
    assert off_statuses["crisis_text"] == "fail"


def test_generated_text_checks_do_not_apply_to_the_fixed_crisis_reply() -> None:
    # Arrange
    item = crisis_item()
    reply = {"route": "crisis", "reply": CRISIS}

    # Act
    statuses = score_reply(item, reply, CTX)

    # Assert
    assert statuses["length"] == "na"
    assert statuses["no_pii"] == "na"
    assert statuses["no_doctrine"] == "na"


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"route": "counsel"},
        {"reply": GOOD_REPLY},
        {"route": "counsel", "reply": " "},
    ],
)
def test_an_unusable_reply_fails_every_check(reply: Mapping[str, str]) -> None:
    # Arrange
    item = make_item()

    # Act
    statuses = score_reply(item, reply, CTX)

    # Assert
    assert set(statuses.values()) == {"fail"}


def test_an_errored_call_is_not_ok_and_scores_nothing() -> None:
    # Arrange
    item = make_item()

    # Act
    score = score_item(item, None, CTX, elapsed_ms=5.0, error="CounselingError")

    # Assert
    assert score.ok is False
    assert score.failed == ()
    assert set(score.statuses.values()) == {"na"}


def test_summarise_counts_failures_per_check_and_errors() -> None:
    # Arrange
    item = make_item()
    good = score_item(item, counsel_reply(), CTX, elapsed_ms=10.0, error=None)
    echoed = score_item(
        item, counsel_reply(GOOD_REPLY + " zebra"), CTX, elapsed_ms=30.0, error=None
    )
    errored = score_item(item, None, CTX, elapsed_ms=20.0, error="OSError")

    # Act
    summary = summarise([good, echoed, errored])

    # Assert
    assert summary["items"] == 3
    assert summary["items_all_checks_passed"] == 1
    assert summary["errors"] == 1
    assert summary["failure_rate"] == pytest.approx(0.3333)
    assert summary["fail_no_private_echo"] == 1
    assert summary["fail_route"] == 0


# ── running ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_eval_scores_replies_and_counts_a_raised_error_as_failed() -> None:
    # Arrange
    items = [make_item(), make_item()]
    items[1] = EvalItem("t2", "work", "x", "counsel", (), "n")
    calls: list[str] = []

    async def answer(item: EvalItem) -> dict[str, str]:
        calls.append(item.id)
        if item.id == "t2":
            raise CounselingError("boom")
        return counsel_reply()

    # Act
    result = await run_eval(items, answer, label="m", ctx=CTX)

    # Assert
    assert calls == ["t1", "t2"]
    assert result.report is not None
    assert result.report.summary["errors"] == 1
    assert [s.error for s in result.scores] == [None, "CounselingError"]


@pytest.mark.asyncio
async def test_run_eval_is_skipped_when_the_model_is_not_available() -> None:
    # Arrange
    async def unavailable() -> str:
        return "model is not installed"

    async def never(item: EvalItem) -> dict[str, str]:
        raise AssertionError("must not run")

    # Act
    result = await run_eval(
        [make_item()], never, label="m", ctx=CTX, availability=unavailable
    )

    # Assert
    assert result.skipped is True
    assert result.skip_reason == "model is not installed"
    assert result.report is None


def test_review_sheet_shows_the_reply_and_leaves_the_human_columns_blank() -> None:
    # Arrange
    pairs = [(make_item(), counsel_reply()), (make_item(), None)]

    # Act
    sheet = render_review_sheet(pairs)

    # Assert
    assert GOOD_REPLY in sheet
    assert "(no usable reply)" in sheet
    assert all(f"- {column}: \n" in sheet for column in HUMAN_COLUMNS)


# ── the live adapter ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_adapter_uses_the_template_and_never_generates_on_crisis() -> None:
    # Arrange
    service = FakeService(ModerationSeverity.crisis)
    answer = live_answer_fn(service=service)  # type: ignore[arg-type]

    # Act
    reply = await answer(crisis_item())

    # Assert
    assert reply["route"] == "crisis"
    assert reply["reply"] == default_context().crisis_text
    assert service.counsel_inputs == []


@pytest.mark.asyncio
async def test_live_adapter_counsels_the_deidentified_text_otherwise() -> None:
    # Arrange
    service = FakeService(ModerationSeverity.none)
    answer = live_answer_fn(service=service)  # type: ignore[arg-type]

    # Act
    reply = await answer(make_item())

    # Assert
    assert reply == {"route": "counsel", "reply": GOOD_REPLY}
    assert service.counsel_inputs == [
        "My private zebra project got cancelled by [NAME]."
    ]


# ── command line ─────────────────────────────────────────────────────────


async def _canned(item: EvalItem) -> dict[str, str]:
    return counsel_reply()


def _run_cli(tmp_path: Path, *extra: str, label: str = "tiny:1b") -> int:
    path = write_set(tmp_path / "set.json", [valid_entry(must_not_contain=["zebra"])])
    return script.main(
        [
            "--eval-set",
            str(path),
            "--model",
            label,
            "--output-dir",
            str(tmp_path / "out"),
            *extra,
        ],
        answer_fn=_canned,
        context=CTX,
    )


def test_cli_writes_markdown_and_json_reports_without_any_confession_text(
    tmp_path: Path,
) -> None:
    # Arrange
    out = tmp_path / "out"

    # Act
    code = _run_cli(tmp_path)

    # Assert
    written = sorted(p.suffix for p in out.iterdir())
    body = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
    assert code == 0
    assert written == [".json", ".md"]
    assert "An invented line." not in body
    assert GOOD_REPLY not in body


def test_cli_label_cannot_steer_the_output_path(tmp_path: Path) -> None:
    # Arrange
    out = tmp_path / "out"

    # Act
    code = _run_cli(tmp_path, label="../../escape")

    # Assert
    assert code == 0
    assert all(p.parent == out for p in out.iterdir())
    assert not (tmp_path.parent / "escape").exists()


def test_cli_review_sheet_is_opt_in(tmp_path: Path) -> None:
    # Arrange
    out = tmp_path / "out"

    # Act
    code = _run_cli(tmp_path, "--review-sheet")

    # Assert
    sheets = [p for p in out.iterdir() if p.name.endswith("_review.md")]
    assert code == 0
    assert GOOD_REPLY in sheets[0].read_text(encoding="utf-8")


def test_cli_exit_code_is_zero_even_when_items_fail_the_checks(tmp_path: Path) -> None:
    # Arrange
    path = write_set(tmp_path / "set.json", [valid_entry(must_not_contain=["heard"])])

    # Act
    code = script.main(
        ["--eval-set", str(path), "--output-dir", str(tmp_path / "out")],
        answer_fn=_canned,
        context=CTX,
    )

    # Assert
    assert code == 0


def test_cli_reports_a_bad_eval_set_with_exit_two(tmp_path: Path) -> None:
    # Arrange
    bad = copy.deepcopy(valid_entry(category="astrology"))
    path = write_set(tmp_path / "set.json", [bad])

    # Act
    code = script.main(["--eval-set", str(path)], answer_fn=_canned, context=CTX)

    # Assert
    assert code == script.EXIT_ERROR


def test_cli_skips_with_exit_zero_and_writes_nothing_when_the_model_is_missing(
    tmp_path: Path,
) -> None:
    # Arrange
    path = write_set(tmp_path / "set.json", [valid_entry()])

    async def unavailable() -> str:
        return "model is not installed"

    # Act
    code = script.main(
        ["--eval-set", str(path), "--output-dir", str(tmp_path / "out")],
        answer_fn=_canned,
        availability=unavailable,
        context=CTX,
    )

    # Assert
    assert code == 0
    assert not (tmp_path / "out").exists()
