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
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.database import get_db
from app.services.tenant_service import TenantService
from app.handlers.request_handler import RequestHandler
from app.error_handler import ErrorHandler, format_error_for_telegram
from app.config import settings
from app.services.backup_service import create_backup_service

logger = logging.getLogger(__name__)


class TelegramBotListener:
    """Telegram adapter. Owns nothing except the Telegram connection."""

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be configured")

        self.handler = RequestHandler()
        self.application = None
        self.backup = create_backup_service()

    async def _send(self, update: Update, text: str):
        """Send with Markdown, fall back to plain text."""
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive text → resolve tenant → delegate → reply."""
        try:
            if not update.message or not update.message.text:
                return

            chat_id = str(update.message.chat_id)
            text = update.message.text.strip()
            logger.info(f"Text from {chat_id}: {text}")

            db = next(get_db())
            try:
                tenant = TenantService(db).get_or_create_tenant(chat_id)
                response = await self.handler.handle_text(db, tenant.tenant_id, chat_id, text)
                await self._send(update, response)
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    format_error_for_telegram(ErrorHandler.handle_exception(e))
                )
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

            db = next(get_db())
            try:
                tenant = TenantService(db).get_or_create_tenant(chat_id)

                await update.message.reply_text("🔍 Processing image...")

                response = await self.handler.handle_image(
                    db, tenant.tenant_id, chat_id, photo_bytes, caption
                )

                if response is None:
                    # No caption - ask user to specify type
                    await self._send(update, (
                        "📸 I received your image!\n\n"
                        "Please add a caption to tell me what it is:\n"
                        "• *recipe* — handwritten or printed recipe\n"
                        "• *receipt* — payment receipt or bill\n"
                        "• *order* — WhatsApp/SMS order screenshot"
                    ))
                else:
                    await self._send(update, response)
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error handling photo: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    format_error_for_telegram(ErrorHandler.handle_exception(e))
                )
            except Exception:
                pass

    async def start(self):
        logger.info("Starting Telegram bot in polling mode...")
        self.application = Application.builder().token(self.bot_token).build()
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
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
