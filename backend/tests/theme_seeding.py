"""Shared helpers for the themes API tests.

Only the model boundary is faked; everything else in these tests is real.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.api.v1 import hr_themes
from app.main import app
from app.models.confession import Confession, ConfessionStatus
from app.services import settings_service
from app.services.llm import LLMService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SettingPatcher

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
PATH = "/api/v1/hr/themes"
MIN_COHORT = 3
PRIVATE = "PRIVATE-TRANSCRIPT-NEVER-SENT"


class FakeModel:
    """Stands in for the local model: groups summaries by a keyword they contain."""

    def __init__(self, reply: Callable[[str], str] | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._reply = reply or self._group_by_keyword

    def _complete(self, service: LLMService, instruction: str, content: str) -> str:
        self.calls.append((service._provider, instruction, content))
        return self._reply(content)

    def patch(self):
        """Patch ``LLMService.complete`` with a real function so it binds ``self``."""

        def complete(service: LLMService, instruction: str, content: str) -> str:
            return self._complete(service, instruction, content)

        return patch("app.services.llm.LLMService.complete", new=complete)

    @staticmethod
    def _keyword_in(text: str) -> str | None:
        return next((k for k in ("pay", "manager", "office") if k in text), None)

    @classmethod
    def _group_by_keyword(cls, content: str) -> str:
        buckets: dict[str, list[int]] = {}
        for line in content.split("\n"):
            number, _, text = line.partition(". ")
            keyword = cls._keyword_in(text)
            if keyword is not None:
                buckets.setdefault(keyword, []).append(int(number))
        themes = [{"label": f"Theme {k}", "items": v} for k, v in buckets.items()]
        return json.dumps({"themes": themes})


@pytest.fixture(autouse=True)
def pinned_clock_and_cohort(set_setting: SettingPatcher) -> Iterator[None]:
    """Freeze 'now' and pin the suppression threshold for every test here."""
    set_setting("ANALYTICS_MIN_COHORT", MIN_COHORT)
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)
    app.dependency_overrides[hr_themes.get_clock] = lambda: lambda: NOW
    yield
    app.dependency_overrides.pop(hr_themes.get_clock, None)


async def add_confessions(
    session: AsyncSession,
    count: int,
    keyword: str,
    sentiment: str | None = "neutral",
    category: str = "work",
    days_ago: float = 1,
    status: ConfessionStatus = ConfessionStatus.pending,
) -> None:
    for index in range(count):
        session.add(
            Confession(
                device_token_hash=f"{keyword}{index:027d}",
                voice_mask="warm",
                transcript=PRIVATE,
                ai_summary=f"concern about {keyword} number {index}",
                category=category,
                sentiment=sentiment,
                pii_stripped=True,
                status=status,
                created_at=NOW - timedelta(days=days_ago),
            )
        )
    await session.commit()


async def get_themes(client: AsyncClient, headers: dict[str, str], days: int = 7):
    return await client.get(f"{PATH}?days={days}", headers=headers)
