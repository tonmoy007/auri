"""Confession delivery — relays forwarded confessions to each recipient
department's Telegram chat and marks them delivered on the backend.

Disabled entirely unless ``BotSettings.delivery_enabled`` is true
(``delivery_api_key`` configured).
"""

from __future__ import annotations

import logging

import httpx
from telegram.ext import ContextTypes

from bot.config import BotSettings

logger = logging.getLogger(__name__)


def _delivery_headers(settings: BotSettings) -> dict[str, str]:
    return {"X-Delivery-Api-Key": settings.delivery_api_key or ""}


def _format_delivery_message(item: dict) -> str:
    """Render a forwarded confession as a recipient-facing Telegram message.

    Only de-identified fields are ever included — no device token, no raw
    audio, nothing that could re-identify the sender.
    """
    category = item.get("category") or "uncategorized"
    summary = item.get("ai_summary") or "(no summary generated)"
    transcript = item.get("transcript") or ""
    if len(transcript) > 1000:
        transcript = transcript[:1000] + "…"

    return (
        "🕯️ *A teammate shared an anonymous confession*\n\n"
        f"*Category:* {category}\n"
        f"*Summary:* {summary}\n\n"
        f"*Transcript:*\n{transcript}\n\n"
        "_The sender's identity is never stored or shared._"
    )


async def poll_delivery_queue(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — fetch the backend delivery queue and deliver each item.

    Deduplicates against ``context.bot_data["delivered_ids"]`` as an extra
    guard against a fast repeat poll racing the backend's own
    ``delivered_at`` commit, mirroring ``poll_moderation_queue``'s pattern —
    the backend's ``delivered_at`` column remains the source of truth; this
    set only narrows the race window within this process.
    """
    settings: BotSettings = context.bot_data["settings"]
    if not settings.delivery_enabled:
        return

    chat_ids = settings.department_chat_id_map()
    delivered_ids: set[str] = context.bot_data.setdefault("delivered_ids", set())

    try:
        async with httpx.AsyncClient(
            base_url=str(settings.backend_url), timeout=10
        ) as client:
            response = await client.get(
                "/api/v1/delivery/queue", headers=_delivery_headers(settings)
            )
            response.raise_for_status()
            queue = response.json()
    except Exception as exc:
        logger.error("failed to poll delivery queue: %s", exc)
        return

    for item in queue:
        confession_id = item["id"]
        if confession_id in delivered_ids:
            continue

        department = item.get("recipient_dept")
        chat_id = chat_ids.get(department) if department else None
        if chat_id is None:
            logger.error(
                "no Telegram chat configured for department %r "
                "(confession %s) — leaving undelivered, will retry",
                department,
                confession_id,
            )
            continue

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_format_delivery_message(item),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error(
                "failed to deliver confession %s to department %r: %s",
                confession_id,
                department,
                exc,
            )
            continue

        try:
            async with httpx.AsyncClient(
                base_url=str(settings.backend_url), timeout=10
            ) as client:
                mark_response = await client.post(
                    f"/api/v1/delivery/{confession_id}/delivered",
                    headers=_delivery_headers(settings),
                )
                mark_response.raise_for_status()
        except Exception as exc:
            logger.error(
                "delivered confession %s to Telegram but failed to mark it "
                "delivered on the backend — it will be re-sent next poll: %s",
                confession_id,
                exc,
            )
            continue

        delivered_ids.add(confession_id)
