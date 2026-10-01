"""Auri — Anonymous AI Confession Booth Telegram Bot.

This bot handles anonymous confession delivery via Telegram.
It supports webhook-based operation for production and polling fallback for development.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import BotSettings
from bot.delivery_handlers import poll_delivery_queue
from bot.moderation_handlers import handle_moderation_callback, poll_moderation_queue

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message & link to open Auri."""
    if update.effective_user is None:
        logger.debug("start: update has no effective_user, ignoring")
        return
    msg = update.effective_message
    if msg is None:
        logger.debug("start: update has no effective_message, ignoring")
        return

    settings: BotSettings = context.bot_data["settings"]
    user = update.effective_user
    await msg.reply_text(
        f"🕯️ *Welcome to Auri, {user.first_name}!*\n\n"
        "I'm the anonymous delivery arm of Auri — the AI confession booth.\n\n"
        "🔹 Speak your truth in the booth, then forward it to a department.\n"
        f"🔹 Open the booth: [{settings.web_url}]({settings.web_url})\n"
        "🔹 Use /help to learn what I can do.\n\n"
        "You are never asked for your name, and your voice is masked. "
        "The people who handle a confession can read what is forwarded, and "
        "what you say can still point to you, so share only what you are "
        "comfortable with them reading.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available commands."""
    msg = update.effective_message
    if msg is None:
        logger.debug("help: update has no effective_message, ignoring")
        return
    await msg.reply_text(
        "🕯️ *Auri Bot — Help*\n\n"
        "/start — Welcome & link to the booth\n"
        "/help — This message\n"
        "/confess — How to make a confession\n"
        "/forward — How forwarding works\n\n"
        "Confessions are forwarded from the Auri booth, not from this chat. When a "
        "department is chosen, the confession is posted to that department's chat.",
        parse_mode="Markdown",
    )


async def confess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain how confession works."""
    msg = update.effective_message
    if msg is None:
        logger.debug("confess: update has no effective_message, ignoring")
        return
    await msg.reply_text(
        "📜 *How to Confess*\n\n"
        "1. Open the Auri booth at the link from /start\n"
        "2. Pick a voice mask (Warm, Robotic, Ethereal, Deep, or Random)\n"
        "3. Speak your truth — AI transcribes it and replaces details it recognises "
        "(it can miss some)\n"
        "4. Choose: forward to a department, or delete it\n"
        "5. If you forward, the department's chat receives a summary and up to the "
        "first 1,000 characters of the transcript\n\n"
        "You are never asked for your name. A code for your phone is kept so you "
        "can see your own history, and what you say can still point to you, so "
        "leave out anything you would not want traced back.",
        parse_mode="Markdown",
    )


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm receipt of a forwarded confession."""
    msg = update.effective_message
    if msg is None:
        logger.debug("forward: update has no effective_message, ignoring")
        return
    await msg.reply_text(
        "📬 *Nothing was sent from this chat*\n\n"
        "Confessions are forwarded from the booth, where you choose a department; "
        "when one is delivered it is posted to that department's chat.\n\n"
        "To forward one, open the booth from /start.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Message handler (anonymous delivery)
# ---------------------------------------------------------------------------


async def handle_confession_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle messages forwarded from the Auri backend.

    These arrive as plain text or media captions containing the confession.
    We acknowledge receipt and log delivery for observability.
    """
    if update.effective_message is None:
        logger.debug(
            "handle_confession_message: update has no effective_message, ignoring"
        )
        return

    msg = update.effective_message

    # Log the event (no PII stored in logs — just message id and timestamp)
    logger.info(
        "Confession message received: chat_id=%s, message_id=%s",
        msg.chat_id,
        msg.message_id,
    )

    await msg.reply_text(
        "📬 *Message received*\n\n"
        "This chat is not the booth, so nothing was forwarded. "
        "To confess, open the booth from /start.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors without leaking message content.

    Never log the raw ``update`` object — ``Update.__str__`` includes the
    full message text (found in the 2026-07-20 privacy review: this would
    have dumped confession content, potentially still carrying PII, to
    application logs at ERROR level on any processing failure). Log only
    the non-sensitive ``update_id``.
    """
    update_id = getattr(update, "update_id", None)
    logger.error("Error handling update_id=%s: %s", update_id, context.error)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def build_application(settings: BotSettings) -> Application:
    """Create the Telegram Application with all handlers registered."""
    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # --- Register command handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("confess", confess))
    application.add_handler(CommandHandler("forward", forward))

    # --- Register message handler for incoming confessions ---
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_confession_message,
        )
    )

    # --- Register moderation queue Approve/Reject buttons ---
    application.add_handler(
        CallbackQueryHandler(
            handle_moderation_callback, pattern=r"^mod(approve|reject):"
        )
    )

    # --- Register error handler ---
    application.add_error_handler(error_handler)

    return application


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def post_init(application: Application) -> None:
    """Set webhook after application starts (production) or log (dev)."""
    bot_settings: BotSettings | None = application.bot_data.get("settings")
    if bot_settings is None:
        return

    if bot_settings.is_production:
        await application.bot.set_webhook(
            url=bot_settings.webhook_url,
            drop_pending_updates=True,
        )
        logger.info("Webhook set to %s", bot_settings.webhook_url)
    else:
        logger.info("Running in polling mode (development)")

    if bot_settings.moderation_enabled and application.job_queue is not None:
        application.job_queue.run_repeating(
            poll_moderation_queue,
            interval=bot_settings.moderation_poll_seconds,
            first=10,
            name="poll_moderation_queue",
        )
        logger.info(
            "Moderation queue polling enabled (every %ss)",
            bot_settings.moderation_poll_seconds,
        )
    else:
        logger.info("Moderation queue polling disabled (not configured)")

    if bot_settings.delivery_enabled and application.job_queue is not None:
        application.job_queue.run_repeating(
            poll_delivery_queue,
            interval=bot_settings.delivery_poll_seconds,
            first=15,
            name="poll_delivery_queue",
        )
        logger.info(
            "Delivery queue polling enabled (every %ss)",
            bot_settings.delivery_poll_seconds,
        )
    else:
        logger.info("Delivery queue polling disabled (not configured)")


def main() -> None:
    """Application entry point.

    ``run_webhook``/``run_polling`` are blocking calls that own the event
    loop themselves — they must not be awaited or wrapped in ``asyncio.run``.
    """
    settings = BotSettings()
    application = build_application(settings)
    application.bot_data["settings"] = settings

    if settings.is_production:
        logger.info(
            "Starting webhook server on %s:%s",
            settings.host,
            settings.port,
        )
        application.run_webhook(
            listen=settings.host,
            port=settings.port,
            url_path="telegram-webhook",
            webhook_url=settings.webhook_url,
            secret_token=settings.webhook_secret,
        )
    else:
        logger.info("Starting polling on %s:%s", settings.host, settings.port)
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
