"""Tests for priest-mode usage metrics.

Counts and latencies only. Labels come from fixed sets, so nothing a user typed, no
tradition and no device can ever become a label, and cardinality cannot grow.
"""

from __future__ import annotations

from datetime import timezone

import pytest
from app.priest import metrics
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True)
def _fresh() -> None:
    metrics.reset()


def _sample(outcome: str) -> float:
    return (
        REGISTRY.get_sample_value("auri_priest_answers_total", {"outcome": outcome})
        or 0.0
    )


def test_an_outcome_is_counted_in_prometheus_and_in_the_snapshot() -> None:
    # Arrange
    before = _sample("answer")

    # Act
    metrics.record_outcome("answer")
    metrics.record_outcome("answer")
    metrics.record_outcome("crisis")

    # Assert
    assert _sample("answer") - before == 2
    snap = metrics.snapshot()
    assert snap.outcomes["answer"] == 2 and snap.outcomes["crisis"] == 1
    assert snap.since.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "hostile", ["What is my name?", "buddhism", "device-abc", "", "answer; DROP"]
)
def test_an_unknown_outcome_collapses_to_other(hostile: str) -> None:
    # Act
    metrics.record_outcome(hostile)

    # Assert — nothing a user typed can become a label
    assert metrics.snapshot().outcomes.get("other") == 1
    assert (
        REGISTRY.get_sample_value("auri_priest_answers_total", {"outcome": hostile})
        is None
    )


def test_every_known_outcome_is_accepted() -> None:
    # Act
    for kind in metrics.OUTCOMES:
        metrics.record_outcome(kind)

    # Assert
    assert all(metrics.snapshot().outcomes[k] == 1 for k in metrics.OUTCOMES)
    assert {"answer", "not_covered", "crisis", "deferral", "library_excerpts"} <= set(
        metrics.OUTCOMES
    )
    assert {"error", "busy", "rate_limited", "disabled"} <= set(metrics.OUTCOMES)


def test_latency_percentiles_come_from_the_recorded_values() -> None:
    # Arrange
    for seconds in range(1, 101):
        metrics.record_latency("generate", float(seconds))

    # Act
    snap = metrics.snapshot()

    # Assert
    assert snap.latency_p50["generate"] == pytest.approx(50.5, abs=1.0)
    assert snap.latency_p95["generate"] == pytest.approx(95.05, abs=1.0)


def test_an_unknown_stage_collapses_to_other_and_bad_values_are_ignored() -> None:
    # Act
    metrics.record_latency("a user's question", 1.0)
    metrics.record_latency("total", -1.0)
    metrics.record_latency("total", float("nan"))

    # Assert
    snap = metrics.snapshot()
    assert "other" in snap.latency_p50 and "total" not in snap.latency_p50


def test_the_latency_window_is_bounded() -> None:
    # Act
    for i in range(metrics.LATENCY_WINDOW * 3):
        metrics.record_latency("total", float(i))

    # Assert — only the most recent window is kept, so memory cannot grow
    assert metrics.snapshot().latency_p50["total"] >= metrics.LATENCY_WINDOW * 2


def test_the_snapshot_is_a_copy() -> None:
    # Arrange
    metrics.record_outcome("answer")
    snap = metrics.snapshot()

    # Act
    metrics.record_outcome("answer")

    # Assert
    assert snap.outcomes["answer"] == 1


def test_crisis_and_deferral_replies_share_one_prometheus_label() -> None:
    # Arrange — an exact crisis count, scraped with timestamps and joined to the access
    # log, would say who sent one
    from prometheus_client import REGISTRY

    def sample(label: str) -> float:
        return (
            REGISTRY.get_sample_value("auri_priest_answers_total", {"outcome": label})
            or 0.0
        )

    before = sample("fixed_reply")

    # Act
    metrics.record_outcome("crisis")
    metrics.record_outcome("deferral")

    # Assert
    assert sample("fixed_reply") == before + 2
    assert (
        REGISTRY.get_sample_value("auri_priest_answers_total", {"outcome": "crisis"})
        is None
    )
    assert (
        REGISTRY.get_sample_value("auri_priest_answers_total", {"outcome": "deferral"})
        is None
    )


def test_the_in_process_snapshot_still_counts_crisis_separately() -> None:
    # Arrange
    metrics.reset()

    # Act
    metrics.record_outcome("crisis")

    # Assert — the admin view applies its own small-count suppression to this
    assert metrics.snapshot().outcomes["crisis"] == 1
