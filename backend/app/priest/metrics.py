"""Usage metrics for priest mode: counts and latencies, never text.

Prometheus counters and a histogram for the scrape endpoint, plus an in-process
snapshot for the admin usage view. Every label comes from a fixed set and anything
else becomes ``other``, so a question, a tradition or a device can never become a
label and cardinality cannot grow. Nothing here stores a question or an answer.
"""

from __future__ import annotations

import math
import threading
from collections import Counter as CountMap
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from prometheus_client import Counter, Histogram

OUTCOMES: Final = (
    "answer",
    "not_covered",
    "crisis",
    "deferral",
    "library_excerpts",
    "error",
    "busy",
    "rate_limited",
    "disabled",
)
STAGES: Final = ("safety", "embed", "retrieve", "generate", "validate", "total")
OTHER: Final = "other"
LATENCY_WINDOW: Final = 1000

ANSWERS = Counter(
    "auri_priest_answers_total", "Priest-mode questions by outcome", ["outcome"]
)
LATENCY = Histogram(
    "auri_priest_latency_seconds", "Priest-mode latency by stage", ["stage"]
)


@dataclass(frozen=True)
class MetricsSnapshot:
    """A copy of the counts and latency percentiles since the process started."""

    since: datetime
    outcomes: dict[str, int]
    latency_p50: dict[str, float]
    latency_p95: dict[str, float]


_lock = threading.Lock()
_outcomes: CountMap[str] = CountMap()
_latencies: dict[str, deque[float]] = {}
_since = datetime.now(timezone.utc)


def reset() -> None:
    """Forget the in-process counts (tests). Prometheus counters keep counting."""
    global _since
    with _lock:
        _outcomes.clear()
        _latencies.clear()
        _since = datetime.now(timezone.utc)


def record_outcome(kind: str) -> None:
    """Count one answered (or refused) question under a fixed outcome label."""
    label = kind if kind in OUTCOMES else OTHER
    ANSWERS.labels(outcome=label).inc()
    with _lock:
        _outcomes[label] += 1


def record_latency(stage: str, seconds: float) -> None:
    """Record how long one stage took; negative or non-finite values are ignored."""
    if not math.isfinite(seconds) or seconds < 0:
        return
    label = stage if stage in STAGES else OTHER
    LATENCY.labels(stage=label).observe(seconds)
    with _lock:
        _latencies.setdefault(label, deque(maxlen=LATENCY_WINDOW)).append(seconds)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def snapshot() -> MetricsSnapshot:
    """Return a copy of the current counts and p50/p95 latencies."""
    with _lock:
        return MetricsSnapshot(
            since=_since,
            outcomes=dict(_outcomes),
            latency_p50={
                k: _percentile(list(v), 0.5) for k, v in _latencies.items() if v
            },
            latency_p95={
                k: _percentile(list(v), 0.95) for k, v in _latencies.items() if v
            },
        )
