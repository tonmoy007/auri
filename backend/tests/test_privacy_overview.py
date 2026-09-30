"""Tests for the Privacy panel API.

The panel is what HR shows an employee asking how anonymity works, so the
properties tested are: it never says more than the configuration does, it shows
the limits as well as the guarantees, HR never sees staff by name, and a run
history that says the deletion job is (not) keeping its promise.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from app.api.v1 import privacy
from app.main import app
from app.models.confession import ConfessionStatus
from app.models.retention_run import RetentionRun
from app.models.user import UserRole
from app.services import retention, settings_service
from app.services.retention import RetentionResult
from app.services.retention_status import is_overdue, latest_run, record_run
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.confession_seeding import add_confession
from tests.conftest import SettingPatcher, StaffFactory

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
PATH = "/api/v1/privacy/overview"
MIN_COHORT = 3


@pytest.fixture(autouse=True)
def pinned_clock_and_cohort(
    set_setting: SettingPatcher, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Freeze 'now', pin the threshold, and blank anything a developer's .env sets.

    The statements depend on which provider keys, error tracking and SQL
    logging are configured, so the baseline must not come from the machine.
    """
    set_setting("ANALYTICS_MIN_COHORT", MIN_COHORT)
    for name, value in (
        ("GEMINI_API_KEY", ""),
        ("OPENAI_API_KEY", ""),
        ("SENTRY_DSN", ""),
        ("SQL_ECHO", False),
        ("THEMES_LLM_BASE_URL", ""),
        ("THEMES_LLM_MODEL", ""),
        ("THEMES_LLM_API_KEY", ""),
        ("THEMES_LLM_ALLOW_INSECURE_HTTP", False),
        ("THEMES_LLM_SELF_HOSTED", False),
    ):
        set_setting(name, value)
    for name in (
        "ANALYTICS_MIN_COHORT",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delitem(settings_service._cache, name, raising=False)
    app.dependency_overrides[privacy.get_clock] = lambda: lambda: NOW
    yield
    app.dependency_overrides.pop(privacy.get_clock, None)


def _statements(facts: list[dict[str, str]]) -> str:
    return " ".join(fact["statement"] for fact in facts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected"),
    [(UserRole.hr, 200), (UserRole.admin, 200), (UserRole.moderator, 403)],
)
async def test_role_matrix(
    role: UserRole, expected: int, api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(role)

    # Act
    response = await api_client.get(PATH, headers=headers)

    # Assert
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_no_session_and_the_legacy_admin_key_are_refused(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ADMIN_API_KEY", "legacy-key-for-tests")

    # Act
    anonymous = await api_client.get(PATH)
    legacy = await api_client.get(
        PATH, headers={"X-Admin-Api-Key": "legacy-key-for-tests"}
    )

    # Assert
    assert (anonymous.status_code, legacy.status_code) == (401, 401)


@pytest.mark.asyncio
async def test_the_panel_is_read_only(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)

    # Act
    statuses = [
        (await api_client.request(method, PATH, headers=headers)).status_code
        for method in ("POST", "PUT", "PATCH", "DELETE")
    ]

    # Assert
    assert statuses == [405, 405, 405, 405]


@pytest.mark.asyncio
async def test_hr_sees_how_many_accounts_hold_each_role_but_not_who(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, hr_headers = await make_staff(UserRole.hr, email="hr-lead@example.test")
    await make_staff(UserRole.hr, email="hr-second@example.test")
    await make_staff(UserRole.moderator, email="mod@example.test")
    await make_staff(UserRole.admin, email="root@example.test")

    # Act
    response = await api_client.get(PATH, headers=hr_headers)

    # Assert
    staff = response.json()["staff"]
    assert staff["role_counts"] == {"admin": 1, "hr": 2, "moderator": 1}
    assert staff["members"] is None
    assert "@example.test" not in response.text


@pytest.mark.asyncio
async def test_an_administrator_sees_named_staff_and_never_a_credential(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, admin_headers = await make_staff(UserRole.admin, email="root@example.test")
    await make_staff(UserRole.hr, email="hr-lead@example.test")

    # Act
    response = await api_client.get(PATH, headers=admin_headers)

    # Assert
    members = response.json()["staff"]["members"]
    assert [(m["email"], m["role"]) for m in members] == [
        ("hr-lead@example.test", "hr"),
        ("root@example.test", "admin"),
    ]
    assert "password" not in response.text
    assert "token_version" not in response.text


@pytest.mark.asyncio
async def test_deactivated_accounts_are_neither_counted_nor_listed(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    _, admin_headers = await make_staff(UserRole.admin, email="root@example.test")
    former, _ = await make_staff(UserRole.hr, email="former@example.test")
    former.is_active = False
    await db_session.commit()

    # Act
    body = (await api_client.get(PATH, headers=admin_headers)).json()

    # Assert
    assert body["staff"]["role_counts"]["hr"] == 0
    assert "former@example.test" not in str(body["staff"]["members"])


async def _overview(client: AsyncClient, headers: dict[str, str]) -> dict:
    return (await client.get(PATH, headers=headers)).json()


def _fact(facts: list[dict[str, str]], fact_id: str) -> str:
    return next(f["statement"] for f in facts if f["id"] == fact_id)


@pytest.mark.asyncio
async def test_the_statements_use_the_live_configuration(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange — distinctive values so a hard-coded sentence cannot pass
    set_setting("RETENTION_HOURS", 37)
    set_setting("REPLY_RETENTION_DAYS", 41)
    set_setting("RETENTION_EXPECTED_RUN_HOURS", 5)
    set_setting("ANALYTICS_MIN_COHORT", 7)
    settings_service._cache.pop("ANALYTICS_MIN_COHORT", None)
    _, headers = await make_staff(UserRole.hr)

    # Act
    body = await _overview(api_client, headers)

    # Assert
    guarantees, limits = _statements(body["guarantees"]), _statements(body["limits"])
    assert "unchanged for 37 hours" in guarantees
    assert "fewer than 7 confessions" in guarantees
    assert "up to 41 days" in limits
    assert "at least every 5 hours" in limits
    assert "up to 42 hours" in limits


@pytest.mark.asyncio
async def test_the_limits_are_listed_alongside_the_guarantees(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    body = await _overview(api_client, headers)

    # Assert
    assert [f["id"] for f in body["guarantees"]] == [
        "cleanup",
        "no_link",
        "summary_first",
        "small_groups",
        "audited",
        "retention",
        "themes",
    ]
    assert {f["id"] for f in body["limits"]} == {
        "raw_words_read",
        "speech",
        "phone_copy",
        "queue_transcripts",
        "telegram",
        "exact_time",
        "db_access",
        "audit_kept",
        "content_identifies",
        "not_removed",
        "reply_outlives",
        "job_dependent",
        "logs",
    }


@pytest.mark.asyncio
async def test_no_guarantee_claims_what_the_review_found_untrue(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange — each phrase below was shown to be false or misleading by the
    # fact-check of this page; none may come back
    _, headers = await make_staff(UserRole.hr)

    # Act
    guarantees = _statements((await _overview(api_client, headers))["guarantees"])

    # Assert
    for overclaim in (
        "deleted as soon as",
        "never written to the database",
        "Only text is ever kept",
        "hidden, including the names of themes, so a small team cannot",
        "Every time staff read",
    ):
        assert overclaim not in guarantees


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gemini", "openai", "expected"),
    [
        ("", "", "No outside provider has a key"),
        ("g-secret-value", "", "A key for Google Gemini is configured"),
        ("", "o-secret-value", "A key for OpenAI is configured"),
        ("g-secret-value", "o-secret-value", "Google Gemini and OpenAI"),
    ],
)
async def test_the_panel_names_the_outside_ai_providers_that_hold_a_key(
    gemini: str,
    openai: str,
    expected: str,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("GEMINI_API_KEY", gemini)
    set_setting("OPENAI_API_KEY", openai)
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(PATH, headers=headers)

    # Assert
    assert expected in _fact(response.json()["limits"], "raw_words_read")
    assert "secret-value" not in response.text


@pytest.mark.asyncio
async def test_a_key_set_in_the_config_tab_counts_as_configured(
    api_client: AsyncClient, make_staff: StaffFactory, monkeypatch
) -> None:
    # Arrange — the LLM code reads the DB override first, so the panel must too
    monkeypatch.setitem(settings_service._cache, "OPENAI_API_KEY", "o-db-secret")
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(PATH, headers=headers)

    # Assert
    limits = response.json()["limits"]
    assert "A key for OpenAI is configured" in _fact(limits, "raw_words_read")
    assert "sent to OpenAI" in _fact(limits, "speech")
    assert "o-db-secret" not in response.text


@pytest.mark.asyncio
async def test_without_an_openai_key_speech_is_said_to_stay_on_this_server(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("OPENAI_API_KEY", "")
    _, headers = await make_staff(UserRole.hr)

    # Act
    body = await _overview(api_client, headers)

    # Assert
    assert "transcribed on this server" in _fact(body["limits"], "speech")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "model", "local"),
    [
        ("http://localhost:11434", "llama3.2:3b", True),
        ("http://10.0.0.5:11434", "llama3.2:3b", True),
        ("http://ollama:11434", "llama3.2:3b", True),
        ("https://ollama.example.net", "llama3.2:3b", False),
        ("http://203.0.113.9:11434", "llama3.2:3b", False),
        ("http://localhost:11434", "gpt-oss:120b-cloud", False),
    ],
    ids=[
        "localhost",
        "private-ip",
        "compose-host",
        "public-host",
        "public-ip",
        "cloud-model",
    ],
)
async def test_the_themes_statement_follows_where_the_model_really_is(
    base_url: str,
    model: str,
    local: bool,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    monkeypatch,
) -> None:
    # Arrange — an administrator can repoint the model from the Config tab
    monkeypatch.setitem(settings_service._cache, "OLLAMA_BASE_URL", base_url)
    monkeypatch.setitem(settings_service._cache, "OLLAMA_MODEL", model)
    _, headers = await make_staff(UserRole.hr)

    # Act
    themes = _fact((await _overview(api_client, headers))["guarantees"], "themes")

    # Assert
    assert ("own infrastructure" in themes) is local
    assert ("outside this organisation" in themes) is not local


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "model", "self_hosted", "phrase", "host"),
    [
        (
            "https://models.example.net",
            "qwen3.5-9b",
            False,
            "where it runs is not verified",
            "models.example.net",
        ),
        (
            "http://10.0.0.7:8000",
            "qwen3.5-9b",
            False,
            "where it runs is not verified",
            "10.0.0.7",
        ),
        (
            "https://localhost:8000",
            "qwen3.5-9b",
            False,
            "where it runs is not verified",
            "localhost",
        ),
        (
            "https://models.example.net",
            "qwen3.5-9b",
            True,
            "own infrastructure",
            "models.example.net",
        ),
        (
            "http://127.0.0.1:18000",
            "qwen3.5-9b",
            True,
            "own infrastructure",
            "127.0.0.1",
        ),
        (
            "http://localhost:11434",
            "gpt-oss:120b-cloud",
            True,
            "where it runs is not verified",
            "localhost",
        ),
    ],
    ids=[
        "public",
        "private",
        "loopback",
        "asserted-self-hosted",
        "tunnel-asserted",
        "cloud-model-overrides",
    ],
)
async def test_the_themes_statement_never_infers_own_infrastructure_from_an_address(
    url: str,
    model: str,
    self_hosted: bool,
    phrase: str,
    host: str,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange — a tunnel to localhost can reach any host, and a private address
    # can sit on a shared network, so only the operator's assertion counts
    set_setting("THEMES_LLM_BASE_URL", url)
    set_setting("THEMES_LLM_MODEL", model)
    set_setting("THEMES_LLM_SELF_HOSTED", self_hosted)
    _, headers = await make_staff(UserRole.hr)

    # Act
    themes = _fact((await _overview(api_client, headers))["guarantees"], "themes")

    # Assert
    assert phrase in themes
    assert host in themes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "unencrypted"),
    [
        ("https://models.example.net", False),
        ("http://127.0.0.1:18000", False),
        ("http://localhost:8000", False),
        ("http://10.0.0.7:8000", True),
        ("http://192.168.1.9:8000", True),
    ],
)
async def test_plain_http_beyond_this_machine_is_disclosed_as_unencrypted(
    url: str,
    unencrypted: bool,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("THEMES_LLM_BASE_URL", url)
    set_setting("THEMES_LLM_MODEL", "qwen3.5-9b")
    _, headers = await make_staff(UserRole.hr)

    # Act
    body = await _overview(api_client, headers)

    # Assert
    ids = {fact["id"] for fact in body["limits"]}
    assert ("themes_unencrypted" in ids) is unencrypted


@pytest.mark.asyncio
async def test_a_refused_endpoint_is_reported_as_sending_nothing(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange — plain http to a public address is refused, so nothing is sent
    set_setting("THEMES_LLM_BASE_URL", "http://118.67.212.45:8000")
    set_setting("THEMES_LLM_MODEL", "qwen3.5-9b")
    set_setting("OPENAI_API_KEY", "sk-secret-value")
    _, headers = await make_staff(UserRole.hr)

    # Act
    response = await api_client.get(PATH, headers=headers)

    # Assert
    themes = _fact(response.json()["guarantees"], "themes")
    assert "cannot be used" in themes
    assert "not sent to any model" in themes
    assert "118.67.212.45" not in response.text
    assert "sk-secret-value" not in response.text


@pytest.mark.asyncio
async def test_the_telegram_limit_states_how_much_text_is_posted(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    telegram = _fact((await _overview(api_client, headers))["limits"], "telegram")

    # Assert
    assert "1,000 characters" in telegram
    assert "500 characters" in telegram
    assert "who reads those chats is not recorded" in telegram


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("echo", "dsn", "echo_said", "tracking_said"),
    [
        (False, "", False, False),
        (True, "", True, False),
        (False, "https://k@o.ingest.sentry.io/1", False, True),
    ],
)
async def test_the_logs_statement_reflects_sql_logging_and_error_tracking(
    echo: bool,
    dsn: str,
    echo_said: bool,
    tracking_said: bool,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("SQL_ECHO", echo)
    set_setting("SENTRY_DSN", dsn)
    _, headers = await make_staff(UserRole.hr)

    # Act
    logs = _fact((await _overview(api_client, headers))["limits"], "logs")

    # Assert
    assert "access log records the address" in logs
    assert ("SQL logging is switched on" in logs) is echo_said
    assert ("error-tracking service" in logs) is tracking_said


@pytest.mark.asyncio
async def test_with_no_recorded_run_the_job_is_reported_overdue(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    retention_block = (await api_client.get(PATH, headers=headers)).json()["retention"]

    # Assert
    assert retention_block["last_run"] is None
    assert retention_block["overdue"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hours_ago", "overdue"),
    [(1, False), (6, False), (7, True)],
    ids=["recent", "exactly-the-expected-gap", "past-the-expected-gap"],
)
async def test_overdue_means_older_than_the_expected_run_gap(
    hours_ago: int,
    overdue: bool,
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange — a gap far shorter than the 24h window, so a job that runs once
    # per window (letting confessions live ~2x as long) does not look healthy
    set_setting("RETENTION_HOURS", 24)
    set_setting("RETENTION_EXPECTED_RUN_HOURS", 6)
    await record_run(
        db_session, NOW - timedelta(hours=hours_ago), RetentionResult(4, 2, 1), 24, 30
    )
    await db_session.commit()
    _, headers = await make_staff(UserRole.hr)

    # Act
    block = (await _overview(api_client, headers))["retention"]

    # Assert
    assert block["overdue"] is overdue
    assert block["expected_run_hours"] == 6


@pytest.mark.asyncio
async def test_last_run_counts_are_suppressed_like_every_other_figure(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — cohort is 3: 4 shown, 2 and 1 withheld
    await record_run(
        db_session, NOW - timedelta(hours=1), RetentionResult(4, 2, 1), 24, 30
    )
    await db_session.commit()
    _, headers = await make_staff(UserRole.hr)

    # Act
    run = (await _overview(api_client, headers))["retention"]["last_run"]

    # Assert
    assert run["deleted"] == {"label": "deleted", "count": 4, "suppressed": False}
    assert run["emptied_to_shell"]["suppressed"] is True
    assert run["emptied_to_shell"]["count"] is None
    assert run["expired_replies"]["suppressed"] is True


@pytest.mark.asyncio
async def test_a_run_records_the_windows_it_enforced(
    db_session: AsyncSession,
) -> None:
    # Arrange
    await record_run(db_session, NOW, RetentionResult(0, 0, 0), 37, 41)
    await db_session.commit()

    # Act
    last = await latest_run(db_session)

    # Assert — values unlike the defaults, so a hard-coded pair cannot pass
    assert last is not None
    assert (last.retention_hours, last.reply_retention_days) == (37, 41)


def test_overdue_handles_a_naive_stored_time_and_an_aware_now() -> None:
    # Arrange — SQLite returns naive datetimes
    naive_run = RetentionRun(
        ran_at=datetime(2026, 9, 30, 5, 0, 0, tzinfo=None),  # noqa: DTZ001 - SQLite returns naive
        retention_hours=24,
        reply_retention_days=30,
        deleted=0,
        emptied_to_shell=0,
        expired_replies=0,
    )

    # Act
    overdue = is_overdue(naive_run, NOW, 6)

    # Assert
    assert overdue is True


@pytest.mark.asyncio
async def test_the_newest_run_is_the_one_reported(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange
    await record_run(
        db_session, NOW - timedelta(hours=30), RetentionResult(9, 9, 9), 24, 30
    )
    await record_run(
        db_session, NOW - timedelta(hours=2), RetentionResult(1, 0, 0), 24, 30
    )
    await db_session.commit()
    _, headers = await make_staff(UserRole.hr)

    # Act
    last_run = (await api_client.get(PATH, headers=headers)).json()["retention"][
        "last_run"
    ]

    # Assert
    assert last_run["deleted"]["count"] is None  # 1 is below the cohort of 3
    assert last_run["deleted"]["suppressed"] is True


async def _stale_forwarded(
    session: AsyncSession, count: int, clock: datetime = NOW
) -> None:
    for index in range(count):
        await add_confession(
            session,
            status=ConfessionStatus.forwarded,
            device_token_hash=f"stale-{index:027d}",
            updated_at=clock - timedelta(hours=48),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "suppressed"),
    [(0, False), (MIN_COHORT - 1, True), (MIN_COHORT, False)],
    ids=["none", "one-below-cohort", "at-cohort"],
)
async def test_the_due_counts_are_suppressed_below_the_cohort(
    count: int,
    suppressed: bool,
    api_client: AsyncClient,
    db_session: AsyncSession,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    await _stale_forwarded(db_session, count)
    _, headers = await make_staff(UserRole.hr)

    # Act
    bucket = (await api_client.get(PATH, headers=headers)).json()["retention"][
        "due_to_delete"
    ]

    # Assert
    assert bucket["suppressed"] is suppressed
    assert bucket["count"] == (None if suppressed else count)


@pytest.mark.asyncio
async def test_count_due_matches_what_a_run_then_does(
    db_session: AsyncSession,
) -> None:
    # Arrange — a mix: two unreplied stale, one replied stale, one replied stale
    # with an expired reply, one fresh
    await _stale_forwarded(db_session, 2)
    await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        device_token_hash="r" * 32,
        updated_at=NOW - timedelta(hours=48),
        hr_reply="we heard you",
        hr_replied_at=NOW - timedelta(hours=40),
    )
    await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        device_token_hash="e" * 32,
        updated_at=NOW - timedelta(days=40),
        hr_reply="old",
        hr_replied_at=NOW - timedelta(days=35),
    )
    await add_confession(db_session, status=ConfessionStatus.forwarded, updated_at=NOW)

    # Act
    due = await retention.count_due(db_session, NOW, 24, 30)
    result = await retention.run_retention(db_session, NOW, 24, 30)

    # Assert
    assert (due.to_delete, due.to_empty, due.to_expire) == (
        result.deleted,
        result.emptied_to_shell,
        result.expired_replies,
    )
    assert (result.deleted, result.emptied_to_shell, result.expired_replies) == (
        2,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_the_retention_command_records_its_own_run_and_windows(
    db_engine: AsyncEngine, db_session: AsyncSession, monkeypatch
) -> None:
    # Arrange — one of each kind of row, so each of the three counts is exercised;
    # the command reads the real clock, so rows are placed relative to it
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.database.async_session_factory", factory)
    clock = datetime.now(timezone.utc)
    await _stale_forwarded(db_session, 2, clock)
    await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        device_token_hash="r" * 32,
        updated_at=clock - timedelta(hours=48),
        hr_reply="we heard you",
        hr_replied_at=clock - timedelta(hours=40),
    )
    await add_confession(
        db_session,
        status=ConfessionStatus.forwarded,
        device_token_hash="e" * 32,
        updated_at=clock - timedelta(days=40),
        hr_reply="old",
        hr_replied_at=clock - timedelta(days=35),
    )

    # Act
    await retention._main()
    db_session.expire_all()

    # Assert
    runs = (await db_session.execute(select(RetentionRun))).scalars().all()
    assert [
        (
            r.deleted,
            r.emptied_to_shell,
            r.expired_replies,
            r.retention_hours,
            r.reply_retention_days,
        )
        for r in runs
    ] == [(2, 1, 1, 24, 30)]


@pytest.mark.asyncio
async def test_latest_run_is_none_before_any_run(db_session: AsyncSession) -> None:
    # Act
    found = await latest_run(db_session)

    # Assert
    assert found is None


@pytest.mark.asyncio
async def test_the_last_run_reports_device_records_removed_under_the_same_rule(
    api_client: AsyncClient, db_session: AsyncSession, make_staff: StaffFactory
) -> None:
    # Arrange — cohort is 3: 5 device records removed is shown, 2 is withheld
    await record_run(
        db_session,
        NOW - timedelta(hours=1),
        RetentionResult(0, 0, 0, expired_devices=5),
        24,
        30,
    )
    await record_run(
        db_session,
        NOW - timedelta(minutes=30),
        RetentionResult(0, 0, 0, expired_devices=2),
        24,
        30,
    )
    await db_session.commit()
    _, headers = await make_staff(UserRole.hr)

    # Act
    run = (await _overview(api_client, headers))["retention"]["last_run"]

    # Assert — the latest run is the one with 2, which is below the cohort
    assert run["expired_devices"]["suppressed"] is True
    assert run["expired_devices"]["count"] is None


@pytest.mark.asyncio
async def test_the_phone_and_device_statements_match_what_the_code_now_does(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.hr)

    # Act
    limits = {
        fact["id"]: fact["statement"]
        for fact in (await _overview(api_client, headers))["limits"]
    }

    # Assert — the phone deletes its copies (and says when it cannot), and the
    # record of when a phone last sent something is cleared by the job
    phone = limits["phone_copy"]
    assert "deletes" in phone
    assert "unmasked" in phone
    assert "keeps a copy" not in phone
    assert "rate limit" in limits["db_access"]


async def _db_access_statement(
    api_client: AsyncClient, make_staff: StaffFactory
) -> str:
    _, headers = await make_staff(UserRole.hr)
    limits = {
        fact["id"]: fact["statement"]
        for fact in (await _overview(api_client, headers))["limits"]
    }
    return limits["db_access"]


@pytest.mark.asyncio
async def test_without_a_secret_the_panel_says_the_phone_code_is_usable_from_the_database(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", "")

    # Act
    statement = await _db_access_statement(api_client, make_staff)

    # Assert
    assert "No server secret is set" in statement
    assert "could be used to read, forward or withdraw" in statement
    assert "keyed hash" not in statement


@pytest.mark.asyncio
async def test_with_a_secret_the_panel_says_the_code_is_stored_hashed(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DEVICE_HASH_PEPPER", "p" * 40)

    # Act
    statement = await _db_access_statement(api_client, make_staff)

    # Assert — it says what still holds, and does not repeat the old warning
    assert "keyed hash" in statement
    assert "server's secret" in statement
    assert "could be used to read, forward or withdraw" not in statement
    assert "p" * 40 not in statement


async def _facts(
    api_client: AsyncClient, make_staff: StaffFactory, group: str
) -> dict[str, str]:
    _, headers = await make_staff(UserRole.hr)
    return {
        f["id"]: f["statement"] for f in (await _overview(api_client, headers))[group]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chars", "expected"),
    [(1000, "first 1,000 characters"), (250, "first 250 characters")],
)
async def test_the_telegram_statement_uses_the_live_transcript_cap(
    chars: int,
    expected: str,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    set_setting("DELIVERY_TRANSCRIPT_CHARS", chars)

    # Act
    statement = (await _facts(api_client, make_staff, "limits"))["telegram"]

    # Assert
    assert expected in statement


@pytest.mark.asyncio
async def test_a_cap_of_zero_is_described_as_summary_only(
    api_client: AsyncClient, make_staff: StaffFactory, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("DELIVERY_TRANSCRIPT_CHARS", 0)

    # Act
    statement = (await _facts(api_client, make_staff, "limits"))["telegram"]

    # Assert
    assert "no transcript" in statement
    assert "first 0" not in statement


@pytest.mark.asyncio
async def test_the_audited_statement_says_what_the_bot_path_does_and_does_not_record(
    api_client: AsyncClient, make_staff: StaffFactory
) -> None:
    # Act
    statement = (await _facts(api_client, make_staff, "guarantees"))["audited"]

    # Assert — decisions from Telegram are recorded as the bot; reading is not
    assert "Telegram bot" in statement
    assert (
        "Reading through the bot, and who reads the Telegram chats, are not recorded"
        in statement
    )
