"""What the organisation can truthfully tell an employee about their data.

The Privacy panel is what HR shows someone who asks "how do I know this is
really anonymous?". A page like that is only worth showing if it never says
more than the system does. So every statement is built from a snapshot of the
*live* configuration (the cohort size, the retention windows, which outside AI
providers hold a key, where the local model really is) rather than typed once
and left to go stale, and the things the system does not protect against are
listed as plainly as the things it does.

Every sentence here was checked against the code that makes it true. When the
code changes, the sentence must change with it: the tests pin the wording to
the configuration, and the review that produced this module found six
statements that had overclaimed.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.retention_run import RetentionRun
from app.models.user import User, UserRole
from app.services import insights_service, retention, retention_status, theme_service
from app.services.confession_access import MIN_JUSTIFICATION_LENGTH
from app.services.insights_service import Bucket
from app.services.settings_service import get_config

DEPARTMENT_TRANSCRIPT_CHARS = 1000
MODERATOR_TRANSCRIPT_CHARS = 500
_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal", "ollama"})
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)


@dataclass(frozen=True)
class Fact:
    """One plain-language statement about how data is handled."""

    id: str
    statement: str


@dataclass(frozen=True)
class PrivacySnapshot:
    """The live configuration the statements are built from."""

    min_cohort: int
    retention_hours: int
    reply_retention_days: int
    expected_run_hours: int
    themes_stay_local: bool
    model_host: str
    outside_ai: tuple[str, ...]
    openai_speech_fallback: bool
    error_tracking: bool
    sql_echo: bool


@dataclass(frozen=True)
class RunSummary:
    """One retention run, its counts already suppressed."""

    ran_at: datetime
    retention_hours: int
    reply_retention_days: int
    deleted: Bucket
    emptied_to_shell: Bucket
    expired_replies: Bucket


@dataclass(frozen=True)
class RetentionOverview:
    """The retention promise, and evidence the job is keeping it."""

    retention_hours: int
    reply_retention_days: int
    expected_run_hours: int
    last_run: RunSummary | None
    overdue: bool
    due_to_delete: Bucket
    due_to_empty: Bucket
    due_to_expire: Bucket


@dataclass(frozen=True)
class StaffMember:
    """A staff account as the Privacy panel shows it to an administrator."""

    email: str
    role: UserRole
    last_login_at: datetime | None


@dataclass(frozen=True)
class StaffOverview:
    """Who can sign in, by role, and (for administrators only) by name."""

    role_counts: dict[str, int]
    members: list[StaffMember] | None


@dataclass(frozen=True)
class PrivacyOverview:
    """Everything the Privacy panel renders."""

    min_cohort: int
    guarantees: list[Fact]
    limits: list[Fact]
    retention: RetentionOverview
    staff: StaffOverview


def _configured(name: str) -> str:
    """Read a setting the way the LLM code does: the live DB override first."""
    return get_config(name, getattr(settings, name))


def _host_is_local(host: str) -> bool:
    """Whether *host* is this machine or an address on a private network.

    Only loopback, RFC 1918, link-local and unique-local ranges count.
    ``ipaddress``'s own ``is_private`` is wider (it includes documentation and
    reserved ranges) and would call a public-looking address private.
    """
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _LOCAL_NETWORKS)


def model_host() -> str:
    """The host the local model is reached at, from the live configuration."""
    return urlparse(_configured("OLLAMA_BASE_URL")).hostname or ""


def themes_stay_local() -> bool:
    """Whether theme grouping runs on this organisation's own infrastructure.

    True only if the provider used is Ollama, its address is loopback or
    private, and the model is not an Ollama cloud model (those are named
    ``…-cloud`` and are run by a third party). An administrator can change the
    address and model from the Config tab, so this is read live.
    """
    if theme_service.THEMES_PROVIDER != "ollama":
        return False
    cloud_model = _configured("OLLAMA_MODEL").endswith("-cloud")
    return _host_is_local(model_host()) and not cloud_model


def outside_ai_providers() -> tuple[str, ...]:
    """Names of the third-party AI providers that hold a key on this server.

    De-identification, the safety check, the summary, category, mood label and
    the confessor's reply all use the ``auto`` provider chain: local Ollama
    first, then Gemini, then OpenAI. A key being present is what makes the
    fallback possible. Read live, because keys can be set in the Config tab.
    """
    named = (("Google Gemini", "GEMINI_API_KEY"), ("OpenAI", "OPENAI_API_KEY"))
    return tuple(label for label, key in named if _configured(key))


def build_snapshot() -> PrivacySnapshot:
    """Capture the live configuration the panel's statements depend on."""
    return PrivacySnapshot(
        min_cohort=insights_service.min_cohort(),
        retention_hours=settings.RETENTION_HOURS,
        reply_retention_days=settings.REPLY_RETENTION_DAYS,
        expected_run_hours=settings.RETENTION_EXPECTED_RUN_HOURS,
        themes_stay_local=themes_stay_local(),
        model_host=model_host(),
        outside_ai=outside_ai_providers(),
        openai_speech_fallback=bool(_configured("OPENAI_API_KEY")),
        error_tracking=bool(settings.SENTRY_DSN),
        sql_echo=settings.SQL_ECHO,
    )


def _themes_statement(snapshot: PrivacySnapshot) -> str:
    """Say where theme grouping runs, from where it actually runs."""
    where = (
        f"a model on this organisation's own infrastructure ({snapshot.model_host})"
        if snapshot.themes_stay_local
        else f"an AI service outside this organisation ({snapshot.model_host or 'unknown'})"
    )
    return (
        f"Recurring themes are grouped by {where}, from de-identified summaries "
        "only, never original transcripts."
    )


def build_guarantees(snapshot: PrivacySnapshot) -> list[Fact]:
    """State what the system guarantees, using the live configuration.

    Args:
        snapshot: The live configuration.

    Returns:
        The guarantees, in reading order. Each is narrower than it would be
        tempting to write; the wider claims are in the limits.
    """
    return [
        Fact(
            "cleanup",
            "The transcript is saved after an automatic clean-up that replaces "
            "the names, email addresses, phone numbers and similar details it "
            "recognises. The rest of the wording is kept as it was said. The "
            "version from before the clean-up is not saved in the database.",
        ),
        Fact(
            "no_link",
            "No staff account is linked to who submitted a confession, and no "
            "HR or moderator screen shows the identifier of the phone. The "
            "phone is known only by a one-way code, not by name.",
        ),
        Fact(
            "summary_first",
            "Most HR screens show a summary, a category and a mood label. HR "
            "can open the full transcript of an item held for review (crisis "
            "items stay open after release) only with a written reason of at "
            f"least {MIN_JUSTIFICATION_LENGTH} characters, which is logged.",
        ),
        Fact(
            "small_groups",
            f"On the Insights and Themes charts, a number covering fewer than "
            f"{snapshot.min_cohort} confessions is hidden, and so is the name "
            "of any theme that small.",
        ),
        Fact(
            "audited",
            "Each time a signed-in staff member opens confession content in "
            "the dashboard, their account, the time and whether they saw the "
            "summary or the full text are recorded in an audit trail that "
            "administrators can read and the app cannot change.",
        ),
        Fact(
            "retention",
            "A confession that was forwarded or withdrawn is removed at the "
            f"first clean-up run after it has been unchanged for "
            f"{snapshot.retention_hours} hours.",
        ),
        Fact("themes", _themes_statement(snapshot)),
    ]


def _ai_limits(snapshot: PrivacySnapshot) -> list[Fact]:
    """Limits about the AI services and the recordings."""
    if snapshot.outside_ai:
        fallback = (
            f"A key for {' and '.join(snapshot.outside_ai)} is configured on "
            "this server, so that fallback can happen."
        )
    else:
        fallback = "No outside provider has a key on this server, so this stays here."
    speech = (
        "If the speech model on this server fails or hears nothing, the "
        "recording itself is sent to OpenAI to be transcribed."
        if snapshot.openai_speech_fallback
        else "No outside speech service is configured, so recordings are "
        "transcribed on this server."
    )
    return [
        Fact(
            "raw_words_read",
            "Every step that reads a confession (cleaning it, the safety check, "
            "the summary, the category, the mood label and the reply the "
            "confessor sees) uses the model on this server first and may fall "
            "back to an outside AI provider. Cleaning and the safety check read "
            f"the words before they are cleaned. {fallback}",
        ),
        Fact("speech", speech),
        Fact(
            "phone_copy",
            "The app on the confessor's phone keeps a copy of the voice-masked "
            "recording in its cache, and anyone who can open the app can see "
            "the confession history and HR's replies.",
        ),
    ]


def _people_limits(snapshot: PrivacySnapshot) -> list[Fact]:
    """Limits about who can see content, and where copies live."""
    return [
        Fact(
            "queue_transcripts",
            "Moderators and HR see the full transcript of every item held for "
            "review on the Queue tab, without giving a reason.",
        ),
        Fact(
            "telegram",
            "When a confession is forwarded, its category, summary and the "
            f"first {DEPARTMENT_TRANSCRIPT_CHARS:,} characters of the "
            "transcript are posted to that department's Telegram chat, and "
            f"items held for review appear in the moderators' chat "
            f"({MODERATOR_TRANSCRIPT_CHARS} characters). Telegram keeps "
            "messages after this system deletes the confession, and neither "
            "moderation from Telegram nor who reads those chats is recorded.",
        ),
        Fact(
            "exact_time",
            "HR sees the exact time each confession was sent, and lists show "
            "each one with its department. In a small team, that alone can "
            "point to someone.",
        ),
        Fact(
            "db_access",
            "Anyone with direct access to the database can read every stored "
            "transcript without being recorded in the audit trail, can see "
            "which confessions came from the same phone (though not whose), "
            "and could use that phone's code to read, forward or withdraw "
            "them. Database backups are outside this system's control.",
        ),
        Fact(
            "audit_kept",
            "The audit trail keeps the confession numbers and written reasons "
            "it records, with no expiry.",
        ),
        Fact(
            "content_identifies",
            "What someone says can point to them by itself: "
            "a rare event, a named place. The clean-up is automatic and can miss things.",
        ),
    ]


def _keeping_limits(snapshot: PrivacySnapshot) -> list[Fact]:
    """Limits about how long things are kept, and what the logs hold."""
    logs = (
        "The server's access log records the address and the confession "
        "number of every request."
    )
    if snapshot.sql_echo:
        logs += (
            " SQL logging is switched on, so database statements, including "
            "confession text, are being logged too."
        )
    if snapshot.error_tracking:
        logs += (
            " An error-tracking service is configured; failed requests are "
            "reported to it without what was submitted."
        )
    return [
        Fact(
            "not_removed",
            "A confession that is never forwarded, or that is held for review, "
            "is kept until its author or a moderator acts on it. Only "
            "forwarded and withdrawn ones are removed on a schedule.",
        ),
        Fact(
            "reply_outlives",
            "When HR replies, the reply and the one-way phone code needed to "
            f"show it are kept for up to {snapshot.reply_retention_days} days "
            "after the reply, even though the confession itself goes after "
            f"{snapshot.retention_hours} hours. If HR replies to a confession "
            "that has not been forwarded, both are kept until it is forwarded "
            "or withdrawn.",
        ),
        Fact(
            "job_dependent",
            "Removal is done by a scheduled job outside the app, expected to "
            f"run at least every {snapshot.expected_run_hours} hours, so a "
            f"confession can stay up to {snapshot.retention_hours + snapshot.expected_run_hours} "
            "hours. If the job stops, nothing else removes anything; this page "
            "shows when it last ran and warns when it is overdue.",
        ),
        Fact("logs", logs),
    ]


def build_limits(snapshot: PrivacySnapshot) -> list[Fact]:
    """State what the system does *not* protect against, as plainly as the rest.

    Args:
        snapshot: The live configuration.

    Returns:
        The limits, in reading order.
    """
    return [
        *_ai_limits(snapshot),
        *_people_limits(snapshot),
        *_keeping_limits(snapshot),
    ]


async def _role_counts(session: AsyncSession) -> dict[str, int]:
    """Count active staff accounts by role; every role appears, possibly as 0."""
    stmt = (
        select(User.role, func.count())
        .where(User.is_active.is_(True))
        .group_by(User.role)
    )
    found = {role: count for role, count in (await session.execute(stmt)).all()}
    return {role.value: found.get(role, 0) for role in UserRole}


async def _members(session: AsyncSession) -> list[StaffMember]:
    """List active staff by name; only selected columns, never the password hash."""
    stmt = (
        select(User.email, User.role, User.last_login_at)
        .where(User.is_active.is_(True))
        .order_by(User.email)
    )
    return [
        StaffMember(email=row.email, role=row.role, last_login_at=row.last_login_at)
        for row in (await session.execute(stmt)).all()
    ]


def _summarise_run(run: RetentionRun, threshold: int) -> RunSummary:
    """Suppress a run's counts by the same rule as every other figure."""
    suppress = insights_service.suppress_small_cohort
    return RunSummary(
        ran_at=run.ran_at,
        retention_hours=run.retention_hours,
        reply_retention_days=run.reply_retention_days,
        deleted=suppress("deleted", run.deleted, threshold),
        emptied_to_shell=suppress("emptied_to_shell", run.emptied_to_shell, threshold),
        expired_replies=suppress("expired_replies", run.expired_replies, threshold),
    )


async def build_retention_overview(
    session: AsyncSession, now: datetime, threshold: int
) -> RetentionOverview:
    """Assemble the retention promise, the last run, and what is due next."""
    hours, days = settings.RETENTION_HOURS, settings.REPLY_RETENTION_DAYS
    gap = settings.RETENTION_EXPECTED_RUN_HOURS
    last = await retention_status.latest_run(session)
    due = await retention.count_due(session, now, hours, days)
    suppress = insights_service.suppress_small_cohort
    return RetentionOverview(
        retention_hours=hours,
        reply_retention_days=days,
        expected_run_hours=gap,
        last_run=_summarise_run(last, threshold) if last else None,
        overdue=retention_status.is_overdue(last, now, gap),
        due_to_delete=suppress("due_to_delete", due.to_delete, threshold),
        due_to_empty=suppress("due_to_empty", due.to_empty, threshold),
        due_to_expire=suppress("due_to_expire", due.to_expire, threshold),
    )


async def build_overview(
    session: AsyncSession, now: datetime, include_members: bool
) -> PrivacyOverview:
    """Assemble the Privacy panel's data.

    Args:
        session: Active database session.
        now: Current time, injected.
        include_members: Whether to include named staff. Only administrators
            get this; HR sees how many accounts hold each role, not who.

    Returns:
        The overview, with every figure that could describe a small group
        already suppressed.
    """
    snapshot = build_snapshot()
    return PrivacyOverview(
        min_cohort=snapshot.min_cohort,
        guarantees=build_guarantees(snapshot),
        limits=build_limits(snapshot),
        retention=await build_retention_overview(session, now, snapshot.min_cohort),
        staff=StaffOverview(
            role_counts=await _role_counts(session),
            members=await _members(session) if include_members else None,
        ),
    )
