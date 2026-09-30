"""Public endpoints for the study-library guide ("priest mode").

There is no staff auth here: the device header is the identity, as for confessions
and STT. Nothing in this module reads, stores, logs or echoes a question or a device
code beyond handing them to the limiter and the service, and it never touches the
confessions tables. Failures are logged by exception class and request id only.
"""

from __future__ import annotations

import logging
import uuid
from typing import Final

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.exceptions import PriestError, PriestUnavailableError
from app.priest import metrics, priest_config
from app.priest.priest_service import PriestService, get_priest_service
from app.priest.rate_limiter import (
    PriestRateLimiter,
    PriestRateLimitError,
    get_rate_limiter,
)
from app.priest.schemas import (
    TRADITION_LABELS,
    PriestAnswerResponse,
    PriestAskRequest,
    PriestStatusResponse,
    TraditionId,
    TraditionOption,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/priest", tags=["priest"])

STATUS_CACHE_CONTROL: Final = "max-age=60"
BUSY_RETRY_AFTER_SECONDS: Final = 2
UNEXPECTED_ERROR_DETAIL: Final = "priest_unexpected_error"


def _require_enabled() -> None:
    """Refuse every question while the kill switch is off.

    A dependency rather than a line in the handler: dependencies resolve in
    declaration order and this one is declared first, so a disabled guide never
    builds the service (which may load the index) and never counts against a device.
    """
    if not priest_config.enabled():
        metrics.record_outcome("disabled")
        raise PriestUnavailableError("priest_mode_disabled")


@router.get("/status", response_model=PriestStatusResponse)
async def priest_status(response: Response) -> PriestStatusResponse:
    """Whether the guide is on and what the app needs to draw its entry point.

    Needs no identity and touches neither the index nor a model.
    """
    allowed = priest_config.enabled_traditions()
    response.headers["Cache-Control"] = STATUS_CACHE_CONTROL
    return PriestStatusResponse(
        enabled=priest_config.enabled(),
        persona_name=priest_config.persona_name(),
        traditions=[
            TraditionOption(id=t.value, label=TRADITION_LABELS[t.value])
            for t in TraditionId
            if allowed is None or t.value in allowed
        ],
    )


@router.post(
    "/ask",
    response_model=PriestAnswerResponse,
    dependencies=[Depends(_require_enabled)],
)
async def ask_priest(
    body: PriestAskRequest,
    x_device_token_hash: str = Header(
        ..., alias="X-Device-Token-Hash", min_length=16, max_length=256
    ),
    limiter: PriestRateLimiter = Depends(get_rate_limiter),
    service: PriestService = Depends(get_priest_service),
) -> PriestAnswerResponse:
    """Answer a question from the study library, or refuse with a fixed reason.

    Raises:
        PriestRateLimitError: The device asked too often (429).
        PriestUnavailableError: The guide is busy or its index is unavailable (503).
        HTTPException: An unexpected failure, as a fixed 500.
    """
    # Random on purpose: not derived from the device, the question or the time.
    request_id = str(uuid.uuid4())
    _check_rate_limit(limiter, x_device_token_hash)
    try:
        return await service.answer(
            body.question, body.tradition, request_id=request_id
        )
    except PriestUnavailableError as exc:
        metrics.record_outcome("busy" if exc.code == "priest_busy" else "error")
        _log_failure(exc, request_id)
        raise _unavailable(exc) from None
    except PriestError as exc:
        metrics.record_outcome("error")
        _log_failure(exc, request_id)
        raise PriestUnavailableError("priest_index_unavailable") from None
    except Exception as exc:  # noqa: BLE001 — the route's last line: whatever the service raised may quote the question, so the client gets a fixed 500 and the log gets the class name only
        metrics.record_outcome("error")
        _log_failure(exc, request_id)
        raise HTTPException(status_code=500, detail=UNEXPECTED_ERROR_DETAIL) from None


def _check_rate_limit(limiter: PriestRateLimiter, device_token_hash: str) -> None:
    """Count the question against the device, recording a refusal in the metrics."""
    try:
        limiter.check_and_record(device_token_hash)
    except PriestRateLimitError:
        metrics.record_outcome("rate_limited")
        raise


def _unavailable(exc: PriestUnavailableError) -> PriestUnavailableError:
    """A clean copy of *exc*; a busy guide always says when to retry."""
    retry_after = exc.retry_after
    if exc.code == "priest_busy" and retry_after is None:
        retry_after = BUSY_RETRY_AFTER_SECONDS
    return PriestUnavailableError(exc.code, retry_after)


def _log_failure(exc: Exception, request_id: str) -> None:
    """Log that a question failed: the class and request id, never the message."""
    logger.error("priest question failed: %s (%s)", type(exc).__name__, request_id)
