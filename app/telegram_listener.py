"""
Telegram Bot Polling Listener.

Telegram protocol adapter only:
- Receive messages and photos
- Resolve tenant from chat_id
- Delegate to RequestHandler
- Send the response back

No business logic here.
"""

import asyncio
import logging
import threading
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.database import get_registry_db
from app.services.tenant_service import TenantService
from app.handlers.request_handler import RequestHandler
from app.services.admin_notifier import AdminNotifier
from app.error_handler import ErrorHandler, format_error_for_telegram
from app.config import settings
from app.services.backup_service import create_backup_service
from app.instagram_listener import InstagramListener

logger = logging.getLogger(__name__)

# How long to wait for the agent before giving up (seconds)
REQUEST_TIMEOUT = 90


class TelegramBotListener:
    """Telegram adapter. Owns nothing except the Telegram connection."""

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be configured")

        self.instagram = InstagramListener(notify_owner_fn=self._send_to_chat)
        self.admin_notifier = AdminNotifier(
            admin_chat_id=settings.ADMIN_CHAT_ID or "",
            # send_fn wired after application starts
        )
        self.handler = RequestHandler(
            admin_notifier=self.admin_notifier,
            instagram_listener=self.instagram,
        )
        self.application = None
        self.backup = create_backup_service()

    def _start_webhook_server(self):
        """Start the FastAPI webhook server in a background thread."""
        from app.webhook_server import app as webhook_app, register_instagram
        register_instagram(self.instagram)

        import uvicorn

        def run():
            uvicorn.run(webhook_app, host="0.0.0.0", port=8000, log_level="warning")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        logger.info("Webhook server started on port 8000")

    async def _send_to_chat(self, chat_id: str, message: str):
        """Send a message to a specific chat_id (used by Instagram listener)."""
        try:
            await self.application.bot.send_message(
                chat_id=int(chat_id),
                text=message,
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await self.application.bot.send_message(chat_id=int(chat_id), text=message)
            except Exception as e:
                logger.error(f"Failed to send notification to {chat_id}: {e}")

    async def _send(self, update: Update, text: str):
        """Send with Markdown, fall back to plain text."""
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)

    async def _edit(self, message, text: str):
        """Edit an existing message with Markdown, fall back to plain text."""
        try:
            await message.edit_text(text, parse_mode="Markdown")
        except Exception:
            try:
                await message.edit_text(text)
            except Exception:
                pass  # message may have been deleted or is unchanged

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive text → show thinking indicator → resolve tenant → delegate → reply."""
        try:
            if not update.message or not update.message.text:
                return

            chat_id = str(update.message.chat_id)
            text = update.message.text.strip()
            logger.info(f"Text from {chat_id}: {text}")

            # Show "Thinking..." immediately so the user knows we're working
            thinking_msg = await update.message.reply_text("💭 Thinking...")

            # Step 1: resolve tenant from the shared registry
            registry_db = next(get_registry_db())
            try:
                tenant = TenantService(registry_db).get_or_create_tenant(chat_id)
                tenant_id = tenant.tenant_id
            finally:
                registry_db.close()

            # Step 2: process with timeout — handler opens its own DB session
            try:
                response = await asyncio.wait_for(
                    self.handler.handle_text(tenant_id, chat_id, text),
                    timeout=REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Request timed out for chat_id={chat_id}")
                response = (
                    "⏱ That took too long to process. Please try again.\n"
                    "If this keeps happening, try breaking your request into smaller steps."
                )

            # File response (e.g. invoice PDF)
            if isinstance(response, tuple):
                pdf_bytes, filename = response
                await update.message.reply_document(
                    document=pdf_bytes,
                    filename=filename,
                    caption="📄 Here's your invoice!",
                )
            else:
                await self._edit(thinking_msg, response)

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            try:
                chat_id = str(update.message.chat_id) if update.message else "unknown"
                text = update.message.text.strip() if update.message and update.message.text else ""
                error_msg = await self.handler.handle_error(chat_id, text, e)
                await update.message.reply_text(error_msg)
            except Exception:
                pass

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive photo → download bytes → delegate → reply."""
        try:
            if not update.message or not update.message.photo:
                return

            chat_id = str(update.message.chat_id)
            caption = (update.message.caption or "").strip()
            logger.info(f"Photo from {chat_id}, caption: '{caption}'")

            # Download highest-quality photo
            photo_bytes = bytes(
                await (await update.message.photo[-1].get_file()).download_as_bytearray()
            )

            # Step 1: resolve tenant
            registry_db = next(get_registry_db())
            try:
                tenant = TenantService(registry_db).get_or_create_tenant(chat_id)
                tenant_id = tenant.tenant_id
            finally:
                registry_db.close()

            # Step 2: process image with timeout — handler opens its own DB session
            thinking_msg = await update.message.reply_text("🔍 Processing image...")

            try:
                response = await asyncio.wait_for(
                    self.handler.handle_image(tenant_id, chat_id, photo_bytes, caption),
                    timeout=REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Image processing timed out for chat_id={chat_id}")
                response = (
                    "⏱ Image processing took too long. Please try again.\n"
                    "Make sure the image is clear and well-lit."
                )

            if response is None:
                await self._edit(thinking_msg, (
                    "📸 I received your image!\n\n"
                    "Please add a caption to tell me what it is:\n"
                    "• *recipe* — handwritten or printed recipe\n"
                    "• *receipt* — payment receipt or bill\n"
                    "• *order* — WhatsApp/SMS order screenshot"
                ))
            else:
                await self._edit(thinking_msg, response)

        except Exception as e:
            logger.error(f"Error handling photo: {e}", exc_info=True)
            try:
                chat_id = str(update.message.chat_id) if update.message else "unknown"
                caption = update.message.caption or "" if update.message else ""
                error_msg = await self.handler.handle_error(chat_id, f"[photo] {caption}", e)
                await update.message.reply_text(error_msg)
            except Exception:
                pass

    async def start(self):
        logger.info("Starting Telegram bot in polling mode...")
        self.application = Application.builder().token(self.bot_token).build()

        # Wire send function now that application exists
        self.instagram.notify_owner = self._send_to_chat
        self.admin_notifier.set_send_fn(self._send_to_chat)

        # Start webhook server in background thread (for Instagram/WhatsApp webhooks)
        self._start_webhook_server()

        self.application.add_handler(
            MessageHandler(filters.TEXT, self.handle_message)
        )
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self.handle_photo)
        )
        async with self.application:
            await self.application.initialize()
            await self.application.start()
            logger.info("Bot is now listening for messages...")
            if self.backup:
                self.backup.start()
            await self.application.updater.start_polling()
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Stopping bot...")
            finally:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

    async def stop(self):
        if self.backup:
            await self.backup.stop()
        await self.handler.close()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    listener = TelegramBotListener()
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
