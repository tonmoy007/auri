"""Moderation queue delivery — relays flagged confessions to the moderator's
Telegram chat and lets them approve/reject with inline buttons.

Disabled entirely unless ``BotSettings.moderation_enabled`` is true (both
``moderator_chat_id`` and ``moderation_api_key`` configured).
"""

from __future__ import annotations

import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import BotSettings

logger = logging.getLogger(__name__)

_APPROVE_PREFIX = "modapprove:"
_REJECT_PREFIX = "modreject:"


def _moderation_headers(settings: BotSettings) -> dict[str, str]:
    return {"X-Moderation-Api-Key": settings.moderation_api_key or ""}


def _format_queue_item(item: dict) -> str:
    """Render a flagged confession as a moderator-facing Telegram message."""
    category = item.get("category") or "uncategorized"
    summary = item.get("ai_summary") or "(no summary generated)"
    transcript = item.get("transcript") or ""
    if len(transcript) > 500:
        transcript = transcript[:500] + "…"

    return (
        "🚩 *Flagged confession awaiting review*\n\n"
        f"*Category:* {category}\n"
        f"*Summary:* {summary}\n\n"
        f"*Transcript:*\n{transcript}"
    )


async def poll_moderation_queue(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — fetch the backend moderation queue and notify the moderator.

    Deduplicates against ``context.bot_data["notified_moderation_ids"]`` so a
    flagged item is only posted to the moderator chat once, even though this
    runs on a repeating timer.
    """
    settings: BotSettings = context.bot_data["settings"]
    if not settings.moderation_enabled:
        return
    assert (
        settings.moderator_chat_id is not None
    )  # narrows for mypy; enforced by moderation_enabled

    notified_ids: set[str] = context.bot_data.setdefault(
        "notified_moderation_ids", set()
    )

    try:
        async with httpx.AsyncClient(
            base_url=str(settings.backend_url), timeout=10
        ) as client:
            response = await client.get(
                "/api/v1/moderation/queue", headers=_moderation_headers(settings)
            )
            response.raise_for_status()
            queue = response.json()
    except Exception as exc:
        logger.error("failed to poll moderation queue: %s", exc)
        return

    for item in queue:
        confession_id = item["id"]
        if confession_id in notified_ids:
            continue

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Approve", callback_data=f"{_APPROVE_PREFIX}{confession_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject", callback_data=f"{_REJECT_PREFIX}{confession_id}"
                    ),
                ]
            ]
        )
        try:
            await context.bot.send_message(
                chat_id=settings.moderator_chat_id,
                text=_format_queue_item(item),
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error(
                "failed to notify moderator of confession %s: %s", confession_id, exc
            )
            continue

        notified_ids.add(confession_id)


async def handle_moderation_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle an Approve/Reject button press on a moderation queue message."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    settings: BotSettings = context.bot_data["settings"]

    if query.data.startswith(_APPROVE_PREFIX):
        confession_id = query.data[len(_APPROVE_PREFIX) :]
        action = "approve"
    elif query.data.startswith(_REJECT_PREFIX):
        confession_id = query.data[len(_REJECT_PREFIX) :]
        action = "reject"
    else:
        return

    await query.answer()

    try:
        async with httpx.AsyncClient(
            base_url=str(settings.backend_url), timeout=10
        ) as client:
            response = await client.post(
                f"/api/v1/moderation/{confession_id}/{action}",
                headers=_moderation_headers(settings),
            )
    except Exception as exc:
        logger.error("moderation %s call failed for %s: %s", action, confession_id, exc)
        await query.edit_message_text(
            f"⚠️ Could not reach the backend to {action} this confession. Try again."
        )
        return

    if response.status_code == 404:
        await query.edit_message_text(
            "ℹ️ This confession was already handled (approved/rejected elsewhere)."
        )
        return
    if response.is_error:
        logger.error(
            "moderation %s returned %s for %s: %s",
            action,
            response.status_code,
            confession_id,
            response.text,
        )
        await query.edit_message_text(f"⚠️ Backend rejected the {action} request.")
        return

    verdict = (
        "✅ Approved — returned to the normal delivery flow."
        if action == "approve"
        else "❌ Rejected — deleted."
    )
    await query.edit_message_text(verdict)

    notified_ids: set[str] = context.bot_data.setdefault(
        "notified_moderation_ids", set()
    )
    notified_ids.discard(confession_id)
