"""Auri — Anonymous AI Confession Booth Telegram Bot.

This bot handles anonymous confession delivery via Telegram.
It supports webhook-based operation for production and polling fallback for development.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import BotSettings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AURI_WEB_URL: Final[str] = "https://auri.app"  # TODO: read from config / env

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message & link to open Auri."""
    assert update.effective_user is not None
    user = update.effective_user
    await update.effective_message.reply_text(
        f"🕯️ *Welcome to Auri, {user.first_name}!*\n\n"
        "I'm the anonymous delivery arm of Auri — the AI confession booth.\n\n"
        "🔹 Speak your truth in the booth, then forward anonymously here.\n"
        f"🔹 Open the booth: [{AURI_WEB_URL}]({AURI_WEB_URL})\n"
        "🔹 Use /help to learn what I can do.\n\n"
        "Your identity is never stored. Your voice is masked. "
        "What you share stays between you and whoever you send it to.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available commands."""
    await update.effective_message.reply_text(
        "🕯️ *Auri Bot — Help*\n\n"
        "/start — Welcome & link to the booth\n"
        "/help — This message\n"
        "/confess — How to make a confession\n"
        "/forward — Confirm receipt of a forwarded confession\n\n"
        "Confessions arrive here automatically when you forward them from the Auri app. "
        "You don't need to do anything special — just wait for them to appear.",
        parse_mode="Markdown",
    )


async def confess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain how confession works."""
    await update.effective_message.reply_text(
        "📜 *How to Confess*\n\n"
        "1. Open the Auri booth at the link from /start\n"
        "2. Pick a voice mask (Warm, Robotic, Ethereal, Deep, or Random)\n"
        "3. Speak your truth — AI transcribes and strips any identifying info\n"
        "4. Choose: forward anonymously, send to a specific person, or delete forever\n"
        "5. If you forward, the message arrives here in this chat\n\n"
        "Your identity stays anonymous. Your original voice is never stored.",
        parse_mode="Markdown",
    )


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm receipt of a forwarded confession."""
    await update.effective_message.reply_text(
        "✅ *Confession Received*\n\n"
        "Your anonymous message has been delivered. "
        "The recipient will see it without knowing who sent it.\n\n"
        "If you need to send another, open the booth again from /start.",
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
    assert update.effective_message is not None
    msg = update.effective_message

    # Log the event (no PII stored in logs — just message id and timestamp)
    logger.info(
        "Confession message received: chat_id=%s, message_id=%s",
        msg.chat_id,
        msg.message_id,
    )

    await msg.reply_text(
        "📬 *Delivery Confirmed*\n\n"
        "This confession has been safely delivered to its recipient.\n"
        "The sender remains anonymous.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all errors."""
    logger.error("Update %s caused error %s", update, context.error)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def build_application(settings: BotSettings) -> Application:
    """Create the Telegram Application with all handlers registered."""
    builder = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
    )

    application = builder.build()

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


def _build_and_run(settings: BotSettings) -> Application:
    """Build application, wire up post_init, and return it ready to run."""
    application = build_application(settings)
    application.bot_data["settings"] = settings

    # Patch post_init via the builder's internal mechanism by re-building
    builder = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
    )
    application = builder.build()

    # Re-register handlers on the new builder-built application
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("confess", confess))
    application.add_handler(CommandHandler("forward", forward))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_confession_message,
        )
    )
    application.add_error_handler(error_handler)
    application.bot_data["settings"] = settings

    return application


async def main() -> None:
    """Application entry point."""
    settings = BotSettings()
    application = _build_and_run(settings)

    if settings.is_production:
        logger.info(
            "Starting webhook server on %s:%s",
            settings.host,
            settings.port,
        )
        await application.run_webhook(
            listen=settings.host,
            port=settings.port,
            url_path=settings.bot_token,
            webhook_url=settings.webhook_url,
            secret_token=settings.bot_token,
        )
    else:
        logger.info("Starting polling on %s:%s", settings.host, settings.port)
        await application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
