"""CRUD endpoints for anonymous confessions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import parse_comma_separated_list, settings
from app.database import get_async_session
from app.exceptions import DeidentificationError, RateLimitError
from app.models.confession import Confession, ConfessionStatus
from app.models.user import AnonymousUser
from app.services.llm import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/confessions", tags=["confessions"])

ClockDependency = Callable[[], datetime]


# ── Pydantic request/response schemas ────────────────────────────────────


class ConfessionCreate(BaseModel):
    """Request body for creating a new confession."""

    device_token_hash: str = Field(
        ..., min_length=16, max_length=256, description="SHA-256 hash of device token"
    )
    voice_mask: str = Field(
        "warm",
        pattern=r"^(warm|robotic|ethereal|deep|random)$",
        description="Voice modulation mask identifier",
    )
    transcript: str = Field(
        ..., min_length=1, description="Whisper-generated transcript text"
    )


class ConfessionResponse(BaseModel):
    """Public response representation of a confession."""

    id: uuid.UUID
    voice_mask: str
    transcript: str
    ai_summary: str | None
    category: str | None
    pii_stripped: bool
    status: ConfessionStatus
    recipient_dept: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConfessionForward(BaseModel):
    """Request body for forwarding a confession to a department."""

    department: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Target recipient department",
    )


# ── Dependencies ─────────────────────────────────────────────────────────


def get_clock() -> ClockDependency:
    """FastAPI dependency providing the current-time function.

    Tests can override this dependency (``app.dependency_overrides``) to
    freeze time per AGENTS.md §16.5, instead of calling ``datetime.now()``
    directly inside route handlers.
    """
    return lambda: datetime.now(timezone.utc)


# ── Internal helpers ─────────────────────────────────────────────────────


async def _fetch_confession_or_404(
    session: AsyncSession, confession_id: uuid.UUID
) -> Confession:
    """Fetch a confession by ID or raise ``404``.

    Args:
        session: Active database session.
        confession_id: UUID of the confession to fetch.

    Returns:
        The matching :class:`Confession` row.

    Raises:
        HTTPException: 404 if no confession matches *confession_id*.
    """
    stmt = select(Confession).where(Confession.id == confession_id)
    result = await session.execute(stmt)
    confession = result.scalar_one_or_none()

    if confession is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confession not found",
        )
    return confession


def _verify_ownership(confession: Confession, device_token_hash: str) -> None:
    """Raise ``403`` if *device_token_hash* does not own *confession*.

    Args:
        confession: The confession being accessed.
        device_token_hash: Value of the ``X-Device-Token-Hash`` request header.

    Raises:
        HTTPException: 403 if the hashes do not match.
    """
    if confession.device_token_hash != device_token_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this confession",
        )


def _check_rate_limit(user: AnonymousUser | None, now: datetime) -> None:
    """Raise ``RateLimitError`` if *user* submitted within the rate-limit window.

    Args:
        user: Existing anonymous-user record, or ``None`` for first-time
            devices.
        now: Current time, from the injected clock.

    Raises:
        RateLimitError: If the device's last confession is too recent
            (AGENTS.md §8.5 — 1 confession per configurable window).
    """
    if user is None:
        return

    window = timedelta(seconds=settings.CONFESSION_RATE_LIMIT_SECONDS)
    elapsed = now - user.last_confession_at
    if elapsed < window:
        retry_after = (window - elapsed).seconds
        raise RateLimitError(f"rate limit exceeded; retry in {retry_after}s")


def _safe_categorize(llm_service: LLMService, text: str) -> str | None:
    """Categorize *text*, returning ``None`` on failure instead of blocking creation.

    Categorization is a nice-to-have enrichment — a transient LLM failure
    must never prevent the confession itself from being saved
    (AGENTS.md §15.1 "safe fallback" pattern).
    """
    try:
        return llm_service.categorize(text)
    except Exception as exc:
        logger.warning(
            "confession categorization failed, continuing without it: %s", exc
        )
        return None


def _safe_summarize(llm_service: LLMService, text: str) -> str | None:
    """Summarize *text*, returning ``None`` on failure instead of blocking creation."""
    try:
        return llm_service.summarize(text)
    except Exception as exc:
        logger.warning(
            "confession summarization failed, continuing without it: %s", exc
        )
        return None


def _safe_moderate(llm_service: LLMService, text: str) -> bool:
    """Run the moderation check, failing **closed** (flagged) on error.

    Unlike categorization/summarization, a moderation failure must not
    silently let content through — ``LLMService.moderate`` already fails
    closed internally, but any exception escaping it (network error, etc.)
    is treated the same way here.
    """
    try:
        return llm_service.moderate(text)
    except Exception as exc:
        logger.warning("moderation check failed, flagging for review: %s", exc)
        return True


def _upsert_anonymous_user(
    session: AsyncSession,
    user: AnonymousUser | None,
    device_token_hash: str,
    now: datetime,
) -> None:
    """Create or refresh the anonymous-user usage record for a device.

    Args:
        session: Active database session (mutations flushed by the caller).
        user: Existing record for this device, or ``None`` to create one.
        device_token_hash: SHA-256 hash identifying the device.
        now: Current time, from the injected clock.
    """
    if user is None:
        session.add(
            AnonymousUser(
                device_token_hash=device_token_hash,
                last_confession_at=now,
                confession_count=1,
            )
        )
        return

    user.last_confession_at = now
    user.confession_count += 1


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ConfessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new anonymous confession",
)
async def create_confession(
    body: ConfessionCreate,
    session: AsyncSession = Depends(get_async_session),
    clock: ClockDependency = Depends(get_clock),
) -> Confession:
    """Persist a new confession after de-identifying its transcript.

    The transcript is de-identified (regex pass, then an LLM pass via
    OpenAI) before being stored, and the submitting device is rate-limited
    to one confession per configured window (AGENTS.md §8.5).
    """
    now = clock()

    stmt = select(AnonymousUser).where(
        AnonymousUser.device_token_hash == body.device_token_hash
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    _check_rate_limit(user, now)

    llm_service = LLMService(provider="openai")
    try:
        deidentified_transcript = llm_service.deidentify(body.transcript)
    except Exception as exc:
        logger.error("confession de-identification failed: %s", exc)
        raise DeidentificationError("could not de-identify confession") from exc

    category = _safe_categorize(llm_service, deidentified_transcript)
    ai_summary = _safe_summarize(llm_service, deidentified_transcript)
    # Moderation runs on the ORIGINAL transcript, not the de-identified one:
    # discovered via live testing (2026-07-18) that a de-identify call can
    # itself fail (refusal, meta-commentary) and corrupt its output, which
    # would silently blind the safety check reading it. Moderating raw text
    # instead makes this check's reliability independent of deidentify's.
    is_flagged = _safe_moderate(llm_service, body.transcript)

    confession = Confession(
        device_token_hash=body.device_token_hash,
        voice_mask=body.voice_mask,
        transcript=deidentified_transcript,
        category=category,
        ai_summary=ai_summary,
        pii_stripped=True,
        status=ConfessionStatus.flagged if is_flagged else ConfessionStatus.pending,
    )
    session.add(confession)
    _upsert_anonymous_user(session, user, body.device_token_hash, now)

    await session.flush()
    await session.refresh(confession)
    return confession


@router.get(
    "/{confession_id}",
    response_model=ConfessionResponse,
    summary="Retrieve a confession by its UUID",
)
async def get_confession(
    confession_id: uuid.UUID,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Return a single confession identified by ``confession_id``.

    Requires the ``X-Device-Token-Hash`` header to match the confession's
    owning device.
    """
    confession = await _fetch_confession_or_404(session, confession_id)
    _verify_ownership(confession, x_device_token_hash)
    return confession


@router.delete(
    "/{confession_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a confession",
)
async def delete_confession(
    confession_id: uuid.UUID,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Mark a confession as ``deleted`` without removing the row.

    The original transcript is retained for audit purposes but will not
    appear in any forward-facing query. Requires the ``X-Device-Token-Hash``
    header to match the confession's owning device.
    """
    confession = await _fetch_confession_or_404(session, confession_id)
    _verify_ownership(confession, x_device_token_hash)
    confession.status = ConfessionStatus.deleted


@router.post(
    "/{confession_id}/forward",
    response_model=ConfessionResponse,
    summary="Forward a confession to a recipient department",
)
async def forward_confession(
    confession_id: uuid.UUID,
    body: ConfessionForward,
    x_device_token_hash: str = Header(..., alias="X-Device-Token-Hash"),
    session: AsyncSession = Depends(get_async_session),
) -> Confession:
    """Change a confession's status to ``forwarded`` and assign a department.

    The confession must currently be in ``pending`` status; otherwise the
    request is rejected with a ``409 Conflict``. Requires the
    ``X-Device-Token-Hash`` header to match the confession's owning device.
    """
    confession = await _fetch_confession_or_404(session, confession_id)
    _verify_ownership(confession, x_device_token_hash)

    if confession.status != ConfessionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot forward confession in status '{confession.status.value}'",
        )

    known_departments = parse_comma_separated_list(settings.DEPARTMENTS)
    if body.department not in known_departments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown department '{body.department}'; see GET /api/v1/departments",
        )

    confession.status = ConfessionStatus.forwarded
    confession.recipient_dept = body.department

    await session.flush()
    await session.refresh(confession)
    return confession
